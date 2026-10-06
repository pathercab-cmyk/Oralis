import os
import json
from flask import Flask, render_template, request, Response, redirect, url_for, session, jsonify
from groq import Groq

app = Flask(__name__)
# Clave secreta para la gestión de sesiones
app.secret_key = os.getenv("FLASK_SECRET_KEY", "oralis_secret_key_change_in_production")

# Inicialización del cliente de Groq
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "tu-api-key-de-groq-aqui")
client = Groq(api_key=GROQ_API_KEY)

# Modelo predeterminado de Groq
MODEL_NAME = "qwen/qwen3.8-27b"

# Archivos de persistencia local
FEEDBACK_FILE = "feedback.json"
NOTEBOOK_FILE = "notebook.json"


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
# RUTAS DE NAVEGACIÓN Y SESIÓN
# -------------------------------------------------------------------

@app.route("/")
def index():
    if "user_name" not in session:
        session["user_name"] = "Estudiante"
    if "chat_history" not in session:
        session["chat_history"] = []
    return render_template("index.html", user_name=session["user_name"])


@app.route("/set_name", methods=["POST"])
def set_name():
    name = request.form.get("user_name", "Estudiante").strip()
    session["user_name"] = name if name else "Estudiante"
    return redirect(url_for("index"))


@app.route("/clear_chat", methods=["POST"])
def clear_chat():
    session["chat_history"] = []
    session.modified = True
    return jsonify({"status": "ok", "message": "Historial reiniciado"})


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


# -------------------------------------------------------------------
# CHAT Y STREAMING CON GROQ
# -------------------------------------------------------------------

@app.route("/chat_stream", methods=["POST"])
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
        full_response_text = ""
        try:
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

            # Guardar en el historial de sesión (se limita a las últimas 20 interacciones)
            history.append({"role": "user", "content": full_user_text})
            history.append({"role": "assistant", "content": full_response_text})
            session["chat_history"] = history[-20:]
            session.modified = True

        except Exception as e:
            yield f"[RESPUESTA_PRINCIPAL] Ocurrió un error al procesar la solicitud con Groq: {str(e)}"

    return Response(generate(), mimetype="text/plain; charset=utf-8")


# -------------------------------------------------------------------
# MI CUADERNO (VOCABULARIO Y GRAMÁTICA)
# -------------------------------------------------------------------

@app.route("/get_notebook", methods=["GET"])
def get_notebook():
    user_name = session.get("user_name", "Estudiante")
    notebook_data = {"vocabulary": [], "grammar": []}
    
    if os.path.exists(NOTEBOOK_FILE):
        try:
            with open(NOTEBOOK_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                notebook_data = data.get(user_name, {"vocabulary": [], "grammar": []})
        except Exception:
            pass

    return jsonify({"status": "ok", "notebook": notebook_data})


@app.route("/save_notebook", methods=["POST"])
def save_notebook():
    data = request.get_json() or {}
    user_name = session.get("user_name", "Estudiante")
    
    item_type = data.get("type")  # 'vocabulary' o 'grammar'
    content = data.get("content", "").strip()

    if not content or item_type not in ["vocabulary", "grammar"]:
        return jsonify({"status": "error", "message": "Datos inválidos"}), 400

    all_notebooks = {}
    if os.path.exists(NOTEBOOK_FILE):
        try:
            with open(NOTEBOOK_FILE, "r", encoding="utf-8") as f:
                all_notebooks = json.load(f)
        except Exception:
            all_notebooks = {}

    user_data = all_notebooks.get(user_name, {"vocabulary": [], "grammar": []})
    
    if content not in user_data[item_type]:
        user_data[item_type].append(content)

    all_notebooks[user_name] = user_data

    try:
        with open(NOTEBOOK_FILE, "w", encoding="utf-8") as f:
            json.dump(all_notebooks, f, ensure_ascii=False, indent=4)
        return jsonify({"status": "ok", "message": "Guardado en Mi Cuaderno"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# -------------------------------------------------------------------
# FEEDBACK Y PANEL DE ADMINISTRACIÓN
# -------------------------------------------------------------------

@app.route("/feedback", methods=["POST"])
def feedback():
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
        return jsonify({"status": "ok", "message": "Feedback recibido"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/admin/feedback", methods=["GET"])
def admin_feedback():
    feedbacks = []
    if os.path.exists(FEEDBACK_FILE):
        try:
            with open(FEEDBACK_FILE, "r", encoding="utf-8") as f:
                feedbacks = json.load(f)
        except Exception:
            feedbacks = []
            
    return render_template("admin_feedback.html", feedbacks=feedbacks)


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
