import os
import re
import json
from datetime import datetime
from flask import (
    Flask, render_template, request, jsonify, redirect, 
    url_for, session, Response, stream_with_context, flash
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from groq import Groq

app = Flask(__name__)

# ---------------------------------------------------------------------------
# CONFIGURACIÓN GENERAL Y BASE DE DATOS
# ---------------------------------------------------------------------------
app.secret_key = os.environ.get("SECRET_KEY", "oralis-cream-green-secret-2026")

# Normalización de la URL de PostgreSQL o SQLite para Render/Local
db_url = os.environ.get("DATABASE_URL", "sqlite:///oralis.db")
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql+psycopg2://", 1)
elif db_url.startswith("postgresql://") and not db_url.startswith("postgresql+"):
    db_url = db_url.replace("postgresql://", "postgresql+psycopg2://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# Cliente de IA de Groq
groq_api_key = os.environ.get("GROQ_API_KEY", "gsk_dummy_key_oralis")
client = Groq(api_key=groq_api_key)

ADMIN_EMAIL = "p75886777@gmail.com"

# ---------------------------------------------------------------------------
# MODELOS DE BASE DE DATOS
# ---------------------------------------------------------------------------

class Usuario(db.Model):
    __tablename__ = 'usuarios'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    nivel_cefr = db.Column(db.String(10), default="B1")
    es_admin = db.Column(db.Boolean, default=False)
    fecha_registro = db.Column(db.DateTime, default=datetime.utcnow)

    historiales = db.relationship('HistorialChat', backref='usuario', lazy=True, cascade="all, delete-orphan")
    cuadernos = db.relationship('CuadernoRepaso', backref='usuario', lazy=True, cascade="all, delete-orphan")
    feedbacks = db.relationship('Feedback', backref='usuario', lazy=True, cascade="all, delete-orphan")
    writings = db.relationship('WritingEntregado', backref='usuario', lazy=True, cascade="all, delete-orphan")


class HistorialChat(db.Model):
    __tablename__ = 'historial_chats'
    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuarios.id'), nullable=False)
    idioma = db.Column(db.String(30), nullable=False)
    modo = db.Column(db.String(30), default='tutor') # tutor, oral, examen, writing
    rol = db.Column(db.String(20), nullable=False) # user, assistant, system
    contenido = db.Column(db.Text, nullable=False)
    traduccion = db.Column(db.Text, nullable=True)
    correccion = db.Column(db.Text, nullable=True)
    fecha = db.Column(db.DateTime, default=datetime.utcnow)


class CuadernoRepaso(db.Model):
    __tablename__ = 'cuaderno_repaso'
    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuarios.id'), nullable=False)
    idioma = db.Column(db.String(30), nullable=False)
    tipo = db.Column(db.String(20), nullable=False) # vocabulario, gramatica
    nivel = db.Column(db.String(10), default='B1')
    tema = db.Column(db.String(100), default='General')
    item = db.Column(db.Text, nullable=False)
    traduccion = db.Column(db.Text, nullable=True)
    fecha_guardado = db.Column(db.DateTime, default=datetime.utcnow)


class Feedback(db.Model):
    __tablename__ = 'feedbacks'
    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuarios.id'), nullable=True)
    usuario_email = db.Column(db.String(120), nullable=True)
    puntuacion = db.Column(db.Integer, nullable=False)
    comentario = db.Column(db.Text, nullable=False)
    fecha_creacion = db.Column(db.DateTime, default=datetime.utcnow)


class WritingEntregado(db.Model):
    __tablename__ = 'writings_entregados'
    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuarios.id'), nullable=False)
    idioma = db.Column(db.String(30), nullable=False)
    modo_correccion = db.Column(db.String(20), nullable=False) # normal, desafio
    texto_original = db.Column(db.Text, nullable=False)
    correccion_resultado = db.Column(db.Text, nullable=False)
    fecha = db.Column(db.DateTime, default=datetime.utcnow)


with app.app_context():
    try:
        db.create_all()
    except Exception as e:
        print(f"Nota en creación de tablas BDD: {e}")

# ---------------------------------------------------------------------------
# RUTAS DE AUTENTICACIÓN
# ---------------------------------------------------------------------------

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        if 'user_id' in session:
            return redirect(url_for('index'))
        return render_template('login.html')

    data = request.get_json() or {}
    email = data.get('email', '').strip().lower()
    password = data.get('password', '').strip()

    if not email or not password:
        return jsonify({'status': 'error', 'message': 'Escribe tu correo y contraseña.'}), 400

    user = Usuario.query.filter_by(email=email).first()
    if user and check_password_hash(user.password_hash, password):
        session['user_id'] = user.id
        session['username'] = user.username
        session['email'] = user.email
        session['es_admin'] = (user.email == ADMIN_EMAIL or user.es_admin)
        session['nivel_cefr'] = user.nivel_cefr
        return jsonify({'status': 'ok', 'message': 'Inicio de sesión correcto.'})

    return jsonify({'status': 'error', 'message': 'Credenciales no válidas.'}), 401


@app.route('/register', methods=['POST'])
def register():
    data = request.get_json() or {}
    username = data.get('username', '').strip()
    email = data.get('email', '').strip().lower()
    password = data.get('password', '').strip()
    nivel = data.get('nivel_cefr', 'B1')

    if not username or not email or not password:
        return jsonify({'status': 'error', 'message': 'Por favor rellena todos los datos.'}), 400

    if Usuario.query.filter_by(email=email).first():
        return jsonify({'status': 'error', 'message': 'Este correo ya está registrado.'}), 400

    if Usuario.query.filter_by(username=username).first():
        return jsonify({'status': 'error', 'message': 'El nombre de usuario ya está ocupado.'}), 400

    es_admin = (email == ADMIN_EMAIL)
    hash_password = generate_password_hash(password)

    nuevo_usuario = Usuario(
        username=username,
        email=email,
        password_hash=hash_password,
        nivel_cefr=nivel,
        es_admin=es_admin
    )
    db.session.add(nuevo_usuario)
    db.session.commit()

    session['user_id'] = nuevo_usuario.id
    session['username'] = nuevo_usuario.username
    session['email'] = nuevo_usuario.email
    session['es_admin'] = nuevo_usuario.es_admin
    session['nivel_cefr'] = nuevo_usuario.nivel_cefr

    return jsonify({'status': 'ok', 'message': 'Cuenta creada exitosamente.'})


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/usuario/nivel', methods=['POST'])
def cambiar_nivel():
    if 'user_id' not in session:
        return jsonify({'status': 'error', 'message': 'No autorizado'}), 401
    
    data = request.get_json() or {}
    nuevo_nivel = data.get('nivel', 'B1')
    
    user = Usuario.query.get(session['user_id'])
    if user:
        user.nivel_cefr = nuevo_nivel
        db.session.commit()
        session['nivel_cefr'] = nuevo_nivel
        return jsonify({'status': 'ok', 'nivel': nuevo_nivel})
    return jsonify({'status': 'error'}), 400

# ---------------------------------------------------------------------------
# RUTAS PRINCIPALES DEL CHAT Y MODOS
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template(
        'index.html', 
        username=session.get('username'), 
        email=session.get('email'),
        es_admin=session.get('es_admin', False),
        nivel_cefr=session.get('nivel_cefr', 'B1'),
        admin_email=ADMIN_EMAIL
    )


@app.route('/chat/historial/<idioma>/<modo>', methods=['GET'])
def obtener_historial(idioma, modo):
    if 'user_id' not in session:
        return jsonify({'status': 'error', 'message': 'No autorizado'}), 401

    user_id = session['user_id']
    mensajes = HistorialChat.query.filter_by(
        usuario_id=user_id, idioma=idioma, modo=modo
    ).order_by(HistorialChat.fecha.asc()).all()

    historial = [{
        'rol': m.rol, 
        'contenido': m.contenido,
        'traduccion': m.traduccion,
        'correccion': m.correccion
    } for m in mensajes]
    
    return jsonify({'status': 'ok', 'historial': historial})


@app.route('/chat/enviar', methods=['POST'])
def enviar_mensaje():
    if 'user_id' not in session:
        return jsonify({'status': 'error', 'message': 'No autorizado'}), 401

    user_id = session['user_id']
    data = request.get_json() or {}
    mensaje_texto = data.get('mensaje', '').strip()
    idioma = data.get('idioma', 'Inglés')
    modo = data.get('modo', 'tutor') # tutor, oral, examen, writing
    profesion_rol = data.get('profesion', 'Tutor General de Idiomas')
    examen_nombre = data.get('examen_nombre', '')
    detalles_examen = data.get('detalles_examen', '')
    idioma_estudiante = data.get('idioma_estudiante', 'Español')

    if not mensaje_texto:
        return jsonify({'status': 'error', 'message': 'Escribe un mensaje.'}), 400

    # Guardar mensaje del usuario
    msg_user = HistorialChat(
        usuario_id=user_id, idioma=idioma, modo=modo, rol='user', contenido=mensaje_texto
    )
    db.session.add(msg_user)
    db.session.commit()

    # Cargar historial reciente
    historial_previo = HistorialChat.query.filter_by(
        usuario_id=user_id, idioma=idioma, modo=modo
    ).order_by(HistorialChat.fecha.asc()).limit(10).all()

    # Formulación del Prompt del Sistema según el modo
    nivel_actual = session.get('nivel_cefr', 'B1')

    system_prompt = f"""Eres Oralis, un tutor de idiomas de élite con acento nativo y pedagógico.
Idioma de práctica que enseñas actualmente: {idioma.upper()}.
Idioma nativo o preferido del estudiante para explicaciones y traducciones: {idioma_estudiante.upper()}.
Nivel CEFR objetivo del estudiante: {nivel_actual}.
Modo activo actual: {modo.upper()}.
Rol o Profesión asignada a Oralis: {profesion_rol}.
"""

    if modo == 'oral':
        system_prompt += f"\nInstrucción especial Modo Oral: Mantén respuestas fluidas, conversacionales y profesionales adoptando la profesión de {profesion_rol}. Fomenta que el estudiante practique su speaking y listening."
    elif modo == 'examen':
        system_prompt += f"\nInstrucción especial Modo Examen: Actúa como examinador oficial de {examen_nombre}. Evaluación/Rúbrica a aplicar: {detalles_examen if detalles_examen else 'Estándar oficial del examen'}. Realiza preguntas tipo examen e indica la puntuación o aspectos de mejora."
    elif modo == 'writing':
        system_prompt += "\nInstrucción especial Modo Writing: Evalúa la coherencia, gramática, vocabulario y estructura del ensayo o redacción enviada."

    system_prompt += f"""\n\nREGLAS INVIOLABLES DE FORMATO EN TU RESPUESTA:
1. Responde de forma natural en el idioma de práctica ({idioma.upper()}).
2. SIEMPRE al final de tu respuesta debes incluir OBLIGATORIAMENTE dos bloques etiquetados exactos:

---TRADUCCION---
[Traduce aquí tu respuesta completa de forma natural al {idioma_estudiante.upper()}]

---CORRECCION---
[Evalúa el mensaje del estudiante. Si cometió errores gramaticales o de vocabulario, señálalos amablemente y muestra la forma corregida. Si estuvo perfecto, felicítalo brevemente en {idioma_estudiante.upper()}]

---CUADERNO---
[Si en la conversación se usó vocabulario o gramática útil, añádelos en este formato: VOCABULARIO: palabra = traducción | GRAMATICA: regla breve]
"""

    messages = [{"role": "system", "content": system_prompt}]
    for h in historial_previo:
        messages.append({"role": h.rol, "content": h.contenido})

    def generate():
        try:
            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=messages,
                temperature=0.7,
                stream=True
            )

            respuesta_completa = ""
            for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    respuesta_completa += content
                    yield content

            # Procesar y guardar en BDD la respuesta con traducciones y cuaderno
            with app.app_context():
                partes_trad = respuesta_completa.split("---TRADUCCION---")
                texto_oralis = partes_trad[0].strip()
                
                traduccion_texto = ""
                correccion_texto = ""

                if len(partes_trad) > 1:
                    partes_corr = partes_trad[1].split("---CORRECCION---")
                    traduccion_texto = partes_corr[0].strip()

                    if len(partes_corr) > 1:
                        partes_cuad = partes_corr[1].split("---CUADERNO---")
                        correccion_texto = partes_cuad[0].strip()

                        if len(partes_cuad) > 1:
                            extraer_y_guardar_cuaderno(user_id, idioma, nivel_actual, partes_cuad[1].strip())

                msg_bot = HistorialChat(
                    usuario_id=user_id,
                    idioma=idioma,
                    modo=modo,
                    rol='assistant',
                    contenido=texto_oralis,
                    traduccion=traduccion_texto,
                    correccion=correccion_texto
                )
                db.session.add(msg_bot)
                db.session.commit()

        except Exception as e:
            yield f"\n[Error de conexión con Oralis AI: {str(e)}]"

    return Response(stream_with_context(generate()), content_type='text/plain; charset=utf-8')


def extraer_y_guardar_cuaderno(user_id, idioma, nivel, texto_cuaderno):
    try:
        lineas = texto_cuaderno.split('\n')
        for linea in lineas:
            if 'VOCABULARIO:' in linea:
                item = linea.replace('VOCABULARIO:', '').strip()
                if item and not CuadernoRepaso.query.filter_by(usuario_id=user_id, idioma=idioma, tipo='vocabulario', item=item).first():
                    db.session.add(CuadernoRepaso(
                        usuario_id=user_id, idioma=idioma, tipo='vocabulario', nivel=nivel, tema='Chat Inteligente', item=item
                    ))
            elif 'GRAMATICA:' in linea:
                item = linea.replace('GRAMATICA:', '').strip()
                if item and not CuadernoRepaso.query.filter_by(usuario_id=user_id, idioma=idioma, tipo='gramatica', item=item).first():
                    db.session.add(CuadernoRepaso(
                        usuario_id=user_id, idioma=idioma, tipo='gramatica', nivel=nivel, tema='Chat Inteligente', item=item
                    ))
        db.session.commit()
    except Exception as e:
        print(f"Error procesando cuaderno: {e}")


@app.route('/chat/limpiar/<idioma>/<modo>', methods=['DELETE'])
def limpiar_chat(idioma, modo):
    if 'user_id' not in session:
        return jsonify({'status': 'error', 'message': 'No autorizado'}), 401

    user_id = session['user_id']
    HistorialChat.query.filter_by(usuario_id=user_id, idioma=idioma, modo=modo).delete()
    db.session.commit()
    return jsonify({'status': 'ok', 'message': 'Historial vaciado.'})

# ---------------------------------------------------------------------------
# MODO WRITING ESPECIALIZADO (NORMAL / DESAFÍO CON PISTAS)
# ---------------------------------------------------------------------------

@app.route('/writing/evaluar', methods=['POST'])
def evaluar_writing():
    if 'user_id' not in session:
        return jsonify({'status': 'error', 'message': 'No autorizado'}), 401

    data = request.get_json() or {}
    texto = data.get('texto', '').strip()
    idioma = data.get('idioma', 'Inglés')
    modo_correccion = data.get('modo_correccion', 'normal') # normal o desafio
    idioma_estudiante = data.get('idioma_estudiante', 'Español')

    if not texto:
        return jsonify({'status': 'error', 'message': 'Por favor introduce o sube un texto.'}), 400

    if modo_correccion == 'normal':
        prompt = f"""Eres un profesor experto corrigiendo una redacción en {idioma.upper()}.
El idioma explicativo del estudiante es {idioma_estudiante.upper()}.
Analiza el siguiente texto:
"{texto}"

Proporciona:
1. Puntuación global estimada (0 al 10) y nivel CEFR demostrado.
2. Desglose de Errores Gramaticales y de Vocabulario con sus explicaciones directas.
3. Versión corregida y reescrita de manera fluida y elegante.
"""
    else: # Modo Desafío
        prompt = f"""Eres un tutor desafiante. El estudiante ha enviado esta redacción en {idioma.upper()}:
"{texto}"

INSTRUCCIONES DEL MODO DESAFÍO:
1. NO le des la respuesta corregida directamente.
2. Señala entre corchetes o mediante lista numerada las frases/palabras concretas donde cometió fallos (Gramática, Ortografía, Estructura).
3. Pídele al estudiante que intente resolver los errores por sí mismo.
4. Incluye un código de Pistas ocultas con el tag [PISTA 1], [PISTA 2] para cuando el alumno pida ayuda con el botón de pistas.
"""

    try:
        res = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5
        )
        resultado = res.choices[0].message.content

        # Guardar entregable en la BDD
        we = WritingEntregado(
            usuario_id=session['user_id'],
            idioma=idioma,
            modo_correccion=modo_correccion,
            texto_original=texto,
            correccion_resultado=resultado
        )
        db.session.add(we)
        db.session.commit()

        return jsonify({'status': 'ok', 'resultado': resultado})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/writing/pista', methods=['POST'])
def pedir_pista():
    if 'user_id' not in session:
        return jsonify({'status': 'error', 'message': 'No autorizado'}), 401

    data = request.get_json() or {}
    texto = data.get('texto', '')
    idioma = data.get('idioma', 'Inglés')

    prompt = f"""El estudiante necesita una pista sutil para corregir su redacción en {idioma}:
"{texto}"
Dale una pista indirecta sobre una regla gramatical o elección de palabra sin revelar el texto corregido final.
"""
    try:
        res = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.6
        )
        return jsonify({'status': 'ok', 'pista': res.choices[0].message.content})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

# ---------------------------------------------------------------------------
# CUADERNO DE REPASO Y BANCO RECOMENDADO
# ---------------------------------------------------------------------------

@app.route('/cuaderno/<idioma>', methods=['GET'])
def obtener_cuaderno(idioma):
    if 'user_id' not in session:
        return jsonify({'status': 'error', 'message': 'No autorizado'}), 401

    user_id = session['user_id']
    items = CuadernoRepaso.query.filter_by(usuario_id=user_id, idioma=idioma).all()

    vocab = [{'id': i.id, 'item': i.item, 'nivel': i.nivel, 'tema': i.tema} for i in items if i.tipo == 'vocabulario']
    gram = [{'id': i.id, 'item': i.item, 'nivel': i.nivel, 'tema': i.tema} for i in items if i.tipo == 'gramatica']

    return jsonify({'status': 'ok', 'vocabulario': vocab, 'gramatica': gram})


@app.route('/cuaderno/eliminar/<int:item_id>', methods=['DELETE'])
def eliminar_item_cuaderno(item_id):
    if 'user_id' not in session:
        return jsonify({'status': 'error', 'message': 'No autorizado'}), 401

    item = CuadernoRepaso.query.filter_by(id=item_id, usuario_id=session['user_id']).first()
    if item:
        db.session.delete(item)
        db.session.commit()
        return jsonify({'status': 'ok'})
    return jsonify({'status': 'error', 'message': 'Elemento no encontrado'}), 404

# ---------------------------------------------------------------------------
# FEEDBACK Y PANEL DE ADMINISTRACIÓN
# ---------------------------------------------------------------------------

@app.route('/feedback/enviar', methods=['POST'])
def enviar_feedback():
    data = request.get_json() or {}
    puntuacion = data.get('puntuacion', 5)
    comentario = data.get('comentario', '').strip()

    if not comentario:
        return jsonify({'status': 'error', 'message': 'Escribe un comentario.'}), 400

    fb = Feedback(
        usuario_id=session.get('user_id'),
        usuario_email=session.get('email', 'Anónimo'),
        puntuacion=puntuacion,
        comentario=comentario
    )
    db.session.add(fb)
    db.session.commit()

    return jsonify({'status': 'ok', 'message': '¡Gracias por tus comentarios!'})


@app.route('/admin/feedbacks', methods=['GET'])
def admin_feedbacks():
    if 'user_id' not in session or session.get('email') != ADMIN_EMAIL:
        flash("Acceso restringido únicamente a la administradora autorizada.")
        return redirect(url_for('index'))

    feedbacks = Feedback.query.order_by(Feedback.fecha_creacion.desc()).all()
    total_usuarios = Usuario.query.count()
    total_writings = WritingEntregado.query.count()

    return render_template(
        'admin_feedbacks.html', 
        feedbacks=feedbacks, 
        total_usuarios=total_usuarios,
        total_writings=total_writings,
        admin_email=ADMIN_EMAIL
    )


@app.route('/admin/feedbacks/eliminar/<int:id>', methods=['POST'])
def eliminar_feedback(id):
    if 'user_id' not in session or session.get('email') != ADMIN_EMAIL:
        return jsonify({'status': 'error', 'message': 'No autorizado'}), 403

    fb = Feedback.query.get_or_404(id)
    db.session.delete(fb)
    db.session.commit()
    flash("Comentario borrado correctamente.")
    return redirect(url_for('admin_feedbacks'))


@app.route('/estadisticas', methods=['GET'])
def obtener_estadisticas():
    if 'user_id' not in session:
        return jsonify({'status': 'error', 'message': 'No autorizado'}), 401

    user_id = session['user_id']
    total_vocab = CuadernoRepaso.query.filter_by(usuario_id=user_id, tipo='vocabulario').count()
    total_gram = CuadernoRepaso.query.filter_by(usuario_id=user_id, tipo='gramatica').count()
    total_writings = WritingEntregado.query.filter_by(usuario_id=user_id).count()
    total_chats = HistorialChat.query.filter_by(usuario_id=user_id, rol='user').count()

    return jsonify({
        'status': 'ok',
        'vocabulario': total_vocab,
        'gramatica': total_gram,
        'writings': total_writings,
        'mensajes': total_chats
    })

if __name__ == '__main__':
    app.run(debug=True)
