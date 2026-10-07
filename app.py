import os
import json
from datetime import datetime
from flask import Flask, render_template, request, Response, redirect, url_for, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from groq import Groq

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "oralis_secret_key_change_in_production")

db_url = os.getenv("DATABASE_URL", "sqlite:///oralis.db")

# Render genera URLs que empiezan por 'postgres://' o 'postgresql://'
# Esta transformación asegura que SQLAlchemy use el conector 'psycopg2' instalado
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql+psycopg2://", 1)
elif db_url.startswith("postgresql://") and not db_url.startswith("postgresql+psycopg2://"):
    db_url = db_url.replace("postgresql://", "postgresql+psycopg2://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login_page'

# Cliente Groq
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None


# -------------------------------------------------------------------
# MODELOS DE BASE DE DATOS
# -------------------------------------------------------------------

class User(UserMixin, db.Model):
    __tablename__ = 'users'  # Evita conflictos con la palabra reservada 'user' en PostgreSQL
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    notebook_items = db.relationship('NotebookItem', backref='user', lazy=True, cascade="all, delete-orphan")
    feedbacks = db.relationship('Feedback', backref='user', lazy=True, cascade="all, delete-orphan")


class NotebookItem(db.Model):
    __tablename__ = 'notebook_items'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    categoria = db.Column(db.String(50), nullable=False)
    idioma = db.Column(db.String(50), nullable=False)
    nivel = db.Column(db.String(10), nullable=False)
    termino = db.Column(db.String(200), nullable=False)
    explicacion = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Feedback(db.Model):
    __tablename__ = 'feedbacks'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    user_name = db.Column(db.String(100), default="Anónimo")
    score = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# Inicializar tablas de la BD
with app.app_context():
    db.create_all()


# -------------------------------------------------------------------
# LÓGICA DE MODELOS GROQ Y PROMPT DEL SISTEMA
# -------------------------------------------------------------------

def get_active_groq_models():
    """Obtiene dinámicamente modelos válidos para chat o usa fallback."""
    if not client:
        return ["llama3-8b-8192", "llama3-70b-8192"]

    modelos_candidatos = []
    try:
        modelos_data = client.models.list().data
        for m in modelos_data:
            m_id = str(m.id).lower() if hasattr(m, 'id') else str(m).lower()
            if not any(x in m_id for x in ["whisper", "guard", "vision"]):
                modelos_candidatos.append(m.id if hasattr(m, 'id') else str(m))
    except Exception:
        pass

    return modelos_candidatos if modelos_candidatos else ["llama3-8b-8192", "llama3-70b-8192"]


def get_system_prompt(target_lang, cefr_level, mode, rol_practica, examen_oficial, rubrica):
    """Construye el Prompt del Sistema completo adaptado a la configuración actual del usuario."""
    prompt = f"""Eres Oralis, un tutor virtual experto e interactivo en la enseñanza de idiomas.

Configuración del estudiante:
- Idioma Objetivo: {target_lang}
- Nivel CEFR: {cefr_level}
- Modo de Trabajo: {mode}
"""

    if mode == "practica_oral":
        rol = rol_practica.strip() if rol_practica else "Hablante nativo en una conversación informal"
        prompt += f"- Rol asignado para la simulación: {rol}\n"
        prompt += "Debes mantener la conversación adoptando completamente ese rol, incentivando al usuario a responder en el idioma objetivo.\n"

    elif mode == "examenes":
        examen = examen_oficial.strip() if examen_oficial else "Examen Oficial General"
        prompt += f"- Examen / Prueba Específica: {examen}\n"
        if rubrica and rubrica.strip():
            prompt += f"- Criterios y Rúbrica de Evaluación: {rubrica.strip()}\n"
        prompt += "Modela tus preguntas, ejercicios y correcciones según las exigencias y el formato real de dicho examen.\n"

    elif mode == "writing":
        prompt += "El usuario practicará la redacción escrita. Evalúa la corrección gramatical, riqueza de vocabulario, coherencia y adecuación formal.\n"

    prompt += """
ESTRUCTURA OBLIGATORIA DE TUS RESPUESTAS:
Para mantener el formato limpio y estructurado en la interfaz web, organiza SIEMPRE tus respuestas utilizando exactamente las siguientes tres etiquetas separadoras:

[RESPUESTA_PRINCIPAL]
Escribe aquí tu respuesta, explicación, pregunta o intervención principal en el idioma objetivo.

[TRADUCCION_INTEGRADA]
Si el nivel es A1, A2 o B1, incluye aquí la traducción o aclaración en español de tu respuesta principal. Si el nivel es B2, C1 o C2, puedes dejar esta sección vacía o incluir notas aclaratorias breves.

[CORRECCION_Y_MEJORA]
Analiza el último mensaje escrito por el usuario. Si cometió errores gramaticales, ortográficos o de vocabulario, corrígelos aquí de forma constructiva e indica cómo expresarlo de forma más natural. Si no hubo errores, indica brevemente que su mensaje fue correcto.
"""
    return prompt


# -------------------------------------------------------------------
# RUTAS DE NAVEGACIÓN Y AUTENTICACIÓN
# -------------------------------------------------------------------

@app.route("/")
def index():
    if not current_user.is_authenticated:
        return redirect(url_for("login_page"))
    if "chat_history" not in session:
        session["chat_history"] = []
    return render_template("index.html", user_name=current_user.nombre, is_admin=current_user.is_admin)


@app.route("/login", methods=["GET"])
def login_page():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    return render_template("login.html")


@app.route("/login", methods=["POST"])
def login_api():
    data = request.get_json() or {}
    action = data.get("action")
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")

    if action == "register":
        nombre = data.get("nombre", "").strip()
        if not nombre or not email or not password:
            return jsonify({"status": "error", "message": "Por favor completa todos los campos."}), 400

        user_exists = User.query.filter_by(email=email).first()
        if user_exists:
            return jsonify({"status": "error", "message": "El correo ya está registrado."}), 400

        # El primer usuario registrado se puede convertir opcionalmente en admin
        is_first_user = User.query.count() == 0
        hashed_pw = bcrypt.generate_password_hash(password).decode("utf-8")
        new_user = User(nombre=nombre, email=email, password_hash=hashed_pw, is_admin=is_first_user)

        db.session.add(new_user)
        db.session.commit()
        login_user(new_user)
        session["chat_history"] = []
        return jsonify({"status": "ok", "message": "Cuenta creada con éxito."})

    else:  # 'login'
        user = User.query.filter_by(email=email).first()
        if user and bcrypt.check_password_hash(user.password_hash, password):
            login_user(user)
            session["chat_history"] = []
            return jsonify({"status": "ok", "message": "Sesión iniciada."})

        return jsonify({"status": "error", "message": "Credenciales incorrectas."}), 401


@app.route("/logout")
@login_required
def logout():
    logout_user()
    session.clear()
    return redirect(url_for("login_page"))


@app.route("/clear_chat", methods=["POST"])
@login_required
def clear_chat():
    session["chat_history"] = []
    session.modified = True
    return jsonify({"status": "ok", "message": "Historial reiniciado"})


# -------------------------------------------------------------------
# CHAT Y STREAMING CON GROQ (CON FALLBACK)
# -------------------------------------------------------------------

@app.route("/chat_stream", methods=["POST"])
@login_required
def chat_stream():
    data = request.get_json() or {}

    user_message = data.get("message", "").strip()
    target_lang = data.get("target_lang", "Inglés")
    cefr_level = data.get("cefr_level", "B1")
    mode = data.get("mode", "tutor_general")
    rol_practica = data.get("rol_practica", "")
    examen_oficial = data.get("examen_oficial", "")
    rubrica = data.get("rubrica", "")
    file_name = data.get("file_name", "")
    file_content = data.get("file_content", "")

    system_prompt = get_system_prompt(target_lang, cefr_level, mode, rol_practica, examen_oficial, rubrica)

    full_user_text = user_message
    if file_content:
        full_user_text += f"\n\n--- ARCHIVO ADJUNTO ({file_name}) ---\n{file_content}\n--- FIN ARCHIVO ---"

    history = session.get("chat_history", [])

    messages = [{"role": "system", "content": system_prompt}]
    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": full_user_text})

    def generate():
        if not client:
            yield "[RESPUESTA_PRINCIPAL] Error: No se ha configurado la variable GROQ_API_KEY en el servidor."
            return

        modelos_disponibles = get_active_groq_models()
        full_response_text = ""
        success = False

        for model_candidate in modelos_disponibles:
            try:
                response = client.chat.completions.create(
                    model=model_candidate,
                    messages=messages,
                    stream=True,
                    temperature=0.7
                )
                for chunk in response:
                    if chunk.choices and chunk.choices[0].delta.content:
                        content = chunk.choices[0].delta.content
                        full_response_text += content
                        yield content

                success = True
                break
            except Exception:
                continue

        if success:
            history.append({"role": "user", "content": full_user_text})
            history.append({"role": "assistant", "content": full_response_text})
            session["chat_history"] = history[-20:]
            session.modified = True
        else:
            yield "[RESPUESTA_PRINCIPAL] No se pudo conectar con los servidores de IA de Groq en este momento."

    return Response(generate(), mimetype="text/plain; charset=utf-8")


# -------------------------------------------------------------------
# MI CUADERNO (PERSISTENCIA BD)
# -------------------------------------------------------------------

@app.route("/cuaderno/listar", methods=["GET"])
@login_required
def listar_cuaderno():
    items = NotebookItem.query.filter_by(user_id=current_user.id).order_by(NotebookItem.created_at.desc()).all()
    resultado = [{
        "id": item.id,
        "categoria": item.categoria,
        "idioma": item.idioma,
        "nivel": item.nivel,
        "termino": item.termino,
        "explicacion": item.explicacion
    } for item in items]

    return jsonify(resultado)


@app.route("/cuaderno/guardar", methods=["POST"])
@login_required
def guardar_cuaderno():
    data = request.get_json() or {}

    categoria = data.get("categoria", "Vocabulario")
    idioma = data.get("idioma", "Inglés")
    nivel = data.get("nivel", "B1")
    termino = data.get("termino", "").strip()
    explicacion = data.get("explicacion", "").strip()

    if not termino or not explicacion:
        return jsonify({"status": "error", "message": "Datos incompletos"}), 400

    item = NotebookItem(
        user_id=current_user.id,
        categoria=categoria,
        idioma=idioma,
        nivel=nivel,
        termino=termino,
        explicacion=explicacion
    )
    db.session.add(item)
    db.session.commit()

    return jsonify({"status": "ok", "message": "Anotación guardada en Mi Cuaderno"})


# -------------------------------------------------------------------
# HISTORIAL DE CHATS
# -------------------------------------------------------------------

@app.route("/historial/listar", methods=["GET"])
@login_required
def listar_historial():
    history = session.get("chat_history", [])
    resumido = []

    for i in range(0, len(history), 2):
        if i + 1 < len(history):
            user_msg = history[i]["content"]
            resumido.append({
                "titulo": user_msg[:40] + ("..." if len(user_msg) > 40 else ""),
                "modo": "Sesión Activa",
                "fecha": datetime.now().strftime("%H:%M - %d/%m/%Y")
            })

    return jsonify(resumido)


# -------------------------------------------------------------------
# FEEDBACK Y ADMINISTRACIÓN
# -------------------------------------------------------------------

@app.route("/feedback", methods=["POST"])
def guardar_feedback():
    data = request.get_json() or {}
    score = data.get("score", 5)
    comment = data.get("comment", "").strip()

    u_id = current_user.id if current_user.is_authenticated else None
    u_name = current_user.nombre if current_user.is_authenticated else "Anónimo"

    fb = Feedback(
        user_id=u_id,
        user_name=u_name,
        score=score,
        comment=comment
    )
    db.session.add(fb)
    db.session.commit()

    return jsonify({"status": "ok", "message": "Feedback registrado"})


@app.route("/admin/feedback", methods=["GET"])
@login_required
def admin_feedback():
    if not current_user.is_admin:
        return redirect(url_for("index"))

    try:
        feedbacks = Feedback.query.order_by(Feedback.created_at.desc()).all()
        feedbacks_data = [{
            "user": fb.user_name or "Anónimo",
            "score": fb.score,
            "comment": fb.comment,
            "date": fb.created_at.strftime("%d/%m/%Y %H:%M") if fb.created_at else "N/A"
        } for fb in feedbacks]
    except Exception as e:
        print(f"Error al obtener feedbacks: {e}")
        feedbacks_data = []

    return render_template("admin_feedback.html", feedbacks=feedbacks_data)


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
