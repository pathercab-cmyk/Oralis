from flask import Flask, render_template, request, jsonify
from tutor import obtener_respuesta_tutor

app = Flask(__name__)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/chat", methods=["POST"])
def chat():
    datos = request.get_json()
    mensaje = datos.get("mensaje", "")
    idioma = datos.get("idioma", "en")
    nivel = datos.get("nivel", "B1")
    modo = datos.get("modo", "conversacion")

    if not mensaje:
        return jsonify({"error": "Mensaje vacío"}), 400

    respuesta_raw = obtener_respuesta_tutor(mensaje, idioma, nivel, modo)
    partes = respuesta_raw.split("|")

    if len(partes) >= 3:
        respuesta_texto = partes[0].strip()
        correccion = partes[1].strip()
        explicacion = partes[2].strip()
    else:
        respuesta_texto = respuesta_raw
        correccion = ""
        explicacion = ""

    return jsonify({
        "respuesta": respuesta_texto,
        "correccion": correccion,
        "explicacion": explicacion
    })

if __name__ == "__main__":
    app.run(debug=True)