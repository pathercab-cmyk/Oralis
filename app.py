import os
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

# --- MODELOS DE BASE DE DATOS (SISTEMA DE PERSISTENCIA POR USUARIO) ---

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
    contenido_json = db.Column(db.Text, nullable=False) # Historial estructurado
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


# --- MOTOR PRINCIPAL IA CON ESTRUCTURA DE RESPUESTA Y DETECCIÓN DE IDIOMA ---

@app.route('/chat_stream', methods=['POST'])
def chat_stream():
    if 'user_email' not in session:
        return jsonify({'error': 'No autorizado'}), 401

    data = request.get_json() or {}
    user_message = data.get('message', '').strip()
    target_lang = data.get('target_lang', 'Inglés')
    cefr_level = data.get('cefr_level', 'B1')
    mode = data.get('mode', 'tutor_general')
    
    # Parámetros adicionales según el modo
    rol_practica = data.get('rol_practica', 'Interlocutor general')
    examen_oficial = data.get('examen_oficial', '')
    rubrica = data.get('rubrica', '')

    system_prompt = f"""
Eres Oralis, un tutor inteligente de idiomas.
- Idioma Objetivo de práctica: {target_lang}.
- Nivel CEFR Objetivo: {cefr_level}.
- Modo Activo: {mode}.
- Rol/Contexto Específico: {rol_practica}.
- Examen Objetivo (si aplica): {examen_oficial}.
- Rúbrica/Criterios adicionales: {rubrica}.

INSTRUCCIONES OBLIGATORIAS DE ESTRUCTURA Y FORMATO DE RESPUESTA:
Debes responder SIEMPRE estructurando tu salida en 3 bloques claramente delimitados por etiquetas Markdown exactas para que el sistema de interfaz pueda separarlas:

1. [RESPUESTA_PRINCIPAL]
Responde directamente en el idioma objetivo ({target_lang}) adaptando la dificultad a nivel {cefr_level}. Actúa según el modo activo ({mode}).

2. [TRADUCCION_INTEGRADA]
Detecta automáticamente en qué idioma te habló el estudiante en su último mensaje y traduce tu [RESPUESTA_PRINCIPAL] exactamente a ese idioma nativo/de entrada del usuario.

3. [CORRECCION_Y_MEJORA]
Analiza la gramática, vocabulario y fluidez del mensaje del usuario. Proporciona:
- Evaluación del mensaje enviado por el usuario.
- Correcciones de errores cometidos.
- Sugerencia de palabras o frases más naturales para su nivel ({cefr_level}).
- [CUADERNO_AUTO]: Si encuentras una palabra clave o regla relevante, indícala al final en este formato: "Término | Explicación corta" para que el usuario pueda guardarla en su cuaderno.
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
            yield f"[Error de respuesta de Oralis: {str(e)}]"

    return Response(stream_with_context(generate()), content_type='text/plain; charset=utf-8')


# --- MÓDULO 4: EVALUACIÓN DE WRITING (NORMAL Y DESAFÍO CON PISTAS) ---

@app.route('/evaluate_writing', methods=['POST'])
def evaluate_writing():
    if 'user_email' not in session:
        return jsonify({'error': 'No autorizado'}), 401

    data = request.get_json() or {}
    text_to_eval = data.get('text', '').strip()
    target_lang = data.get('target_lang', 'Inglés')
    cefr_level = data.get('cefr_level', 'B1')
    modo_correccion = data.get('modo_correccion', 'normal') # 'normal' o 'desafio'

    if modo_correccion == 'desafio':
        prompt_modo = f"""
EVALUACIÓN EN MODO DESAFÍO (GUIADO):
1. Resalta y señala las frases o zonas del texto donde hay errores gramaticales u ortográficos.
2. NO des la solución directa ni reescribas el texto corregido.
3. Proporciona una "Pista Inicial" por cada área señalada para orientar al alumno a corregirlo por sí mismo.
"""
    else:
        prompt_modo = f"""
EVALUACIÓN EN MODO NORMAL:
1. Puntuación global sobre 10.
2. Desglose detallado de errores gramaticales y ortográficos.
3. Explicación clara de las reglas aplicadas.
4. Versión optimizada del escrito.
"""

    prompt = f"""
Evalúa la siguiente redacción escrita para nivel {cefr_level} en {target_lang}:
"{text_to_eval}"

{prompt_modo}
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

    prompt = f"El alumno está intentando corregir este texto: '{texto}'. Ofrécele la Pista #{pista_num} adicional (más específica) sin revelarle la respuesta completa."
    
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


# --- MÓDULO 7: MI CUADERNO (REPASO INTELIGENTE) ---

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


# --- MÓDULO 8: HISTORIAL Y PANEL ADMINISTRADOR ---

@app.route('/admin/feedbacks')
def admin_feedbacks():
    user_email = session.get('user_email')
    if user_email != 'p75886777@gmail.com':
        return "Acceso denegado. Panel privado exclusivo para administración.", 403

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
