import os
import json
from datetime import timedelta
from flask import Flask, render_template, request, jsonify, Response, stream_with_context, session, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from groq import Groq

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "oralis_secret_key_2026_persistente")

# --- PERSISTENCIA DE SESIÓN ---
# Mantiene la sesión abierta durante 30 días para no perder el login al recargar
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)

# --- CONFIGURACIÓN BASE DE DATOS Y CLIENTE API ---
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///oralis.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

GROQ_KEY = os.environ.get("GROQ_API_KEY", "TU_API_KEY_AQUI")
groq_client = Groq(api_key=GROQ_KEY)

# --- MODELOS DE BASE DE DATOS ---
class Usuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(80), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)

class Cuaderno(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    usuario_email = db.Column(db.String(120), nullable=False)
    idioma = db.Column(db.String(50), nullable=False)
    nivel = db.Column(db.String(10), nullable=False)
    categoria = db.Column(db.String(50), nullable=False)
    termino = db.Column(db.Text, nullable=False)
    explicacion = db.Column(db.Text, nullable=False)
    fecha = db.Column(db.DateTime, default=db.func.current_timestamp())

class HistorialChat(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    usuario_email = db.Column(db.String(120), nullable=False)
    titulo = db.Column(db.String(150), default="Conversación")
    modo = db.Column(db.String(50), nullable=False)
    idioma = db.Column(db.String(50), nullable=False)
    contenido_json = db.Column(db.Text, nullable=False)
    fecha = db.Column(db.DateTime, default=db.func.current_timestamp())

class Feedback(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    usuario_email = db.Column(db.String(120), nullable=False)
    puntuacion = db.Column(db.Integer, nullable=False)
    comentario = db.Column(db.Text, nullable=False)
    fecha_creacion = db.Column(db.DateTime, default=db.func.current_timestamp())

# Creación de tablas de forma segura
with app.app_context():
    db.create_all()

# --- RUTAS DE NAVEGACIÓN Y AUTENTICACIÓN ---
@app.route('/')
def home():
    if 'user_email' not in session:
        return redirect(url_for('login_page'))
    return render_template('index.html', user_email=session['user_email'], user_name=session.get('user_name', 'Estudiante'))

@app.route('/login_page')
def login_page():
    if 'user_email' in session:
        return redirect(url_for('home'))
    return render_template('login.html')

@app.route('/login', methods=['POST'])
def login():
    data = request.get_json() or {}
    action = data.get('action', 'login')  # 'login' o 'register'
    email = data.get('email', '').strip().lower()
    password = data.get('password', '').strip()

    if not email or not password:
        return jsonify({'status': 'error', 'message': 'Debes completar todos los campos.'}), 400

    # 1. Verificar si el usuario ya existe en la base de datos
    usuario_existente = Usuario.query.filter_by(email=email).first()

    if usuario_existente:
        # Si el usuario ya existe, validamos la contraseña e iniciamos sesión
        if check_password_hash(usuario_existente.password_hash, password):
            session.permanent = True
            session['user_email'] = usuario_existente.email
            session['user_name'] = usuario_existente.nombre
            return jsonify({'status': 'ok', 'message': 'Inicio de sesión exitoso'})
        else:
            return jsonify({'status': 'error', 'message': 'El correo ya está registrado y la contraseña es incorrecta.'}), 401

    # 2. Si NO existe y la acción es registrar, creamos el usuario
    if action == 'register':
        nombre = data.get('nombre', '').strip()
        if not nombre:
            nombre = email.split('@')[0].capitalize()

        nuevo_usuario = Usuario(
            nombre=nombre,
            email=email,
            password_hash=generate_password_hash(password)
        )
        db.session.add(nuevo_usuario)
        db.session.commit()

        session.permanent = True
        session['user_email'] = email
        session['user_name'] = nombre
        return jsonify({'status': 'ok', 'message': 'Registro exitoso'})

    return jsonify({'status': 'error', 'message': 'El correo no está registrado. Por favor, regístrate primero.'}), 404

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login_page'))

# --- STREAM CHAT CON MODELO QWEN 3.8 27B ---
@app.route('/chat_stream', methods=['POST'])
def chat_stream():
    if 'user_email' not in session:
        return jsonify({'error': 'No autorizado'}), 401

    data = request.get_json() or {}
    user_message = data.get('message', '').strip()
    target_lang = data.get('target_lang', 'Inglés')
    cefr_level = data.get('cefr_level', 'B1')
    mode = data.get('mode', 'tutor_general')
    rol_practica = data.get('rol_practica', 'Interlocutor General')
    examen_oficial = data.get('examen_oficial', '')
    rubrica = data.get('rubrica', '')
    
    file_name = data.get('file_name', '')
    file_content = data.get('file_content', '')

    user_name = session.get('user_name', 'Estudiante')

    prompt_contenido = user_message
    if file_content:
        prompt_contenido += f"\n\n--- ARCHIVO ADJUNTO ({file_name}) ---\n{file_content}\n--- FIN DEL ARCHIVO ---"

    # Definición específica de conducta según el modo activo
    instruccion_modo = ""
    if mode == "tutor_general":
        instruccion_modo = (
            "ESTÁS EN MODO TUTOR GENERAL. Tu objetivo principal es resolver dudas, explicar temas gramaticales, "
            "proporcionar vocabulario y responder preguntas del alumno. "
            "NO incites, sugieras ni fuerces al usuario a realizar simulaciones de examen, ni dinámicas orales, ni "
            "pruebas de nivel a menos que el usuario te lo solicite explícitamente."
        )
    elif mode == "practica_oral":
        instruccion_modo = (
            f"ESTÁS EN MODO PRÁCTICA ORAL. Asume el rol de: {rol_practica}. "
            "Mantén un diálogo fluido simulando esta situación real y haz preguntas acordes al nivel para promover la conversación."
        )
    elif mode == "examenes":
        instruccion_modo = (
            f"ESTÁS EN MODO EXÁMENES OFICIALES. Simula un examen oficial del tipo: {examen_oficial}. "
            f"Evalúa según la siguiente rúbrica: {rubrica}. Sé riguroso, haz preguntas del tipo de examen y evalúa el nivel {cefr_level}."
        )
    elif mode == "writing":
        instruccion_modo = (
            "ESTÁS EN MODO PRÁCTICA DE WRITING. Revisa la composición o archivo adjunto. Proporciona una corrección minuciosa "
            "de estructura, coherencia, vocabulario y gramática adaptada al nivel CEFR objetivo."
        )

    system_prompt = f"""
Eres Oralis, un tutor inteligente de idiomas. El estudiante con el que hablas se llama {user_name}.
Refiérete a él por su nombre ({user_name}) de forma cercana y natural durante la conversación.

CONFIGURACIÓN DE LA SESIÓN:
- Idioma Objetivo: {target_lang}
- Nivel CEFR Objetivo: {cefr_level}
- Modo Activo: {mode}

INSTRUCCIÓN DE MODO ESPECÍFICA:
{instruccion_modo}

REGLAS DE FORMATO ESTRICTAS:
1. NO utilices NUNCA asteriscos (* o **) ni almohadillas (#) en ninguna parte de tu respuesta.
2. Si vas a proporcionar listas de vocabulario, ejemplos, correcciones o puntos clave, debes estructurarlos obligatoriamente usando etiquetas HTML explícitas como <ul> y <li>, o listas numeradas <ol> y <li>.

ESTRUCTURA DE SALIDA OBLIGATORIA:
[RESPUESTA_PRINCIPAL]
Escribe aquí tu respuesta directa en {target_lang} adecuada al modo actual y adaptada al nivel {cefr_level}.

[TRADUCCION_INTEGRADA]
Traduce aquí la respuesta principal al idioma español para ayudar al estudiante.

[CORRECCION_Y_MEJORA]
Analiza los errores cometidos en el mensaje o texto del usuario (gramática, vocabulario, sintaxis) y proporciona sugerencias concretas de mejora usando listas HTML (<ul><li>...</li></ul>). Si el mensaje es correcto, indícalo felicitándolo.
"""

    def generate():
        try:
            completion = groq_client.chat.completions.create(
                model="qwen/qwen3.8-27b",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt_contenido}
                ],
                temperature=0.7,
                max_tokens=1500,
                stream=True
            )
            for chunk in completion:
                content = chunk.choices[0].delta.content
                if content:
                    yield content
        except Exception as e:
            yield f"[Error al conectar con Oralis: {str(e)}]"

    return Response(stream_with_context(generate()), content_type='text/plain; charset=utf-8')

# --- RUTAS DE DATOS ---
@app.route('/cuaderno/listar')
def listar_cuaderno():
    if 'user_email' not in session:
        return jsonify([])
    items = Cuaderno.query.filter_by(usuario_email=session['user_email']).order_by(Cuaderno.fecha.desc()).all()
    return jsonify([{
        'id': item.id,
        'idioma': item.idioma,
        'nivel': item.nivel,
        'categoria': item.categoria,
        'termino': item.termino,
        'explicacion': item.explicacion,
        'fecha': item.fecha.strftime('%Y-%m-%d')
    } for item in items])

@app.route('/historial/listar')
def listar_historial():
    if 'user_email' not in session:
        return jsonify([])
    chats = HistorialChat.query.filter_by(usuario_email=session['user_email']).order_by(HistorialChat.fecha.desc()).all()
    return jsonify([{
        'id': c.id,
        'titulo': c.titulo,
        'modo': c.modo,
        'idioma': c.idioma,
        'fecha': c.fecha.strftime('%Y-%m-%d %H:%M')
    } for c in chats])

@app.route('/feedback', methods=['POST'])
def save_feedback():
    if 'user_email' not in session:
        return jsonify({'error': 'No autorizado'}), 401
    data = request.get_json() or {}
    nuevo_fb = Feedback(
        usuario_email=session['user_email'],
        puntuacion=int(data.get('score', 5)),
        comentario=data.get('comment', '').strip()
    )
    db.session.add(nuevo_fb)
    db.session.commit()
    return jsonify({'status': 'ok'})

@app.route('/admin/feedbacks')
def admin_feedbacks():
    if session.get('user_email') != 'p75886777@gmail.com':
        return "Acceso denegado", 403
    feedbacks = Feedback.query.order_by(Feedback.fecha_creacion.desc()).all()
    total_usuarios = Usuario.query.count()
    return render_template('admin_feedbacks.html', feedbacks=feedbacks, total_usuarios=total_usuarios)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
