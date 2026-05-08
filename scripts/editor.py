import os
import sys
import requests
import json
from moviepy.editor import VideoFileClip, TextClip, CompositeVideoClip
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def create_styled_subtitle(text, duration, start_time):
    # Estilo 'Elite': Fuente Inter/Roboto, Bold, Amarillo/Blanco, Sombra Negra
    return TextClip(
        text.upper(),
        fontsize=70,
        color='yellow',
        font='Liberation-Sans-Bold',
        stroke_color='black',
        stroke_width=2,
        method='caption',
        size=(800, None)
    ).set_start(start_time).set_duration(duration).set_position(('center', 800))

def process_video(input_path, output_path):
    print(f"🎬 ViralFactory Engine: Procesando {input_path}")
    
    try:
        clip = VideoFileClip(input_path)
        audio_temp = f"temp_audio_{os.path.basename(output_path)}.mp3"
        clip.audio.write_audiofile(audio_temp, logger=None)
        
        print("🎙️ Transcribiendo con AI y obteniendo timestamps...")
        with open(audio_temp, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                model="whisper-1", 
                file=audio_file,
                response_format="verbose_json"
            )
        
        # 3. Generar Subtítulos por Segmentos
        subtitles = []
        for segment in transcript.segments:
            # En la versión nueva de OpenAI (>=1.0.0), los segmentos son objetos, no diccionarios
            txt = segment.text if hasattr(segment, 'text') else segment['text']
            start = segment.start if hasattr(segment, 'start') else segment['start']
            end = segment.end if hasattr(segment, 'end') else segment['end']
            sub = create_styled_subtitle(txt, end - start, start)
            subtitles.append(sub)
        
        print(f"🎨 Aplicando {len(subtitles)} subtítulos estilo 'Viral Elite'...")
        
        # Superponer subtítulos al video original
        final_video = CompositeVideoClip([clip] + subtitles)
        
        # Exportar con ajustes de alta calidad
        final_video.write_videofile(output_path, codec="libx264", audio_codec="aac", logger=None, fps=24)
        
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
