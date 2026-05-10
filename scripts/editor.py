import os
import sys
import requests
import json
from moviepy.editor import VideoFileClip, TextClip, CompositeVideoClip
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def create_styled_subtitle(text, duration, start_time, template_name, video_width, video_height):
    text = text.upper()
    
    # Text wrap width is 85% of video width
    text_width = int(video_width * 0.85)
    
    if template_name == "cinematic":
        # Posición más abajo (90% de la altura), texto elegante
        y_pos = int(video_height * 0.85)
        font_size = int(video_width * 0.05) # 5% del ancho
        return TextClip(
            text,
            fontsize=font_size,
            color='white',
            font='Liberation-Sans',
            method='caption',
            size=(text_width, None)
        ).set_start(start_time).set_duration(duration).set_position(('center', y_pos))
        
    elif template_name == "neon":
        # Posición clásica (80% de la altura)
        y_pos = int(video_height * 0.80)
        font_size = int(video_width * 0.07) # 7% del ancho
        return TextClip(
            text,
            fontsize=font_size,
            color='#00f0ff',
            font='Liberation-Sans-Bold',
            method='caption',
            size=(text_width, None)
        ).set_start(start_time).set_duration(duration).set_position(('center', y_pos))
        
    else:
        # Default: hormozi (Posición al 75% para impactar, letras más grandes)
        y_pos = int(video_height * 0.75)
        font_size = int(video_width * 0.09) # 9% del ancho
        stroke_w = max(1, int(font_size * 0.04))
        return TextClip(
            text,
            fontsize=font_size,
            color='yellow',
            font='Liberation-Sans-Bold',
            stroke_color='black',
            stroke_width=stroke_w,
            method='caption',
            size=(text_width, None)
        ).set_start(start_time).set_duration(duration).set_position(('center', y_pos))

def process_video(input_path, output_path, template_name="hormozi"):
    print(f"🎬 ViralFactory Engine: Procesando {input_path} con plantilla: {template_name}")
    
    try:
        from moviepy.video.fx.all import colorx, lum_contrast, gamma_corr

        clip = VideoFileClip(input_path)
        
        # --- APLICAR FILTROS DE COLOR (LUT SIMULADOS) ---
        print(f"✨ Aplicando corrección de color para template: {template_name}...")
        if template_name == "cinematic":
            # Cinematic: Más contraste, un poco más oscuro, tonos más fríos
            clip = clip.fx(lum_contrast, lum=0, contrast=0.2, contrast_thr=127)
            clip = clip.fx(gamma_corr, gamma=1.1)
        elif template_name == "neon":
            # Neon: Alto contraste, vibrante, ligeramente sobreexpuesto
            clip = clip.fx(lum_contrast, lum=10, contrast=0.3, contrast_thr=127)
            clip = clip.fx(colorx, 1.2) # Saturación/Brillo general aumentado
        else:
            # Hormozi: Colores muy vivos, brillantes y alto contraste para captar atención
            clip = clip.fx(colorx, 1.15)
            clip = clip.fx(lum_contrast, lum=15, contrast=0.15, contrast_thr=127)

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
        video_w, video_h = clip.size
        
        for segment in transcript.segments:
            # En la versión nueva de OpenAI (>=1.0.0), los segmentos son objetos, no diccionarios
            txt = segment.text if hasattr(segment, 'text') else segment['text']
            start = segment.start if hasattr(segment, 'start') else segment['start']
            end = segment.end if hasattr(segment, 'end') else segment['end']
            
            # Skip empty segments
            if not txt or not txt.strip():
                continue
                
            sub = create_styled_subtitle(txt, end - start, start, template_name, video_w, video_h)
            subtitles.append(sub)
        
        if not subtitles:
            raise ValueError("No se detectó ninguna voz o diálogo en el video. Sube un video donde alguien esté hablando.")
            
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
