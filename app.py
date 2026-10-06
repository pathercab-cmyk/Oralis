import os
import json
from flask import Flask, render_template, request, Response, redirect, url_for, session, jsonify
from groq import Groq

app = Flask(__name__)
# Clave secreta para la sesión de Flask
app.secret_key = os.getenv("FLASK_SECRET_KEY", "oralis_secret_key_change_in_production")

# Inicialización del cliente oficial de Groq
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "tu-api-key-de-groq-aqui")
client = Groq(api_key=GROQ_API_KEY)

# Modelo de Groq seleccionado
MODEL_NAME = "qwen/qwen3.8-27b"

# Archivo de persistencia de valoraciones/feedback
FEEDBACK_FILE = "feedback.json"


def get_system_prompt(target_lang, cefr_level, mode, rol_practica, examen_oficial, rubrica):
    """
    Construye el Prompt del Sistema completo adaptado a la configuración actual del usuario.
    """
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


@app.route("/")
def index():
    """Ruta principal que renderiza la interfaz."""
    if "user_name" not in session:
        session["user_name"] = "Estudiante"
    
    # Inicializa el historial de conversación en la sesión si no existe
    if "chat_history" not in session:
        session["chat_history"] = []
        
    return render_template("index.html", user_name=session["user_name"])


@app.route("/set_name", methods=["POST"])
def set_name():
    """Actualiza el nombre del usuario en la sesión actual."""
    name = request.form.get("user_name", "Estudiante").strip()
    session["user_name"] = name if name else "Estudiante"
    return redirect(url_for("index"))


@app.route("/clear_chat", methods=["POST"])
def clear_chat():
    """Limpia el historial de la conversación en la sesión."""
    session["chat_history"] = []
    return jsonify({"status": "ok", "message": "Historial reiniciado"})


@app.route("/logout")
def logout():
    """Cierra la sesión y limpia las variables."""
    session.clear()
    return redirect(url_for("index"))


@app.route("/chat_stream", methods=["POST"])
def chat_stream():
    """
    Endpoint principal de conversación con Groq (Streaming response).
    Mantiene historial de conversación, adjuntos y parámetros de exámenes/rúbricas.
    """
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

    # Construir prompt de sistema
    system_prompt = get_system_prompt(target_lang, cefr_level, mode, rol_practica, examen_oficial, rubrica)

    # Preparar el contenido del mensaje del usuario
    full_user_text = user_message
    if file_content:
        full_user_text += f"\n\n--- ARCHIVO ADJUNTO ({file_name}) ---\n{file_content}\n--- FIN ARCHIVO ---"

    # Recuperar o inicializar el historial de la conversación en sesión
    history = session.get("chat_history", [])

    # Construir el listado final de mensajes para el modelo
    messages = [{"role": "system", "content": system_prompt}]
    
    # Añadir historial previo
    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})
        
    # Añadir el mensaje actual del usuario
    messages.append({"role": "user", "content": full_user_text})

    def generate():
        full_response_text = ""
        try:
            # Llamada con streaming usando el SDK oficial de Groq
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=messages,
                stream=True,
                temperature=0.7
            )
            for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    full_response_text += content
                    yield content

            # Guardar la interacción en el historial de la sesión
            history.append({"role": "user", "content": full_user_text})
            history.append({"role": "assistant", "content": full_response_text})
            session["chat_history"] = history
            session.modified = True

        except Exception as e:
            yield f"[RESPUESTA_PRINCIPAL] Ocurrió un error al procesar la solicitud con Groq: {str(e)}"

    return Response(generate(), mimetype="text/plain; charset=utf-8")


@app.route("/feedback", methods=["POST"])
def feedback():
    """Registra las valoraciones del usuario en feedback.json."""
    data = request.get_json() or {}
    score = data.get("score")
    comment = data.get("comment", "").strip()
    user_name = session.get("user_name", "Anónimo")

    feedback_entry = {
        "user": user_name,
        "score": score,
        "comment": comment
    }

    feedbacks = []
    if os.path.exists(FEEDBACK_FILE):
        try:
            with open(FEEDBACK_FILE, "r", encoding="utf-8") as f:
                feedbacks = json.load(f)
        except Exception:
            feedbacks = []

    feedbacks.append(feedback_entry)

    try:
        with open(FEEDBACK_FILE, "w", encoding="utf-8") as f:
            json.dump(feedbacks, f, ensure_ascii=False, indent=4)
        return jsonify({"status": "ok", "message": "Feedback recibido correctamente"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
