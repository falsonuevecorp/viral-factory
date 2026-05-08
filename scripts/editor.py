import os
import sys
import requests
import json
from moviepy.editor import VideoFileClip, TextClip, CompositeVideoClip
from openai import OpenAI
# Para Railway es mejor manejar variables de entorno
from dotenv import load_dotenv

load_dotenv()
# Necesitaremos tu OPENAI_API_KEY en las variables de Railway
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def process_video(input_path, output_path):
    print(f"🎬 ViralFactory Engine: Procesando {input_path}")
    
    try:
        # 1. Cargar video y extraer audio temporal
        clip = VideoFileClip(input_path)
        audio_temp = "temp_audio.mp3"
        clip.audio.write_audiofile(audio_temp, logger=None)
        
        # 2. Transcribir con OpenAI Whisper API (Infinitamente más ligero que local)
        print("🎙️ Transcribiendo con AI...")
        with open(audio_temp, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                model="whisper-1", 
                file=audio_file,
                response_format="verbose_json"
            )
        
        print(f"📝 Transcripción completada: {len(transcript.text)} caracteres.")
        
        # 3. Guardar metadatos para el editor visual
        # (Próximo paso: Generar subtítulos quemados en el video)
        
        # De momento, clonamos el video como prueba de vida exitosa
        clip.write_videofile(output_path, codec="libx264", audio_codec="aac", logger=None)
        
        # Limpieza
        if os.path.exists(audio_temp): os.remove(audio_temp)
        print(f"✅ Éxito total: {output_path}")

    except Exception as e:
        print(f"❌ Error en el motor: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) > 2:
        process_video(sys.argv[1], sys.argv[2])
    else:
        print("Error: Faltan rutas de entrada/salida.")
