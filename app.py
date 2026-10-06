import os
import json
from flask import Flask, render_template, request, jsonify, Response, stream_with_context, session, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from groq import Groq

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "oralis_secret_key_2026")

# --- CONFIGURACIÓN DE BASE DE DATOS Y ARCHIVOS ---
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///oralis.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join(os.getcwd(), 'uploads')
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db = SQLAlchemy(app)

GROQ_KEY = os.environ.get("GROQ_API_KEY", "TU_API_KEY_DE_GROQ")
groq_client = Groq(api_key=GROQ_KEY)

# --- MODELOS DE BASE DE DATOS (PERSISTENCIA Y GESTIÓN) ---

class Usuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    nivel_cefr = db.Column(db.String(10), default='B1')
    fecha_registro = db.Column(db.DateTime, default=db.func.current_timestamp())

class Cuaderno(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    usuario_email = db.Column(db.String(120), nullable=False)
    idioma = db.Column(db.String(50), nullable=False)
    nivel = db.Column(db.String(10), nullable=False)
    categoria = db.Column(db.String(50), nullable=False) # Vocabulario o Gramática
    termino = db.Column(db.Text, nullable=False)
    explicacion = db.Column(db.Text, nullable=False)
    fecha = db.Column(db.DateTime, default=db.func.current_timestamp())

class HistorialChat(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    usuario_email = db.Column(db.String(120), nullable=False)
    titulo = db.Column(db.String(150), default="Conversación sin título")
    modo = db.Column(db.String(50), nullable=False)
    idioma = db.Column(db.String(50), nullable=False)
    contenido_json = db.Column(db.Text, nullable=False) # Mensajes guardados en JSON
    fecha = db.Column(db.DateTime, default=db.func.current_timestamp())

class WritingEvaluacion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    usuario_email = db.Column(db.String(120), nullable=False)
    texto_original = db.Column(db.Text, nullable=False)
    modo_correccion = db.Column(db.String(20), default="normal") # normal o desafio
    feedback_ia = db.Column(db.Text, nullable=False)
    fecha = db.Column(db.DateTime, default=db.func.current_timestamp())

class Feedback(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    usuario_email = db.Column(db.String(120), nullable=False)
    puntuacion = db.Column(db.Integer, nullable=False)
    comentario = db.Column(db.Text, nullable=False)
    fecha_creacion = db.Column(db.DateTime, default=db.func.current_timestamp())

with app.app_context():
    db.create_all()


# --- RUTAS DE AUTENTICACIÓN Y NAVEGACIÓN ---

@app.route('/')
def home():
    if 'user_email' not in session:
        return redirect(url_for('login_page'))
    user = Usuario.query.filter_by(email=session['user_email']).first()
    return render_template('index.html', user_email=session['user_email'], user=user)

@app.route('/login_page')
def login_page():
    if 'user_email' in session:
        return redirect(url_for('home'))
    return render_template('login.html')

@app.route('/register', methods=['POST'])
def register():
    # Registro simplificado: Usuario, Correo y Contraseña
    data = request.get_json() or {}
    username = data.get('username', '').strip()
    email = data.get('email', '').strip().lower()
    password = data.get('password', '').strip()

    if not username or not email or not password:
        return jsonify({'status': 'error', 'message': 'Por favor completa todos los campos.'}), 400

    if Usuario.query.filter_by(email=email).first():
        return jsonify({'status': 'error', 'message': 'El correo ya está registrado.'}), 400

    hashed_pw = generate_password_hash(password)
    nuevo_usuario = Usuario(username=username, email=email, password_hash=hashed_pw)
    db.session.add(nuevo_usuario)
    db.session.commit()

    session['user_email'] = email
    return jsonify({'status': 'ok'})

@app.route('/login', methods=['POST'])
def login():
    data = request.get_json() or {}
    email = data.get('email', '').strip().lower()
    password = data.get('password', '').strip()

    usuario = Usuario.query.filter_by(email=email).first()
    if usuario and check_password_hash(usuario.password_hash, password):
        session['user_email'] = email
        return jsonify({'status': 'ok'})

    return jsonify({'status': 'error', 'message': 'Credenciales incorrectas.'}), 401

@app.route('/logout')
def logout():
    session.pop('user_email', None)
    return redirect(url_for('login_page'))


# --- MOTOR PRINCIPAL IA Y CHAT CON DETECCIÓN DE IDIOMA Y ESTRUCTURA TRIPLE ---

@app.route('/chat_stream', methods=['POST'])
def chat_stream():
    if 'user_email' not in session:
        return jsonify({'error': 'No autorizado'}), 401

    data = request.get_json() or {}
    user_message = data.get('message', '').strip()
    target_lang = data.get('target_lang', 'Inglés')
    cefr_level = data.get('cefr_level', 'B1')
    mode = data.get('mode', 'tutor_general')
    
    # Parámetros para Modos Especializados
    rol_practica = data.get('rol_practica', 'Interlocutor General')
    examen_oficial = data.get('examen_oficial', '')
    rubrica = data.get('rubrica', '')

    system_prompt = f"""
Eres Oralis, un tutor inteligente e interactivo de idiomas.
- Idioma Objetivo de Práctica: {target_lang}.
- Nivel CEFR Objetivo: {cefr_level}.
- Modo Activo: {mode}.
- Rol/Simulación Activa: {rol_practica}.
- Examen Seleccionado: {examen_oficial}.
- Criterios/Rúbrica Específica: {rubrica}.

REGLAS DE RESPUESTA OBLIGATORIAS:
Debes formatear tu salida dividida estrictamente en tres secciones mediante estas etiquetas en mayúsculas:

[RESPUESTA_PRINCIPAL]
Escribe la respuesta principal en el idioma objetivo ({target_lang}) adaptada al nivel {cefr_level} y asumiendo el rol asignado si aplica.

[TRADUCCION_INTEGRADA]
Analiza el mensaje previo del estudiante, detecta automáticamente el idioma en el que te ha escrito (por ejemplo, español o inglés) y traduce AQUÍ tu [RESPUESTA_PRINCIPAL] a ese idioma detectado para facilitar su comprensión.

[CORRECCION_Y_MEJORA]
Analiza la intervención del usuario e incluye:
- Evaluación del mensaje enviado por el usuario.
- Explicación de errores gramaticales o de vocabulario cometidos.
- Sugerencias de alternativas más naturales de nivel {cefr_level}.
- Si identificas un término o regla clave para guardar, pon al final: "[CUADERNO_AUTO]: Término | Explicación breve".
"""

    def generate():
        try:
            completion = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
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


# --- MÓDULO 4: EVALUACIÓN DE WRITING (NORMAL, DESAFÍO Y PISTAS) ---

@app.route('/evaluate_writing', methods=['POST'])
def evaluate_writing():
    if 'user_email' not in session:
        return jsonify({'error': 'No autorizado'}), 401

    data = request.get_json() or {}
    text_to_eval = data.get('text', '').strip()
    target_lang = data.get('target_lang', 'Inglés')
    cefr_level = data.get('cefr_level', 'B1')
    modo_correccion = data.get('modo_correccion', 'normal')

    if modo_correccion == 'desafio':
        instrucciones_modo = """
MODO DESAFÍO (GUIADO):
- Resalta y señala las zonas o frases con errores en el texto.
- NO des la solución corregida ni reescribas el texto.
- Ofrece una pista orientativa para que el estudiante intente corregirlo por sí mismo.
"""
    else:
        instrucciones_modo = """
MODO NORMAL:
- Muestra los errores detectados de forma explícita.
- Entrega la explicación gramatical detallada.
- Proporciona el texto completo en su versión corregida y optimizada.
"""

    prompt = f"""
Evalúa la siguiente redacción en {target_lang} (Nivel {cefr_level}):
"{text_to_eval}"

{instrucciones_modo}
"""

    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=1500
        )
        feedback_text = response.choices[0].message.content

        nueva_eval = WritingEvaluacion(
            usuario_email=session['user_email'],
            texto_original=text_to_eval,
            modo_correccion=modo_correccion,
            feedback_ia=feedback_text
        )
        db.session.add(nueva_eval)
        db.session.commit()

        return jsonify({'status': 'ok', 'feedback': feedback_text})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/request_writing_hint', methods=['POST'])
def request_writing_hint():
    data = request.get_json() or {}
    texto = data.get('text', '')
    pista_num = data.get('hint_level', 1)

    prompt = f"El alumno está corrigiendo su texto en Modo Desafío: '{texto}'. Proporcióndale la Pista #{pista_num} de manera gradual, dándole un indicio más claro sin revelar la respuesta final."

    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.6,
            max_tokens=300
        )
        return jsonify({'status': 'ok', 'hint': response.choices[0].message.content})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# --- MÓDULO 7 Y 8: MI CUADERNO E HISTORIAL DE CHATS ---

@app.route('/cuaderno/guardar', methods=['POST'])
def guardar_cuaderno():
    if 'user_email' not in session:
        return jsonify({'error': 'No autorizado'}), 401

    data = request.get_json() or {}
    nuevo_item = Cuaderno(
        usuario_email=session['user_email'],
        idioma=data.get('idioma', 'Inglés'),
        nivel=data.get('nivel', 'B1'),
        categoria=data.get('categoria', 'Vocabulario'),
        termino=data.get('termino', '').strip(),
        explicacion=data.get('explicacion', '').strip()
    )
    db.session.add(nuevo_item)
    db.session.commit()
    return jsonify({'status': 'ok'})

@app.route('/cuaderno/listar')
def listar_cuaderno():
    if 'user_email' not in session:
        return jsonify({'error': 'No autorizado'}), 401

    items = Cuaderno.query.filter_by(usuario_email=session['user_email']).order_by(Cuaderno.fecha.desc()).all()
    resultado = [{
        'id': item.id,
        'idioma': item.idioma,
        'nivel': item.nivel,
        'categoria': item.categoria,
        'termino': item.termino,
        'explicacion': item.explicacion,
        'fecha': item.fecha.strftime('%Y-%m-%d')
    } for item in items]

    return jsonify(resultado)

@app.route('/historial/guardar', methods=['POST'])
def guardar_historial():
    if 'user_email' not in session:
        return jsonify({'error': 'No autorizado'}), 401

    data = request.get_json() or {}
    nuevo_chat = HistorialChat(
        usuario_email=session['user_email'],
        titulo=data.get('titulo', 'Conversación Oralis'),
        modo=data.get('modo', 'tutor_general'),
        idioma=data.get('idioma', 'Inglés'),
        contenido_json=json.dumps(data.get('mensajes', []))
    )
    db.session.add(nuevo_chat)
    db.session.commit()
    return jsonify({'status': 'ok', 'chat_id': nuevo_chat.id})

@app.route('/historial/listar')
def listar_historial():
    if 'user_email' not in session:
        return jsonify({'error': 'No autorizado'}), 401

    chats = HistorialChat.query.filter_by(usuario_email=session['user_email']).order_by(HistorialChat.fecha.desc()).all()
    resultado = [{
        'id': c.id,
        'titulo': c.titulo,
        'modo': c.modo,
        'idioma': c.idioma,
        'fecha': c.fecha.strftime('%Y-%m-%d %H:%M')
    } for c in chats]
    return jsonify(resultado)


# --- PANEL DE ADMINISTRACIÓN RESTRINGIDO Y FEEDBACKS ---

@app.route('/admin/feedbacks')
def admin_feedbacks():
    user_email = session.get('user_email')
    if user_email != 'p75886777@gmail.com':
        return "Acceso denegado. Panel exclusivo para el administrador.", 403

    feedbacks = Feedback.query.order_by(Feedback.fecha_creacion.desc()).all()
    total_usuarios = Usuario.query.count()
    total_writings = WritingEvaluacion.query.count()

    return render_template('admin_feedbacks.html', 
                           feedbacks=feedbacks, 
                           total_usuarios=total_usuarios, 
                           total_writings=total_writings,
                           admin_email=user_email)

@app.route('/admin/feedbacks/eliminar/<int:fb_id>', methods=['POST'])
def delete_feedback(fb_id):
    if session.get('user_email') != 'p75886777@gmail.com':
        return jsonify({'error': 'No autorizado'}), 403

    fb = Feedback.query.get_or_404(fb_id)
    db.session.delete(fb)
    db.session.commit()
    return redirect(url_for('admin_feedbacks'))

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


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
