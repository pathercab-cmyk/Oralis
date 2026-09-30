import os
from groq import Groq

def obtener_respuesta_tutor(mensaje_usuario, idioma="en", nivel="B1", modo="conversacion"):
    idioma_nombre = "Inglés" if idioma == "en" else "Alemán"
    
    if modo == "examen":
        prompt_sistema = f"""
Eres un Examinador Oficial certificado de {idioma_nombre} para exámenes internacionales (Cambridge, IELTS, TOEFL, Goethe-Zertifikat).
El candidato se examina del nivel {nivel}.

REGLAS COMO EXAMINADOR:
1. Simula una prueba oral/escrita real de examen oficial adaptada al nivel {nivel}.
2. Mantén un tono formal, evaluando fluidez, gramática, vocabulario avanzado y estructura.
3. NO uses marcado Markdown (nada de asteriscos *, almohadillas #, etc.).
4. Tu respuesta DEBE constar de 3 partes divididas exactamente por el carácter | :

PARTE 1: La siguiente pregunta o indicación del examen en {idioma_nombre}.
|
PARTE 2: Puntuación estimada (1-10) y corrección gramatical/estilística detallada de lo que respondió el usuario.
|
PARTE 3: Vocabulario/expresiones recomendadas para subir nota y traducción explicativa al español.
"""
    else:
        prompt_sistema = f"""
Eres LingoMind, un tutor nativo, paciente y profesional de {idioma_nombre}.
El estudiante tiene un nivel objetivo {nivel}.

REGLAS DE RESPUESTA:
1. Responde SIEMPRE en {idioma_nombre} adaptando la complejidad al nivel {nivel}.
2. NO uses marcado Markdown (nada de asteriscos *, almohadillas #, etc.).
3. Tu respuesta DEBE constar de 3 partes divididas exactamente por el carácter | :

PARTE 1: La respuesta conversacional natural en {idioma_nombre}.
|
PARTE 2: Corrección del mensaje del usuario en {idioma_nombre} (si tuvo errores) o versión mejorada.
|
PARTE 3: Explicación breve de la corrección y traducción al español de la respuesta.
"""

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        return "Error|No hay API Key|Configura la variable GROQ_API_KEY en tu entorno de PowerShell."

    client = Groq(api_key=api_key)

    # 1. Obtenemos automáticamente los modelos activos en la cuenta
    modelos_candidatos = []
    try:
        modelos_data = client.models.list().data
        for m in modelos_data:
            m_id = str(m.id).lower() if hasattr(m, 'id') else str(m).lower()
            # Filtramos modelos que no sirven para chat (audio, guard, visión, etc.)
            if not any(x in m_id for x in ["whisper", "guard", "vision"]):
                modelos_candidatos.append(m.id if hasattr(m, 'id') else str(m))
    except Exception:
        pass

    # Backup por si falla la llamada a client.models.list()
    if not modelos_candidatos:
        modelos_candidatos = ["llama3-8b-8192", "llama3-70b-8192"]

    # 2. Probamos conectar con el primer modelo válido
    ultimo_error = ""
    for modelo in modelos_candidatos:
        try:
            completion = client.chat.completions.create(
                model=modelo,
                messages=[
                    {"role": "system", "content": prompt_sistema},
                    {"role": "user", "content": mensaje_usuario}
                ],
                temperature=0.6,
                max_tokens=600
            )
            return completion.choices[0].message.content
        except Exception as e:
            ultimo_error = str(e)
            continue

    return f"Error|Ocurrió un fallo al conectar con la IA|Detalle del error: {ultimo_error}"