"""
ViralFactory Engine PRO — editor de video automático basado en FFmpeg.

Aplica las técnicas de retención de los shorts virales:
  1. HOOK: los primeros 0.7s arrancan con un zoom de impacto + golpe de sonido.
  2. Transcripción con timestamps por PALABRA (Whisper API, o whisper local sin API key).
  3. Jump cuts: elimina silencios y tiempos muertos.
  4. Pattern interrupts: cambio de encuadre (punch-in) cada ~3s y en palabras clave.
  5. Transiciones profesionales (zoom, flash, whip/smooth, glitch-pixel, dissolve) sólo en
     cortes reales de cambio de idea, nunca en exceso.
  6. Sound design: whoosh en transiciones e impacto en flashes y en el hook.
  7. Música de fondo por mood (emocional / motivacional / oscura) con ducking automático
     bajo la voz. Usa tus pistas en music/<mood>/ o genera una pista libre de derechos.
  8. Subtítulos karaoke ASS con palabra activa animada y palabras clave en otro color.
  9. Barra de progreso (incentiva ver el video completo).
 10. Etalonaje por template, master de audio a -14 LUFS, export H.264 CRF 18 + faststart.

Uso (compatible con el servidor):
  python3 editor.py <input> <output> [template]
        [--aspect original|9:16|1:1] [--music auto|none|<archivo>] [--no-cuts]
        [--no-sfx] [--transcript-json archivo.json]
"""
import argparse
import array
import glob
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import wave

from dotenv import load_dotenv

load_dotenv()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SR = 48000

# ---------------------------------------------------------------------------
# Templates: cada uno es un "estilo de editor" completo
# ---------------------------------------------------------------------------
TEMPLATES = {
    # Estilo "creador educativo": subtítulos pequeños y limpios, color natural, cortes secos
    "minimal": {
        "max_gap": 0.4,
        "zooms": [1.0, 1.08],                  # punch-in sutil al alternar planos
        "emphasis_zoom": None,
        "max_piece": 4.0,
        "transitions": [],                     # sólo cortes secos
        "trans_dur": 0.0,
        "trans_min_gap": 99,
        "trans_spacing": 99,
        "hook_zoom": None,
        "progress": None,
        "mood": "emocional",
        "sfx_gain": 0.0,
        "words_per_chunk": 3,
        "max_chars": 22,
        "uppercase": False,
        "highlight": None,
        "emphasis": None,
        # look natural cálido, contraste suave con negros ligeramente levantados
        "grade": "curves=m='0/0.02 0.5/0.5 1/0.98':r='0/0 0.5/0.51 1/1':b='0/0 0.5/0.49 1/0.98',"
                 "eq=saturation=0.95",
    },
    "hormozi": {
        "max_gap": 0.45,                       # silencios más largos se cortan
        "zooms": [1.0, 1.14, 1.0, 1.07],       # encuadres que se alternan
        "emphasis_zoom": 1.22,                 # punch-in fuerte en palabras clave
        "max_piece": 3.0,                      # pattern interrupt cada ~3s
        "transitions": ["zoomin", "fadewhite", "smoothleft"],
        "trans_dur": 0.18,
        "trans_min_gap": 0.7,                  # sólo si el silencio cortado era una pausa de cambio de idea
        "trans_spacing": 4.0,                  # nunca más de una transición cada 4s
        "hook_zoom": 1.30,
        "progress": "0xFFD400",
        "mood": "motivacional",
        "sfx_gain": 0.55,
        "words_per_chunk": 3,
        "max_chars": 18,
        "uppercase": True,
        "highlight": "&H0000D4FF&",           # amarillo (BGR)
        "emphasis": "&H0055FF2E&",            # verde
        "grade": "eq=contrast=1.08:saturation=1.30:brightness=0.02,unsharp=5:5:0.6:5:5:0.0",
    },
    "neon": {
        "max_gap": 0.5,
        "zooms": [1.0, 1.10],
        "emphasis_zoom": 1.18,
        "max_piece": 3.5,
        "transitions": ["pixelize", "hblur", "slideup"],
        "trans_dur": 0.22,
        "trans_min_gap": 0.7,
        "trans_spacing": 4.0,
        "hook_zoom": 1.22,
        "progress": "0x00F0FF",
        "mood": "oscura",
        "sfx_gain": 0.5,
        "words_per_chunk": 3,
        "max_chars": 20,
        "uppercase": True,
        "highlight": "&H00FFF000&",           # cian
        "emphasis": "&H00FF3CFF&",            # magenta
        # sombras azuladas + mucho contraste y saturación
        "grade": "curves=m='0/0 0.25/0.18 0.75/0.85 1/1':b='0/0.06 0.5/0.56 1/1':r='0/0 0.5/0.5 1/1.0',"
                 "eq=saturation=1.45,vignette=PI/5",
    },
    "cinematic": {
        "max_gap": 0.9,                        # más aire, ritmo pausado
        "zooms": [1.0],
        "emphasis_zoom": None,
        "max_piece": None,
        "transitions": ["fadeblack", "dissolve"],
        "trans_dur": 0.4,
        "trans_min_gap": 0.0,
        "trans_spacing": 3.0,
        "hook_zoom": None,
        "progress": None,
        "mood": "emocional",
        "sfx_gain": 0.3,
        "words_per_chunk": 7,
        "max_chars": 42,
        "uppercase": False,
        "highlight": None,
        "emphasis": None,
        # teal & orange: sombras verde-azuladas, altas luces cálidas, contraste suave
        "grade": "curves=m='0/0 0.25/0.21 0.75/0.80 1/1'"
                 ":r='0/0 0.25/0.21 0.75/0.79 1/1':g='0/0.01 0.5/0.5 1/0.98':b='0/0.05 0.25/0.29 0.75/0.70 1/0.94',"
                 "eq=saturation=0.9,vignette=PI/4",
    },
}

# Explainer: estilo minimal + motion graphics automáticos (tarjetas de datos y pantallas blancas)
TEMPLATES["explainer"] = {**TEMPLATES["minimal"], "graphics": True}

PAD_IN = 0.12
PAD_OUT = 0.22
MAX_SEGMENTS = 80
MAX_TRANSITIONS = 14
AUDIO_FADE = 0.012

POWER_WORDS = {
    # español
    "gratis", "dinero", "secreto", "secretos", "nunca", "siempre", "error", "errores", "increible",
    "millones", "millon", "mil", "rapido", "facil", "nadie", "todos", "verdad", "mentira", "gana",
    "ganar", "perder", "peligro", "importante", "clave", "truco", "trucos", "brutal", "exito",
    "fracaso", "mejor", "peor", "ahora", "hoy", "jamas", "prohibido", "viral",
    # inglés
    "free", "money", "secret", "never", "always", "mistake", "million", "millions", "fast", "easy",
    "nobody", "everyone", "truth", "lie", "win", "lose", "best", "worst", "now", "today",
}


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
    hdr = video.get("color_transfer") in ("arib-std-b67", "smpte2084")   # iPhone HLG / HDR10
    return {"w": w, "h": h, "fps": round(fps, 3), "duration": duration, "has_audio": audio is not None, "hdr": hdr}


# HDR (iPhone) -> SDR Rec.709 con tone mapping, para que el color no salga lavado
TONEMAP = ("zscale=t=linear:npl=203,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=mobius:param=0.3:desat=2,"
           "zscale=t=bt709:m=bt709:r=tv,format=yuv420p")


def work_resolution(info, max_short=1080):
    """Trabaja como máximo a 1080p en el lado corto (4K del iPhone -> 1080x1920)."""
    short = min(info["w"], info["h"])
    if short > max_short:
        f = max_short / short
        info["w"], info["h"] = int(info["w"] * f) // 2 * 2, int(info["h"] * f) // 2 * 2
    return info


# ---------------------------------------------------------------------------
# 2. Transcripción
# ---------------------------------------------------------------------------
def _words_from_segments(segments):
    words = []
    for seg in segments:
        tokens = (_get(seg, "text") or "").split()
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
                model="whisper-1", file=f, response_format="verbose_json",
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


def normalize_token(text):
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^\w$%€]", "", text)


def is_emphasis(text):
    tok = normalize_token(text)
    return bool(re.search(r"\d|[$%€]", tok)) or tok in POWER_WORDS


def clean_words(words):
    out = []
    for w in words:
        text = (w.get("word") or "").strip()
        if not text:
            continue
        start = float(w["start"])
        end = max(float(w["end"]), start + 0.05)
        out.append({"word": text, "start": start, "end": end, "emph": is_emphasis(text)})
    out.sort(key=lambda w: w["start"])
    return out


# ---------------------------------------------------------------------------
# 3-5. Montaje: jump cuts, pattern interrupts y transiciones
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


def build_pieces(words, segments, cfg, fps):
    """Divide cada toma en planos de <= max_piece s (cambiando encuadre) y decide transiciones."""
    snap = lambda t: round(t * fps) / fps   # cortes exactos en frame
    pieces = []
    for si, (s, e) in enumerate(segments):
        seg_words = [w for w in words if s - 1e-3 <= w["start"] < e]
        bounds, emph_starts = [s], set()
        if cfg["max_piece"]:
            cur = s
            for j in range(1, len(seg_words)):
                prev, w = seg_words[j - 1], seg_words[j]
                split = (prev["end"] + w["start"]) / 2
                elapsed, remaining = split - cur, e - split
                if remaining < 0.6:
                    break
                if elapsed >= cfg["max_piece"] or (w["emph"] and cfg["emphasis_zoom"] and elapsed >= 1.0):
                    bounds.append(split)
                    if w["emph"]:
                        emph_starts.add(len(bounds) - 1)
                    cur = split
        bounds.append(e)
        bounds = [snap(b) for b in bounds]
        for k in range(len(bounds) - 1):
            if bounds[k + 1] - bounds[k] < 1.5 / fps:
                continue
            pieces.append({
                "start": bounds[k], "end": bounds[k + 1], "seg": si,
                "cut_before": k == 0 and si > 0,
                "gap_before": (s - segments[si - 1][1]) if (k == 0 and si > 0) else 0.0,
                "emph": k in emph_starts,
            })

    zi, kept, last_trans, n_trans = 0, 0.0, -1e9, 0
    td = cfg["trans_dur"]
    for i, p in enumerate(pieces):
        dur = p["end"] - p["start"]
        if p["emph"] and cfg["emphasis_zoom"]:
            p["zoom"] = cfg["emphasis_zoom"]
        else:
            p["zoom"] = cfg["zooms"][zi % len(cfg["zooms"])]
            zi += 1
        # Pattern interrupt real: dos planos contiguos nunca comparten encuadre
        if i > 0 and not p["cut_before"] and abs(p["zoom"] - pieces[i - 1]["zoom"]) < 0.01 and len(cfg["zooms"]) > 1:
            p["zoom"] = next(z for z in cfg["zooms"] if abs(z - pieces[i - 1]["zoom"]) >= 0.01)
        p["transition"] = None
        if (cfg["transitions"] and p["cut_before"] and p["gap_before"] >= cfg["trans_min_gap"] and n_trans < MAX_TRANSITIONS
                and kept - last_trans >= cfg["trans_spacing"]
                and dur > td * 2 + 0.1 and (pieces[i - 1]["end"] - pieces[i - 1]["start"]) > td * 2 + 0.1):
            p["transition"] = cfg["transitions"][n_trans % len(cfg["transitions"])]
            n_trans += 1
            last_trans = kept
        kept += dur
    return pieces


def layout_timeline(pieces, td):
    """Calcula dónde empieza cada plano en la línea de tiempo final (las transiciones solapan)."""
    length = 0.0
    for i, p in enumerate(pieces):
        p["out_start"] = length - td if (i > 0 and p["transition"]) else length
        length = p["out_start"] + (p["end"] - p["start"])
    return length


def remap_words(words, pieces):
    out, idx, prev_seg = [], 0, None
    for w in words:
        while idx < len(pieces) - 1 and w["start"] >= pieces[idx]["end"]:
            idx += 1
        p = pieces[idx]
        if not (p["start"] - 1e-3 <= w["start"] <= p["end"]):
            continue
        ns = p["out_start"] + (w["start"] - p["start"])
        ne = p["out_start"] + (min(w["end"], p["end"]) - p["start"])
        out.append({**w, "start": ns, "end": max(ne, ns + 0.05),
                    "cut": prev_seg is not None and p["seg"] != prev_seg})
        prev_seg = p["seg"]
    return out


# ---------------------------------------------------------------------------
# 6. Sound design (sintetizado, libre de derechos)
# ---------------------------------------------------------------------------
def write_wav(path, samples):
    peak = max(1e-9, max(abs(x) for x in samples))
    data = array.array("h", (int(max(-1.0, min(1.0, x / peak * 0.9)) * 32767) for x in samples))
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(data.tobytes())


def synth_whoosh(dur=0.5, seed=7):
    rnd = random.Random(seed)
    n = int(dur * SR)
    low = band = 0.0
    out = []
    for i in range(n):
        x = i / n
        env = math.sin(math.pi * x ** 0.8) ** 2               # sube y baja (efecto Doppler)
        fc = 350 + 3800 * env
        f = 2 * math.sin(math.pi * fc / SR)
        low += f * band
        high = rnd.uniform(-1, 1) - low - 0.6 * band
        band += f * high
        out.append(band * env)
    return out


def synth_impact(dur=0.9, seed=3):
    rnd = random.Random(seed)
    out, phase = [], 0.0
    for i in range(int(dur * SR)):
        t = i / SR
        freq = 45 + 110 * math.exp(-t * 18)                    # caída de pitch = "boom"
        phase += 2 * math.pi * freq / SR
        body = math.sin(phase) * math.exp(-t * 5.5)
        click = rnd.uniform(-1, 1) * math.exp(-t * 120) * 0.6
        out.append(body + click)
    return out


def build_sfx_events(pieces, cfg):
    events = []
    td = cfg["trans_dur"]
    if cfg["hook_zoom"]:
        events.append(("impact", 0.0, 0.9))
    for p in pieces:
        if p["transition"]:
            center = p["out_start"] + td / 2
            events.append(("whoosh", max(0.0, center - 0.25), 0.8))
            if p["transition"] in ("fadewhite", "zoomin"):
                events.append(("impact", center, 0.6))
    return events


# ---------------------------------------------------------------------------
# 7. Música por mood (tus pistas o una generada libre de derechos)
# ---------------------------------------------------------------------------
NOTE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "Bb": 10, "B": 11}


def nf(name, octave):
    return 440.0 * 2 ** ((NOTE[name] + 12 * (octave + 1) - 69) / 12)


MOODS = {
    # progresión vi-IV-I-V: la más usada en música emocional
    "emocional": {"chords": [("A", "C", "E"), ("F", "A", "C"), ("C", "E", "G"), ("G", "B", "D")],
                  "L": 3.2, "arp": 0.4, "pulse": None},
    "motivacional": {"chords": [("C", "E", "G"), ("G", "B", "D"), ("A", "C", "E"), ("F", "A", "C")],
                     "L": 2.0, "arp": 0.25, "pulse": 0.25},
    "oscura": {"chords": [("D", "F", "A"), ("Bb", "D", "F"), ("F", "A", "C"), ("C", "E", "G")],
               "L": 2.4, "arp": 0.2, "pulse": 0.2},
    # 128 BPM, bombo en cada tiempo y "pump" tipo sidechain: energía para deporte
    "deportiva": {"chords": [("E", "G", "B"), ("C", "E", "G"), ("G", "B", "D"), ("D", "F", "A")],
                  "L": 1.875, "arp": 0.234375, "pulse": 0.46875, "kick": 0.46875},
}


def _select(values, var):
    expr = f"{values[-1]:.3f}"
    for i in range(len(values) - 2, -1, -1):
        expr = f"if(eq({var},{i}),{values[i]:.3f},{expr})"
    return expr


def generate_music(path, mood):
    """Sintetiza un loop de 4 acordes (pad + bajo + arpegio). Se repite sin cortes."""
    m = MOODS[mood]
    L, arp = m["L"], m["arp"]
    c = f"mod(floor(t/{L}),4)"
    pad_env = f"min(1,mod(t,{L})/0.25)*min(1,({L}-mod(t,{L}))/0.25)"
    pulse = f"*(0.45+0.55*exp(-9*mod(t,{m['pulse']})))" if m["pulse"] else ""

    def channel(detune):
        parts = []
        for k in range(3):
            f = _select([nf(ch[k], 4 if k else 3) * detune for ch in m["chords"]], c)
            parts.append(f"0.16*(sin(2*PI*t*{f})+0.25*sin(4*PI*t*{f}))")
        bass = _select([nf(ch[0], 2) * detune for ch in m["chords"]], c)
        parts.append(f"0.22*sin(2*PI*t*{bass})")
        idx = f"mod(floor(t/{arp}),3)"
        arp_freq = "+".join(
            f"if(eq({idx},{k}),{_select([nf(ch[k], 5) * detune for ch in m['chords']], c)},0)" for k in range(3))
        arp_env = f"pow(1-mod(t,{arp})/{arp},3)*min(1,mod(t,{arp})/0.008)"
        kick = ""
        if m.get("kick"):
            u = f"mod(t,{m['kick']})"
            kick = f"+0.55*exp(-14*{u})*sin(2*PI*(48*{u}+3*(1-exp(-40*{u}))))"
        return f"({'+'.join(parts)})*{pad_env}{pulse}+0.12*{arp_env}*sin(2*PI*t*({arp_freq})){kick}"

    expr = f"{channel(1.0)}|{channel(1.004)}"
    run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"aevalsrc=exprs='{expr}':s={SR}:d={4 * L}",
         "-af", "lowpass=f=4200,volume=0.8", path])


def pick_music(option, mood, work, seed_name):
    if option == "none":
        return None, None
    if option not in (None, "", "auto"):
        if not os.path.exists(option):
            raise ValueError(f"No existe la pista de música: {option}")
        return option, "archivo"
    music_dir = os.getenv("VF_MUSIC_DIR", os.path.join(ROOT, "music"))
    tracks = sorted(f for ext in ("mp3", "wav", "m4a", "aac", "ogg", "flac")
                    for f in glob.glob(os.path.join(music_dir, mood, f"*.{ext}")))
    if tracks:
        return tracks[sum(map(ord, seed_name)) % len(tracks)], "biblioteca"
    path = os.path.join(work, "music.wav")
    generate_music(path, mood)
    return path, "generada"


# ---------------------------------------------------------------------------
# 8. Subtítulos ASS karaoke
# ---------------------------------------------------------------------------
def ass_time(t):
    cs = int(round(max(0.0, t) * 100))
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
            if (len(current) >= cfg["words_per_chunk"] or chars > cfg["max_chars"] or gap > 0.5
                    or w.get("cut") or re.search(r"[.!?…]$", current[-1]["word"])):
                chunks.append(current)
                current = []
        current.append(w)
    if current:
        chunks.append(current)
    return chunks


def build_ass(words, template, out_w, out_h, total):
    cfg = TEMPLATES[template]
    ref = min(out_w, out_h)
    if template in ("minimal", "explainer"):
        size = int(out_w * 0.07)
        style = (f"Style: Main,Liberation Sans,{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&HA0000000,"
                 f"0,0,0,0,100,100,-1,0,1,0,{max(1, size * 0.04):.1f},2,"
                 f"{int(out_w * 0.08)},{int(out_w * 0.08)},{int(out_h * 0.30)},1")
    elif template == "cinematic":
        size = int(ref * 0.055)
        style = (f"Style: Main,Liberation Sans,{size},&H00FFFFFF,&H70FFFFFF,&H00000000,&H90000000,"
                 f"0,0,0,0,100,100,1,0,1,{max(1, size * 0.04):.1f},{max(1, size * 0.06):.1f},2,"
                 f"{int(out_w * 0.08)},{int(out_w * 0.08)},{int(out_h * 0.08)},1")
    elif template == "neon":
        size = int(ref * 0.075)
        style = (f"Style: Main,Liberation Sans,{size},&H00F5F5F5,&H00F5F5F5,&H00FFF000,&H00000000,"
                 f"-1,0,0,0,100,100,2,0,1,{max(2, size * 0.05):.1f},0,2,"
                 f"{int(out_w * 0.07)},{int(out_w * 0.07)},{int(out_h * 0.20)},1")
    else:
        size = int(ref * 0.09)
        style = (f"Style: Main,Liberation Sans,{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,"
                 f"-1,0,0,0,100,100,1,0,1,{max(3, size * 0.09):.1f},{max(2, size * 0.04):.1f},2,"
                 f"{int(out_w * 0.07)},{int(out_w * 0.07)},{int(out_h * 0.25)},1")

    header = (
        "[Script Info]\nScriptType: v4.00+\nWrapStyle: 0\nScaledBorderAndShadow: yes\n"
        f"PlayResX: {out_w}\nPlayResY: {out_h}\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"{style}\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    events = []
    chunks = chunk_words(words, cfg)
    for ci, chunk in enumerate(chunks):
        next_start = chunks[ci + 1][0]["start"] if ci + 1 < len(chunks) else total
        chunk_end = min(next_start, chunk[-1]["end"] + 0.35)

        if template in ("minimal", "explainer"):
            text = " ".join(re.sub(r"[.,;:…]+$", "", ass_escape(w["word"])) for w in chunk)
            events.append(f"Dialogue: 0,{ass_time(chunk[0]['start'])},{ass_time(chunk_end)},Main,,0,0,0,,"
                          f"{{\\fad(90,60)\\blur3\\t(0,140,\\blur0)}}{text}")
            continue

        if template == "cinematic":
            parts, cursor = [], chunk[0]["start"]
            for w in chunk:
                parts.append(f"{{\\k{max(1, int(round((w['end'] - cursor) * 100)))}}}{caption_text(w['word'], cfg)}")
                cursor = w["end"]
            events.append(f"Dialogue: 0,{ass_time(chunk[0]['start'])},{ass_time(chunk_end)},Main,,0,0,0,,"
                          f"{{\\fad(150,150)}}{' '.join(parts)}")
            continue

        for wi, w in enumerate(chunk):
            start = w["start"]
            end = chunk[wi + 1]["start"] if wi + 1 < len(chunk) else chunk_end
            if end <= start:
                continue
            pieces = []
            for oj, other in enumerate(chunk):
                word = caption_text(other["word"], cfg)
                color = cfg["emphasis"] if other["emph"] else None
                if oj == wi:
                    big = 135 if other["emph"] else 125
                    color = color or cfg["highlight"]
                    glow = f"\\3c{color}\\blur4" if template == "neon" else ""
                    pieces.append(f"{{\\c{color}{glow}\\fscx{big}\\fscy{big}\\t(0,90,\\fscx110\\fscy110)}}{word}{{\\r}}")
                elif color:
                    pieces.append(f"{{\\c{color}}}{word}{{\\r}}")
                elif template == "neon":
                    pieces.append(f"{{\\alpha&H40&}}{word}{{\\r}}")
                else:
                    pieces.append(word)
            intro = "{\\fscx90\\fscy90\\t(0,80,\\fscx100\\fscy100)}" if wi == 0 else ""
            events.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Main,,0,0,0,,{intro}{' '.join(pieces)}")
    return header + "\n".join(events) + "\n"


# ---------------------------------------------------------------------------
# Grafo de filtros FFmpeg
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
    final = (1080, 1920) if aspect == "9:16" else (1080, 1080)
    return (even(ow), even(oh)), final


def render_pieces(input_path, pieces, info, cfg, work):
    """Etapa 1: cada plano se renderiza por separado (seek preciso, zoom, micro-fades).
    Memoria constante y mucho más rápido que un único grafo gigante."""
    W, H = even(info["w"]), even(info["h"])
    fps, n = info["fps"], len(pieces)
    files = []
    for i, p in enumerate(pieces):
        dur = p["end"] - p["start"]
        if i == 0 and cfg["hook_zoom"]:
            # Hook: arranca acercado y se abre con ease-out en 0.7s
            frames = max(1, int(fps * 0.7))
            z0 = cfg["hook_zoom"]
            vfx = (f"scale={W}:{H},zoompan=z='if(lt(on,{frames}),1+{z0 - 1:.3f}*pow(1-on/{frames},2),1)'"
                   f":d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps={fps}")
        elif p["zoom"] > 1.001:
            vfx = f"scale={even(W * p['zoom'])}:{even(H * p['zoom'])},crop={W}:{H}"
        else:
            vfx = f"scale={W}:{H}"
        afx = f"aformat=sample_rates={SR}:channel_layouts=stereo,apad,atrim=0:{dur:.4f}"
        if p["cut_before"] and not p["transition"]:
            afx += f",afade=t=in:d={AUDIO_FADE}"
        nxt = pieces[i + 1] if i + 1 < n else None
        if nxt and nxt["cut_before"] and not nxt["transition"]:
            afx += f",afade=t=out:st={max(0, dur - AUDIO_FADE):.4f}:d={AUDIO_FADE}"
        out = os.path.join(work, f"piece_{i:03d}.mkv")
        run(["ffmpeg", "-y", "-v", "error", "-ss", f"{p['start']:.4f}", "-i", input_path, "-t", f"{dur:.4f}",
             "-vf", f"{vfx}{',' + TONEMAP if info.get('hdr') else ''},setsar=1,fps={fps},format=yuv420p", "-af", afx,
             "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", "-g", "1",
             "-c:a", "pcm_s16le", out])
        files.append(out)
        if (i + 1) % 10 == 0 or i + 1 == n:
            log(f"   · {i + 1}/{n} planos")
    return files


def group_inputs(pieces, files, work):
    """Planos contiguos se unen (concat demuxer); entre grupos va una transición."""
    groups = [[0]]
    for i in range(1, len(pieces)):
        if pieces[i]["transition"]:
            groups.append([i])
        else:
            groups[-1].append(i)
    args = []
    for gi, g in enumerate(groups):
        lst = os.path.join(work, f"group_{gi:03d}.txt")
        with open(lst, "w") as f:
            f.writelines(f"file '{files[i]}'\n" for i in g)
        args += ["-f", "concat", "-safe", "0", "-i", lst]
    return groups, args


def build_filtergraph(pieces, groups, info, cfg, crop_dims, ass_path, out_w, out_h, total,
                      music_idx, sfx_inputs, mg_path=None):
    fps, td = info["fps"], cfg["trans_dur"]
    W, H = even(info["w"]), even(info["h"])
    L = []
    for gi in range(len(groups)):
        L.append(f"[{gi}:v]settb=AVTB,fps={fps},setpts=PTS-STARTPTS[gv{gi}]")
        L.append(f"[{gi}:a]asetpts=PTS-STARTPTS[ga{gi}]")
    cur_v, cur_a = "gv0", "ga0"
    for gi in range(1, len(groups)):
        p = pieces[groups[gi][0]]
        L.append(f"[{cur_v}][gv{gi}]xfade=transition={p['transition']}:duration={td}:offset={p['out_start']:.4f}[xv{gi}]")
        L.append(f"[{cur_a}][ga{gi}]acrossfade=d={td}:c1=tri:c2=tri[xa{gi}]")
        cur_v, cur_a = f"xv{gi}", f"xa{gi}"

    # Video final: reencuadre, etalonaje, barra de progreso, subtítulos
    reframe = ""
    if crop_dims != (W, H):
        reframe += f"crop={crop_dims[0]}:{crop_dims[1]},"
    if (out_w, out_h) != crop_dims:
        reframe += f"scale={out_w}:{out_h}:flags=lanczos,setsar=1,"
    L.append(f"[{cur_v}]{reframe}{cfg['grade']}[vgraded]")
    video_out = "vgraded"
    if cfg["progress"]:
        bh = max(6, out_h // 160)
        L.append(f"color=c={cfg['progress']}:s={out_w}x{bh}:r={fps}:d={total + 1:.3f}[bar]")
        L.append(f"[vgraded][bar]overlay=x='-w+w*t/{total:.3f}':y={out_h - bh}:eval=frame:shortest=1[vbar]")
        video_out = "vbar"
    mg = f",ass='{filter_path(mg_path)}'" if mg_path else ""
    L.append(f"[{video_out}]ass='{filter_path(ass_path)}'{mg},fps={fps},format=yuv420p[vout]")

    # Audio: voz procesada + música con ducking + SFX
    L.append(f"[{cur_a}]highpass=f=80,acompressor=threshold=-18dB:ratio=3:attack=5:release=120:makeup=2[voice0]")
    mix = ["voice"]
    if music_idx is not None:
        L.append("[voice0]asplit=2[voice][vkey]")
        L.append(
            f"[{music_idx}:a]aformat=sample_rates={SR}:channel_layouts=stereo,atrim=0:{total:.3f},"
            f"asetpts=PTS-STARTPTS,aecho=0.8:0.55:80|140:0.3|0.2,volume=0.55,afade=t=in:d=1.2,"
            f"afade=t=out:st={max(0, total - 2):.3f}:d=2[mus]")
        L.append("[mus][vkey]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=350[musd]")
        mix.append("musd")
    else:
        L.append("[voice0]anull[voice]")
    if sfx_inputs:
        for k, (idx, delay, gain) in enumerate(sfx_inputs):
            L.append(f"[{idx}:a]aformat=sample_rates={SR}:channel_layouts=stereo,"
                     f"adelay=delays={int(delay * 1000)}:all=1,volume={gain:.2f}[sfx{k}]")
        if len(sfx_inputs) > 1:
            L.append("".join(f"[sfx{k}]" for k in range(len(sfx_inputs)))
                     + f"amix=inputs={len(sfx_inputs)}:duration=longest:normalize=0[sfx]")
        else:
            L.append("[sfx0]anull[sfx]")
        mix.append("sfx")
    L.append("".join(f"[{m}]" for m in mix)
             + f"amix=inputs={len(mix)}:duration=first:normalize=0,"
               "loudnorm=I=-14:TP=-1.0:LRA=11,aresample=48000[aout]")
    return ";\n".join(L)


# ---------------------------------------------------------------------------
# Pipeline principal
# ---------------------------------------------------------------------------
def process_video(input_path, output_path, template="minimal", aspect="original", cuts=True,
                  transcript_json=None, music="auto", sfx=True):
    if template not in TEMPLATES:
        log(f"⚠️  Template '{template}' desconocido, usando 'minimal'.")
        template = "minimal"
    cfg = TEMPLATES[template]
    log(f"🎬 ViralFactory Engine PRO: {input_path} | template: {template} | formato: {aspect}")

    work = tempfile.mkdtemp(prefix="vf_")
    try:
        info = work_resolution(probe(input_path))
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
        log(f"📝 {len(words)} palabras · {sum(w['emph'] for w in words)} palabras clave resaltadas")

        segments = limit_segments(words, info["duration"], cfg["max_gap"]) if cuts else [(0.0, info["duration"])]
        pieces = build_pieces(words, segments, cfg, info["fps"])
        total = layout_timeline(pieces, cfg["trans_dur"])
        new_words = remap_words(words, pieces)
        n_trans = sum(1 for p in pieces if p["transition"])
        log(f"✂️  {len(segments)} tomas → {len(pieces)} planos · {n_trans} transiciones · "
            f"{info['duration']:.1f}s → {total:.1f}s")

        crop_dims, (out_w, out_h) = output_dims(even(info["w"]), even(info["h"]), aspect)
        ass_path = os.path.join(work, "subs.ass")
        with open(ass_path, "w", encoding="utf-8") as f:
            f.write(build_ass(new_words, template, out_w, out_h, total))

        mg_path = None
        if cfg.get("graphics"):
            from motion_graphics import pick_graphics, build_graphics_ass
            specs = pick_graphics(new_words, total, log)
            if specs:
                mg_path = os.path.join(work, "graphics.ass")
                with open(mg_path, "w", encoding="utf-8") as f:
                    f.write(build_graphics_ass(specs, out_w, out_h))
            log(f"📊 {len(specs)} gráficos animados: " + ", ".join(
                f"{g['type']}@{g['start']:.1f}s" for g in specs))

        log(f"🎞️  Etapa 1: renderizando {len(pieces)} planos (cortes + zooms + hook)...")
        files = render_pieces(input_path, pieces, info, cfg, work)
        groups, inputs = group_inputs(pieces, files, work)
        n_inputs = len(groups)
        music_path, source = pick_music(music, cfg["mood"], work, os.path.basename(input_path))
        music_idx = None
        if music_path:
            inputs += ["-stream_loop", "-1", "-i", music_path]
            music_idx, n_inputs = n_inputs, n_inputs + 1
            log(f"🎵 Música '{cfg['mood']}' ({source}) con ducking bajo la voz")

        sfx_inputs = []
        events = build_sfx_events(pieces, cfg) if sfx else []
        if events:
            files = {"whoosh": os.path.join(work, "whoosh.wav"), "impact": os.path.join(work, "impact.wav")}
            write_wav(files["whoosh"], synth_whoosh())
            write_wav(files["impact"], synth_impact())
            for kind, at, gain in events:
                inputs += ["-i", files[kind]]
                sfx_inputs.append((n_inputs, at, gain * cfg["sfx_gain"]))
                n_inputs += 1
            log(f"🔊 {len(events)} efectos de sonido (whoosh / impacto)")

        graph_path = os.path.join(work, "graph.txt")
        with open(graph_path, "w") as f:
            f.write(build_filtergraph(pieces, groups, info, cfg, crop_dims, ass_path, out_w, out_h, total,
                                      music_idx, sfx_inputs, mg_path))

        log("🎞️  Etapa 2: transiciones + etalonaje + subtítulos + música + SFX + master de audio...")
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        run(["ffmpeg", "-y", "-v", "error", *inputs,
             "-filter_complex_script", graph_path,
             "-map", "[vout]", "-map", "[aout]", "-t", f"{total:.3f}",
             "-c:v", "libx264", "-preset", os.getenv("VF_PRESET", "fast"), "-crf", "18",
             "-profile:v", "high", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", output_path])
        log(f"✅ Éxito total: {output_path} ({out_w}x{out_h}, {total:.1f}s)")
    except Exception as e:
        log(f"❌ Error en el motor: {e}")
        sys.exit(1)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description="ViralFactory Engine PRO")
    parser.add_argument("input", help="Video de entrada (en 'sport' puedes separar varios clips con comas)")
    parser.add_argument("output")
    parser.add_argument("template", nargs="?", default="minimal")
    parser.add_argument("--lines", default=os.getenv("VF_LINES"),
                        help="sport: frases separadas por | que aparecen al ritmo (ej. 'run|run fast')")
    parser.add_argument("--duration", type=float, default=None, help="sport: duración objetivo en segundos")
    parser.add_argument("--aspect", choices=["original", "9:16", "1:1"], default=os.getenv("VF_ASPECT", "original"))
    parser.add_argument("--music", default=os.getenv("VF_MUSIC", "auto"),
                        help="auto (music/<mood>/ o generada), none, o ruta a un archivo")
    parser.add_argument("--no-sfx", action="store_true", help="Sin efectos de sonido")
    parser.add_argument("--no-cuts", action="store_true", help="No eliminar silencios")
    parser.add_argument("--transcript-json", help="Usar una transcripción [{word,start,end}] en vez de Whisper")
    args = parser.parse_args()
    if args.template == "sport":
        from sport import process_sport
        aspect = args.aspect if args.aspect != "original" else "9:16"
        process_sport(args.input.split(","), args.output, aspect=aspect, music=args.music,
                      lines=args.lines, duration=args.duration, transcript_json=args.transcript_json)
        return
    process_video(args.input, args.output, args.template, args.aspect, cuts=not args.no_cuts,
                  transcript_json=args.transcript_json, music=args.music, sfx=not args.no_sfx)


if __name__ == "__main__":
    main()
