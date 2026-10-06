import os
from flask import Flask, render_template, request, jsonify, Response, stream_with_context, session, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from groq import Groq

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "oralis_secret_key_2026")

# Configuración Base de Datos SQLite
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///oralis.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Cliente Groq AI (Asegúrate de tener GROQ_API_KEY en tus variables de entorno)
groq_client = Groq(api_key=os.environ.get("GROQ_API_KEY", "TU_API_KEY_DE_GROQ"))

# --- MODELOS DE LA BASE DE DATOS ---
class Usuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)

class Feedback(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    usuario_email = db.Column(db.String(120), nullable=False)
    puntuacion = db.Column(db.Integer, nullable=False)
    comentario = db.Column(db.Text, nullable=False)
    fecha_creacion = db.Column(db.DateTime, default=db.func.current_timestamp())

class WritingEvaluacion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    usuario_email = db.Column(db.String(120), nullable=False)
    texto_original = db.Column(db.Text, nullable=False)
    feedback_ia = db.Column(db.Text, nullable=False)
    fecha = db.Column(db.DateTime, default=db.func.current_timestamp())

with app.app_context():
    db.create_all()

# --- RUTAS DE AUTENTICACIÓN ---
@app.route('/')
def home():
    if 'user_email' not in session:
        return redirect(url_for('login_page'))
    return render_template('index.html', user_email=session['user_email'])

@app.route('/login_page')
def login_page():
    return render_template('login.html')

@app.route('/register', methods=['POST'])
def register():
    data = request.get_json()
    username = data.get('username')
    email = data.get('email')
    password = data.get('password')

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
    data = request.get_json()
    email = data.get('email')
    password = data.get('password')

    usuario = Usuario.query.filter_by(email=email).first()
    if usuario and check_password_hash(usuario.password_hash, password):
        session['user_email'] = email
        return jsonify({'status': 'ok'})
    
    return jsonify({'status': 'error', 'message': 'Credenciales incorrectas.'}), 401

@app.route('/logout')
def logout():
    session.pop('user_email', None)
    return redirect(url_for('login_page'))

# --- RUTA PRINCIPAL DE CHAT CON STREAMING Y DETECCIÓN AUTOMÁTICA ---
@app.route('/chat_stream', methods=['POST'])
def chat_stream():
    if 'user_email' not in session:
        return jsonify({'error': 'No autorizado'}), 401

    data = request.get_json()
    user_message = data.get('message', '')
    target_lang = data.get('target_lang', 'Inglés')
    cefr_level = data.get('cefr_level', 'B1')
    mode = data.get('mode', 'conversation')

    # CONSTRUCCIÓN DEL PROMPT CON DETECCIÓN AUTOMÁTICA DE IDIOMA
    system_prompt = f"""
Eres Oralis, un tutor de idiomas inteligente, empático y experto.
- Idioma Objetivo de práctica del estudiante: {target_lang}.
- Nivel CEFR Objetivo del estudiante: {cefr_level}.
- Modo seleccionado: {mode}.

INSTRUCCIONES CLAVE DE DETECCIÓN Y ADAPTACIÓN:
1. DETECTA AUTOMÁTICAMENTE el idioma en el que el usuario te escribe o habla. No le pidas que especifique su idioma de origen.
2. Si el modo es 'translation': Traduce el texto directamente al {target_lang} respetando el nivel {cefr_level}, proporcionando además una breve explicación de vocabulario clave si es necesario.
3. Si el modo es 'grammar': Revisa el texto escrito por el usuario. Muestra primero la versión corregida en {target_lang} y luego explica brevemente los errores gramaticales cometidos.
4. Si el modo es 'pronunciation': Si el usuario escribió o dictó un texto, dale consejos de pronunciación, fonética aproximada y entonación para decir esa frase correctamente en {target_lang}.
5. Si el modo es 'conversation': Mantén una conversación natural en {target_lang} adaptada exactamente al nivel {cefr_level}. Si el usuario comete un error grave en su entrada, haz una pequeña aclaración amigable al final.
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
                max_tokens=1024,
                stream=True
            )
            for chunk in completion:
                content = chunk.choices[0].delta.content
                if content:
                    yield content
        except Exception as e:
            yield f"[Error al comunicarse con la IA: {str(e)}]"

    return Response(stream_with_context(generate()), content_type='text/plain; charset=utf-8')

# --- RUTA DE FEEDBACK DE USUARIOS ---
@app.route('/feedback', methods=['POST'])
def save_feedback():
    if 'user_email' not in session:
        return jsonify({'error': 'No autorizado'}), 401

    data = request.get_json()
    score = data.get('score', 5)
    comment = data.get('comment', '')

    nuevo_fb = Feedback(
        usuario_email=session['user_email'],
        puntuacion=int(score),
        comentario=comment
    )
    db.session.add(nuevo_fb)
    db.session.commit()

    return jsonify({'status': 'ok'})

# --- PANEL DE ADMINISTRACIÓN ---
@app.route('/admin/feedbacks')
def admin_feedbacks():
    user_email = session.get('user_email')
    if user_email != 'p75886777@gmail.com':
        return "Acceso denegado. Panel exclusivo para la administración de Oralis.", 403

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

if __name__ == '__main__':
    app.run(debug=True, port=5000)
