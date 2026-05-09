import os
import sys
import requests
import json
from moviepy.editor import VideoFileClip, TextClip, CompositeVideoClip
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def create_styled_subtitle(text, duration, start_time, template_name="hormozi"):
    text = text.upper()
    
    if template_name == "cinematic":
        return TextClip(
            text,
            fontsize=50,
            color='white',
            font='Liberation-Sans',
            method='caption',
            size=(800, None)
        ).set_start(start_time).set_duration(duration).set_position(('center', 850))
        
    elif template_name == "neon":
        return TextClip(
            text,
            fontsize=65,
            color='#00f0ff',
            font='Liberation-Sans-Bold',
            method='caption',
            size=(800, None)
        ).set_start(start_time).set_duration(duration).set_position(('center', 800))
        
    else:
        # Default: hormozi
        return TextClip(
            text,
            fontsize=75,
            color='yellow',
            font='Liberation-Sans-Bold',
            stroke_color='black',
            stroke_width=3,
            method='caption',
            size=(800, None)
        ).set_start(start_time).set_duration(duration).set_position(('center', 800))

def process_video(input_path, output_path, template_name="hormozi"):
    print(f"🎬 ViralFactory Engine: Procesando {input_path} con plantilla: {template_name}")
    
    try:
        clip = VideoFileClip(input_path)
        
        if clip.audio is None:
            raise ValueError("El video subido no tiene pista de audio. No se pueden generar subtítulos.")
            
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
            sub = create_styled_subtitle(txt, end - start, start, template_name)
            subtitles.append(sub)
        
        print(f"🎨 Aplicando {len(subtitles)} subtítulos estilo '{template_name}'...")
        
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
        template = sys.argv[3] if len(sys.argv) > 3 else "hormozi"
        process_video(sys.argv[1], sys.argv[2], template)
    else:
        print("Error: Faltan rutas de entrada/salida.")
