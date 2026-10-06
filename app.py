
from flask import Flask, render_template, request, jsonify, Response, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
import os
import json
from flask import Flask, render_template, request, Response, jsonify
# ... otros imports ...

from groq import Groq  # Importamos la librería

# Pones la línea AQUÍ en tu archivo app.py (no en la terminal):
client = Groq(api_key="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")
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'tu_clave_secreta_aqui')

# --- CONFIGURACIÓN DE BASE DE DATOS (Ruta Absoluta) ---
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL') or \
    'sqlite:///' + os.path.join(basedir, 'oralis.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# --- MODELOS DE BASE DE DATOS ---
class Usuario(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    idioma = db.Column(db.String(10), default='de')

@login_manager.user_loader
def load_user(user_id):
    return Usuario.query.get(int(user_id))

# --- INICIALIZACIÓN DE BASE DE DATOS ---
with app.app_context():
    db.create_all()

# Almacenamiento temporal del historial por usuario e idioma en memoria
historiales_chat = {}

# Mapeo de códigos de idioma a nombres descriptivos
IDIOMAS_NOMBRES = {
    'en': 'Inglés',
    'de': 'Alemán',
    'fr': 'Francés',
    'nl': 'Neerlandés',
    'pt': 'Portugués',
    'ro': 'Rumano',
    'ja': 'Japonés',
    'zh': 'Chino Mandarín',
    'it': 'Italiano',
    'es': 'Español'
}

# --- CONSTRUCTOR DEL PROMPT BASE ---
def construir_prompt_base(idioma_code, nivel, tema, modo, prof_final, tipo_examen, metodo_writing):
    nombre_target = IDIOMAS_NOMBRES.get(idioma_code, 'Alemán')
    nombre_nativo = 'Español'

    prompt_base = f"""Eres Oralis, un tutor de inteligencia artificial pedagógico, dinámico y totalmente adaptable.
Idioma objetivo de aprendizaje actual: '{nombre_target}' (Nivel CEFR: '{nivel}').
Idioma nativo del usuario para traducciones y explicaciones: '{nombre_nativo}'.
Tema o contexto configurado: {tema if tema else 'Conversación o consulta general'}.

REGLA DE PRIORIDAD ABSOLUTA EN RESPUESTAS (MUY IMPORTANTE):
1. ATENCIÓN DIRECTA A LO QUE PIDE EL USUARIO:
   - Da igual en qué idioma se exprese el usuario (español, alemán, inglés, etc.):
   - Si el usuario solicita vocabulario, explicaciones gramaticales, traducciones, o hace una pregunta concreta (por ejemplo: "dame el vocabulario de la escuela" o "dame vocabulario de avión"), RESPONDE DIRECTAMENTE A SU PETICIÓN DE INMEDIATO.
   - PROHIBIDO saludar de forma genérica ("Hallo, ich bin Oralis...") o preguntar por su día si el usuario te ha hecho una petición clara. Entrega la información solicitada sin rodeos.
   - Solo debes hacer presentaciones o preguntas sobre su día si el usuario escribe únicamente un saludo simple (como "Hola", "Hallo") o no especifica ninguna petición.

DETECCIÓN DE IDIOMA Y FORMATO OBLIGATORIO:
- Tu respuesta principal o la lista de vocabulario/explicaciones solicitadas deben generarse en {nombre_target.upper()} (si el usuario pide explicaciones gramaticales complejas, usa {nombre_nativo.upper()}).
- Si el usuario comete errores en el idioma objetivo ({nombre_target}), indica la corrección en la sección de corrección.
- Incluye SIEMPRE la traducción exacta de tu respuesta principal al idioma nativo ({nombre_nativo.upper()}) al final.

ESTRUCTURA EXACTA DE SALIDA:
[Tu respuesta principal o el vocabulario en {nombre_target.upper()}]

---CORRECCION---
[Si el usuario cometió un fallo gramatical u ortográfico en {nombre_target}, indica brevemente el error y la versión correcta. Si no cometió errores, OMITIR por completo este bloque con su etiqueta.]

---TRADUCCION---
[Traducción exacta de tu mensaje principal al {nombre_nativo.upper()}]

REGLA AUTOMÁTICA PARA "MI CUADERNO":
Al final de todo tu mensaje (después de la traducción), añade las etiquetas de registro automático con el vocabulario enseñado o corregido:
[VOCABULARIO: Sustantivos - palabra1, palabra2]
[VOCABULARIO: Verbos - verbo1, verbo2]
[VOCABULARIO: Adjetivos - adjetivo1]
[VOCABULARIO: Frases - expresion1]
[GRAMATICA: Explicación breve de la regla aprendida]

FORMATO Y ESTILO:
1. Responde de forma pedagógica, clara y adaptada al nivel {nivel}.
2. PUEDES USAR MARKDOWN para resaltar conceptos importantes en negrita (ejemplo: **palabra**).
"""

    if modo == "practicas_orales":
        prompt_base += f"\nModo ACTIVO: Simulación de Rol o Práctica Oral. Adoptas el papel de: '{prof_final}'. Si el usuario te hace una petición directa de vocabulario o ayuda, atiéndela dentro o fuera de la simulación sin rigidez."
    elif modo == "examen":
        prompt_base += f"\nModo ACTIVO: Preparación de Examen Oficial ({tipo_examen}). Evalúa las respuestas o plantea ejercicios según lo solicitado."
    elif modo == "writing":
        if metodo_writing == "gramatica":
            prompt_base += f"""
Modo ACTIVO: Evaluador de Writing - Corrección Directa.
1. Analiza el texto enviado.
2. Muestra la versión corregida en {nombre_target}.
3. Explica los errores de forma detallada en {nombre_nativo}.
"""
        elif metodo_writing == "socratico":
            prompt_base += f"""
Modo ACTIVO: Evaluador de Writing - Método Socrático.
1. NO entregues la solución directamente.
2. Señala la frase con error y haz preguntas guía en {nombre_nativo} para que el alumno lo descubra.
"""

    return prompt_base


# --- RUTAS DE LA APLICACIÓN ---

@app.route('/')
@login_required
def index():
    return render_template('index.html', usuario=current_user)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        usuario = Usuario.query.filter_by(email=email).first()

        if usuario and bcrypt.check_password_hash(usuario.password_hash, password):
            login_user(usuario)
            return redirect(url_for('index'))
        else:
            flash('Credenciales incorrectas.', 'error')
    return render_template('login.html')

@app.route('/registro', methods=['GET', 'POST'])
def registro():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        
        if Usuario.query.filter_by(email=email).first():
            flash('El correo ya está registrado.', 'error')
            return render_template('registro.html')

        pw_hash = bcrypt.generate_password_hash(password).decode('utf-8')
        nuevo_usuario = Usuario(email=email, password_hash=pw_hash)
        db.session.add(nuevo_usuario)
        db.session.commit()
        
        login_user(nuevo_usuario)
        return redirect(url_for('index'))
    return render_template('registro.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


# --- RUTAS PARA HISTORIAL Y AUTO-GUARDADO DE CHAT POR IDIOMA ---

@app.route('/api/chat/historial/<idioma>', methods=['GET'])
@login_required
def obtener_historial(idioma):
    user_id = str(current_user.id)
    user_chats = historiales_chat.get(user_id, {})
    historial_idioma = user_chats.get(idioma, [])
    return jsonify({'historial': historial_idioma})

@app.route('/api/chat/guardar', methods=['POST'])
@login_required
def guardar_mensaje():
    data = request.json or {}
    user_id = str(current_user.id)
    idioma = data.get('idioma', 'de')
    mensaje = data.get('mensaje')

    if not mensaje:
        return jsonify({'status': 'error', 'msg': 'Mensaje vacío'}), 400

    if user_id not in historiales_chat:
        historiales_chat[user_id] = {}
    if idioma not in historiales_chat[user_id]:
        historiales_chat[user_id][idioma] = []

    historiales_chat[user_id][idioma].append(mensaje)
    return jsonify({'status': 'ok'})

@app.route('/api/chat/limpiar/<idioma>', methods=['POST'])
@login_required
def limpiar_historial(idioma):
    user_id = str(current_user.id)
    if user_id in historiales_chat and idioma in historiales_chat[user_id]:
        historiales_chat[user_id][idioma] = []
    return jsonify({'status': 'ok'})


# --- RUTA PRINCIPAL DEL CHAT (STREAMING CON MODELO IA REAL) ---

@app.route('/chat', methods=['POST'])
@login_required
def chat():
    mensaje_usuario = request.form.get('mensaje', '').strip()
    idioma = request.form.get('idioma', 'de')
    nivel = request.form.get('nivel', 'B1')
    modo = request.form.get('modo', 'tutor_original')
    metodo_writing = request.form.get('metodo_writing', 'gramatica')
    profesion = request.form.get('profesion', '')
    profesion_custom = request.form.get('profesion_custom', '')
    tipo_examen = request.form.get('tipo_examen', 'General')
    tema = request.form.get('tema', '')

    prof_final = profesion_custom if profesion == 'Otro' and profesion_custom else profesion
    nombre_idioma = IDIOMAS_NOMBRES.get(idioma, 'Alemán')

    system_prompt = construir_prompt_base(
        idioma_code=idioma,
        nivel=nivel,
        tema=tema,
        modo=modo,
        prof_final=prof_final,
        tipo_examen=tipo_examen,
        metodo_writing=metodo_writing
    )

    user_id = str(current_user.id)
    user_chats = historiales_chat.get(user_id, {})
    historial_previo = user_chats.get(idioma, [])

    mensajes_for_api = [{"role": "system", "content": system_prompt}]
    
    for m in historial_previo[-10:]:
        role = "user" if m.get('emisor') == 'user' else "assistant"
        mensajes_for_api.append({"role": role, "content": m.get('texto', '')})

    if mensaje_usuario:
        mensajes_for_api.append({"role": "user", "content": mensaje_usuario})

    def streamer():
        try:
            # Llamada en tiempo real a Groq (Llama 3.3 70B)
            completion = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=mensajes_for_api,
                temperature=0.7,
                stream=True
            )

            respuesta_completa = ""
            for chunk in completion:
                contenido = chunk.choices[0].delta.content or ""
                if contenido:
                    respuesta_completa += contenido
                    yield f"data: {json.dumps({'content': contenido})}\n\n"

            # Guardar la conversación real en el historial
            if user_id not in historiales_chat:
                historiales_chat[user_id] = {}
            if idioma not in historiales_chat[user_id]:
                historiales_chat[user_id][idioma] = []

            historiales_chat[user_id][idioma].append({'emisor': 'user', 'texto': mensaje_usuario})
            historiales_chat[user_id][idioma].append({'emisor': 'oralis', 'texto': respuesta_completa})

        except Exception as e:
            print(f"Error API: {e}")
            yield f"data: {json.dumps({'content': 'Error al procesar la solicitud con la IA.'})}\n\n"

    return Response(streamer(), mimetype='text/event-stream')


@app.route('/api/examenes/<idioma>', methods=['GET'])
def obtener_examenes(idioma):
    examenes = {
        'de': ['Goethe-Zertifikat B1/B2', 'TestDaF', 'telc Deutsch'],
        'en': ['Cambridge B2 First / C1 Advanced', 'IELTS Academic', 'TOEFL iBT'],
        'fr': ['DELF B1/B2', 'DALF C1', 'TCF'],
        'it': ['CELI', 'CILS'],
        'es': ['DELE B1/B2', 'SIELE']
    }
    return jsonify({'examenes': examenes.get(idioma, ['Examen Estándar'])})


if __name__ == '__main__':
    app.run(debug=True, port=5000)
