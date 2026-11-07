"""
ViralFactory Engine — editor de video "pro" basado en FFmpeg.

Pipeline:
  1. Analiza el video (ffprobe).
  2. Transcribe con timestamps por PALABRA (OpenAI Whisper API, o whisper local si no hay API key).
  3. Jump cuts: elimina silencios / tiempos muertos entre frases.
  4. Punch-in zoom alterno en cada corte (ritmo de short viral).
  5. Micro-fades de audio en cada corte (sin "clicks").
  6. Etalonaje por template (curvas, balance de color, viñeta, grano).
  7. Subtítulos karaoke ASS: palabra activa resaltada con animación "pop".
  8. Audio masterizado: high-pass, compresor y normalización a -14 LUFS (estándar de redes).
  9. Export H.264 CRF 18, yuv420p, +faststart (listo para TikTok / Reels / Shorts).

Uso (compatible con el servidor):
  python3 editor.py <input> <output> [template] [--aspect original|9:16|1:1] [--no-cuts]
                    [--transcript-json archivo.json]
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Configuración por template
# ---------------------------------------------------------------------------
TEMPLATES = {
    "hormozi": {
        "max_gap": 0.45,          # silencios más largos que esto se cortan
        "punch_in": 1.12,         # zoom en cortes alternos
        "words_per_chunk": 3,
        "max_chars": 18,
        "uppercase": True,
        "grade": "eq=contrast=1.08:saturation=1.30:brightness=0.02,unsharp=5:5:0.6:5:5:0.0",
    },
    "neon": {
        "max_gap": 0.5,
        "punch_in": 1.10,
        "words_per_chunk": 3,
        "max_chars": 20,
        "uppercase": True,
        "grade": "eq=contrast=1.15:saturation=1.45:gamma=0.95,"
                 "colorbalance=bs=0.12:bm=0.05:rh=0.05,vignette=PI/5",
    },
    "cinematic": {
        "max_gap": 0.9,           # más aire, ritmo más pausado
        "punch_in": None,
        "words_per_chunk": 7,
        "max_chars": 42,
        "uppercase": False,
        "grade": "curves=preset=medium_contrast,"
                 "colorbalance=rs=-0.08:bs=0.10:rh=0.08:bh=-0.08,"
                 "eq=saturation=0.9,vignette=PI/4,noise=alls=6:allf=t",
    },
}

PAD_IN = 0.12     # aire antes de la primera palabra de cada toma
PAD_OUT = 0.22    # aire después de la última palabra
MAX_SEGMENTS = 80
AUDIO_FADE = 0.012


def log(msg):
    print(msg, flush=True)


def run(cmd):
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-25:])
        raise RuntimeError(f"Fallo en {os.path.basename(cmd[0])}:\n{tail}")
    return proc.stdout


def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


# ---------------------------------------------------------------------------
# 1. Análisis
# ---------------------------------------------------------------------------
def probe(path):
    data = json.loads(run([
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_streams", "-show_format", path,
    ]))
    video = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    audio = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    if video is None:
        raise ValueError("El archivo no contiene pista de video.")

    w, h = int(video["width"]), int(video["height"])
    rotation = 0
    for sd in video.get("side_data_list", []) or []:
        if "rotation" in sd:
            rotation = abs(int(sd["rotation"]))
    rotation = rotation or abs(int((video.get("tags") or {}).get("rotate", 0)))
    if rotation in (90, 270):
        w, h = h, w

    num, den = (video.get("avg_frame_rate") or "30/1").split("/")
    fps = float(num) / float(den) if float(den) else 30.0
    if not 10 <= fps <= 60:
        fps = 30.0

    duration = float(data["format"].get("duration") or video.get("duration") or 0)
    return {"w": w, "h": h, "fps": round(fps, 3), "duration": duration, "has_audio": audio is not None}


# ---------------------------------------------------------------------------
# 2. Transcripción con timestamps por palabra
# ---------------------------------------------------------------------------
def _words_from_segments(segments):
    """Si sólo hay segmentos, reparte el tiempo de cada uno entre sus palabras."""
    words = []
    for seg in segments:
        text = (_get(seg, "text") or "").strip()
        tokens = text.split()
        if not tokens:
            continue
        start, end = float(_get(seg, "start")), float(_get(seg, "end"))
        step = (end - start) / len(tokens)
        for i, tok in enumerate(tokens):
            words.append({"word": tok, "start": start + i * step, "end": start + (i + 1) * step})
    return words


def transcribe(audio_path):
    if os.getenv("OPENAI_API_KEY"):
        from openai import OpenAI
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        with open(audio_path, "rb") as f:
            t = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                response_format="verbose_json",
                timestamp_granularities=["word", "segment"],
            )
        words = [{"word": _get(w, "word"), "start": float(_get(w, "start")), "end": float(_get(w, "end"))}
                 for w in (_get(t, "words") or [])]
        return words or _words_from_segments(_get(t, "segments") or [])

    log("ℹ️  Sin OPENAI_API_KEY: usando Whisper local...")
    import whisper
    model = whisper.load_model(os.getenv("WHISPER_MODEL", "base"))
    result = model.transcribe(audio_path, word_timestamps=True)
    words = [{"word": w["word"], "start": float(w["start"]), "end": float(w["end"])}
             for seg in result.get("segments", []) for w in seg.get("words", [])]
    return words or _words_from_segments(result.get("segments", []))


def clean_words(words):
    out = []
    for w in words:
        text = (w.get("word") or "").strip()
        if not text or w["end"] <= w["start"] - 1e-3:
            continue
        out.append({"word": text, "start": float(w["start"]), "end": max(float(w["end"]), float(w["start"]) + 0.05)})
    out.sort(key=lambda w: w["start"])
    return out


# ---------------------------------------------------------------------------
# 3. Jump cuts
# ---------------------------------------------------------------------------
def compute_keep_segments(words, duration, max_gap):
    segments = []
    for w in words:
        s, e = max(0.0, w["start"] - PAD_IN), min(duration, w["end"] + PAD_OUT)
        if segments and s - segments[-1][1] <= max_gap:
            segments[-1][1] = max(segments[-1][1], e)
        else:
            segments.append([s, e])
    return [tuple(s) for s in segments if s[1] - s[0] > 0.08]


def limit_segments(words, duration, max_gap):
    segments = compute_keep_segments(words, duration, max_gap)
    while len(segments) > MAX_SEGMENTS:
        max_gap *= 1.5
        segments = compute_keep_segments(words, duration, max_gap)
    return segments


def remap_words(words, segments):
    """Traslada los timestamps de las palabras a la nueva línea de tiempo (post-cortes)."""
    remapped, offset, idx = [], 0.0, 0
    offsets = []
    for s, e in segments:
        offsets.append(offset)
        offset += e - s
    prev_idx = -1
    for w in words:
        while idx < len(segments) - 1 and w["start"] >= segments[idx][1]:
            idx += 1
        s, e = segments[idx]
        if not (s - 1e-3 <= w["start"] <= e):
            continue
        ns = offsets[idx] + (w["start"] - s)
        ne = offsets[idx] + (min(w["end"], e) - s)
        remapped.append({**w, "start": ns, "end": max(ne, ns + 0.05), "cut": idx != prev_idx and prev_idx >= 0})
        prev_idx = idx
    return remapped, offset


# ---------------------------------------------------------------------------
# 7. Subtítulos ASS karaoke
# ---------------------------------------------------------------------------
def ass_time(t):
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def ass_escape(text):
    return text.replace("\\", "").replace("{", "(").replace("}", ")")


def caption_text(word, cfg):
    text = word
    if cfg["uppercase"]:
        text = re.sub(r"[.,;:…]+$", "", text).upper()
    return ass_escape(text)


def chunk_words(words, cfg):
    chunks, current = [], []
    for w in words:
        if current:
            chars = sum(len(x["word"]) + 1 for x in current) + len(w["word"])
            gap = w["start"] - current[-1]["end"]
            if (len(current) >= cfg["words_per_chunk"] or chars > cfg["max_chars"]
                    or gap > 0.5 or w.get("cut") or re.search(r"[.!?…]$", current[-1]["word"])):
                chunks.append(current)
                current = []
        current.append(w)
    if current:
        chunks.append(current)
    return chunks


def build_ass(words, template, out_w, out_h, total):
    cfg = TEMPLATES[template]
    ref = min(out_w, out_h)

    if template == "cinematic":
        size = int(ref * 0.055)
        styles = (f"Style: Main,Liberation Sans,{size},&H00FFFFFF,&H70FFFFFF,&H00000000,&H90000000,"
                  f"0,0,0,0,100,100,1,0,1,{max(1, size * 0.04):.1f},{max(1, size * 0.06):.1f},2,"
                  f"{int(out_w * 0.08)},{int(out_w * 0.08)},{int(out_h * 0.08)},1")
    elif template == "neon":
        size = int(ref * 0.075)
        styles = (f"Style: Main,Liberation Sans,{size},&H00F5F5F5,&H00F5F5F5,&H00FFF000,&H00000000,"
                  f"-1,0,0,0,100,100,2,0,1,{max(2, size * 0.05):.1f},0,2,"
                  f"{int(out_w * 0.07)},{int(out_w * 0.07)},{int(out_h * 0.20)},1")
    else:  # hormozi
        size = int(ref * 0.09)
        styles = (f"Style: Main,Liberation Sans,{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,"
                  f"-1,0,0,0,100,100,1,0,1,{max(3, size * 0.09):.1f},{max(2, size * 0.04):.1f},2,"
                  f"{int(out_w * 0.07)},{int(out_w * 0.07)},{int(out_h * 0.25)},1")

    header = (
        "[Script Info]\nScriptType: v4.00+\nWrapStyle: 0\nScaledBorderAndShadow: yes\n"
        f"PlayResX: {out_w}\nPlayResY: {out_h}\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"{styles}\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    events = []
    chunks = chunk_words(words, cfg)
    for ci, chunk in enumerate(chunks):
        next_start = chunks[ci + 1][0]["start"] if ci + 1 < len(chunks) else total
        chunk_end = min(next_start, chunk[-1]["end"] + 0.35)

        if template == "cinematic":
            # Karaoke nativo (\k): las palabras pasan de tenues a blancas al ser dichas.
            parts, cursor = [], chunk[0]["start"]
            for w in chunk:
                dur_cs = max(1, int(round((w["end"] - cursor) * 100)))
                parts.append(f"{{\\k{dur_cs}}}{caption_text(w['word'], cfg)}")
                cursor = w["end"]
            text = "{\\fad(150,150)}" + " ".join(parts)
            events.append(f"Dialogue: 0,{ass_time(chunk[0]['start'])},{ass_time(chunk_end)},Main,,0,0,0,,{text}")
            continue

        for wi, w in enumerate(chunk):
            start = w["start"]
            end = chunk[wi + 1]["start"] if wi + 1 < len(chunk) else chunk_end
            if end <= start:
                continue
            pieces = []
            for oj, other in enumerate(chunk):
                word = caption_text(other["word"], cfg)
                if oj == wi:
                    if template == "neon":
                        active = "{\\c&H00FFF000&\\3c&H00FFF000&\\blur4\\fscx112\\fscy112\\t(0,100,\\fscx100\\fscy100)}"
                    else:
                        active = "{\\c&H0000FFFF&\\fscx125\\fscy125\\t(0,90,\\fscx110\\fscy110)}"
                    pieces.append(f"{active}{word}{{\\r}}")
                else:
                    dim = "{\\alpha&H40&}" if template == "neon" else ""
                    pieces.append(f"{dim}{word}{{\\r}}" if dim else word)
            intro = "{\\fscx90\\fscy90\\t(0,80,\\fscx100\\fscy100)}" if wi == 0 else ""
            events.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Main,,0,0,0,,{intro}{' '.join(pieces)}")

    return header + "\n".join(events) + "\n"


# ---------------------------------------------------------------------------
# Filtros FFmpeg
# ---------------------------------------------------------------------------
def even(x):
    return max(2, int(x) // 2 * 2)


def filter_path(p):
    return p.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def output_dims(w, h, aspect):
    if aspect == "9:16":
        ow, oh = (w, int(w * 16 / 9)) if w / h < 9 / 16 else (int(h * 9 / 16), h)
    elif aspect == "1:1":
        ow = oh = min(w, h)
    else:
        return (even(w), even(h)), (even(w), even(h))
    # Formato redes: recorte centrado y escalado a resolución estándar (1080 de ancho).
    final = (1080, 1920) if aspect == "9:16" else (1080, 1080)
    return (even(ow), even(oh)), final


def build_filtergraph(segments, info, cfg, crop_dims, ass_path, out_w, out_h):
    W, H = even(info["w"]), even(info["h"])
    n = len(segments)
    lines = [
        f"[0:v]split={n}" + "".join(f"[vs{i}]" for i in range(n)),
        f"[0:a]asplit={n}" + "".join(f"[as{i}]" for i in range(n)),
    ]
    concat_in = ""
    for i, (s, e) in enumerate(segments):
        zoom = cfg["punch_in"] if (cfg["punch_in"] and i % 2 == 1) else None
        if zoom:
            vfx = f"scale={even(W * zoom)}:{even(H * zoom)},crop={W}:{H}"
        else:
            vfx = f"scale={W}:{H}"
        dur = e - s
        lines.append(f"[vs{i}]trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS,{vfx},setsar=1[v{i}]")
        lines.append(
            f"[as{i}]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS,"
            f"afade=t=in:d={AUDIO_FADE},afade=t=out:st={max(0, dur - AUDIO_FADE):.3f}:d={AUDIO_FADE}[a{i}]"
        )
        concat_in += f"[v{i}][a{i}]"
    lines.append(f"{concat_in}concat=n={n}:v=1:a=1[cv][ca]")

    reframe = ""
    if crop_dims != (W, H):
        reframe += f"crop={crop_dims[0]}:{crop_dims[1]},"
    if (out_w, out_h) != crop_dims:
        reframe += f"scale={out_w}:{out_h}:flags=lanczos,setsar=1,"
    lines.append(
        f"[cv]{reframe}{cfg['grade']},ass='{filter_path(ass_path)}',"
        f"fps={info['fps']},format=yuv420p[vout]"
    )
    lines.append(
        "[ca]highpass=f=80,"
        "acompressor=threshold=-18dB:ratio=3:attack=5:release=120:makeup=2,"
        "loudnorm=I=-14:TP=-1.0:LRA=11,aresample=48000[aout]"
    )
    return ";\n".join(lines)


# ---------------------------------------------------------------------------
# Pipeline principal
# ---------------------------------------------------------------------------
def process_video(input_path, output_path, template="hormozi", aspect="original",
                  cuts=True, transcript_json=None):
    if template not in TEMPLATES:
        log(f"⚠️  Template '{template}' desconocido, usando 'hormozi'.")
        template = "hormozi"
    cfg = TEMPLATES[template]
    log(f"🎬 ViralFactory Engine PRO: {input_path} | template: {template} | formato: {aspect}")

    work = tempfile.mkdtemp(prefix="vf_")
    try:
        info = probe(input_path)
        log(f"📐 {info['w']}x{info['h']} @ {info['fps']}fps · {info['duration']:.1f}s")
        if not info["has_audio"]:
            raise ValueError("El video subido no tiene pista de audio. No se pueden generar subtítulos.")

        if transcript_json:
            with open(transcript_json) as f:
                words = json.load(f)
        else:
            audio_path = os.path.join(work, "audio.mp3")
            run(["ffmpeg", "-y", "-v", "error", "-i", input_path, "-vn", "-ac", "1", "-ar", "16000",
                 "-b:a", "64k", audio_path])
            log("🎙️ Transcribiendo con timestamps por palabra...")
            words = transcribe(audio_path)

        words = clean_words(words)
        if not words:
            raise ValueError("No se detectó ninguna voz o diálogo en el video. Sube un video donde alguien esté hablando.")
        log(f"📝 {len(words)} palabras detectadas.")

        if cuts:
            segments = limit_segments(words, info["duration"], cfg["max_gap"])
        else:
            segments = [(0.0, info["duration"])]
        new_words, new_total = remap_words(words, segments)
        removed = info["duration"] - new_total
        log(f"✂️  {len(segments)} tomas · {removed:.1f}s de silencios eliminados "
            f"({info['duration']:.1f}s → {new_total:.1f}s)")

        crop_dims, (out_w, out_h) = output_dims(even(info["w"]), even(info["h"]), aspect)
        ass_path = os.path.join(work, "subs.ass")
        with open(ass_path, "w", encoding="utf-8") as f:
            f.write(build_ass(new_words, template, out_w, out_h, new_total))
        log("🎨 Subtítulos karaoke generados.")

        graph_path = os.path.join(work, "graph.txt")
        with open(graph_path, "w") as f:
            f.write(build_filtergraph(segments, info, cfg, crop_dims, ass_path, out_w, out_h))

        log("🎞️  Render: cortes + zooms + etalonaje + subtítulos + master de audio...")
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        run([
            "ffmpeg", "-y", "-v", "error", "-i", input_path,
            "-filter_complex_script", graph_path,
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", os.getenv("VF_PRESET", "medium"), "-crf", "18",
            "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            output_path,
        ])
        log(f"✅ Éxito total: {output_path} ({out_w}x{out_h}, {new_total:.1f}s)")
    except Exception as e:
        log(f"❌ Error en el motor: {e}")
        sys.exit(1)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description="ViralFactory Engine PRO")
    parser.add_argument("input")
    parser.add_argument("output")
    parser.add_argument("template", nargs="?", default="hormozi")
    parser.add_argument("--aspect", choices=["original", "9:16", "1:1"], default=os.getenv("VF_ASPECT", "original"))
    parser.add_argument("--no-cuts", action="store_true", help="No eliminar silencios")
    parser.add_argument("--transcript-json", help="Usar una transcripción [{word,start,end}] en vez de Whisper")
    args = parser.parse_args()
    process_video(args.input, args.output, args.template, args.aspect,
                  cuts=not args.no_cuts, transcript_json=args.transcript_json)


if __name__ == "__main__":
    main()
