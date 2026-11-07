"""
Estilo SPORT: montaje deportivo al ritmo de la música.

  - Detecta el tempo y los beats de la música; cada corte cae en un beat (cada 2 beats,
    con planos "hero" de 4 beats en cámara lenta).
  - Elige los momentos con más movimiento de los clips (sin repetir).
  - Cámara lenta en planos hero, reencuadre vertical 1080x1920.
  - Color verde-azulado intenso con piel cálida.
  - Texto blanco centrado que cambia al ritmo (--lines, o la letra de la canción).
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

import numpy as np

import editor
from editor import SR, TONEMAP, ass_escape, ass_time, even, filter_path, log, pick_music, probe, run

FPS = 30
GRADE = ("curves=r='0/0 0.3/0.26 0.7/0.74 1/1':g='0/0 0.5/0.53 1/1':b='0/0.06 0.4/0.45 1/0.94',"
         "eq=contrast=1.08:saturation=1.15,vignette=PI/7")


# ---------------------------------------------------------------------------
# Beats
# ---------------------------------------------------------------------------
def detect_beats(path, max_len):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-t", f"{max_len:.2f}", "-ac", "1",
                          "-ar", "22050", "-f", "f32le", "-"], capture_output=True).stdout
    x = np.frombuffer(raw, np.float32)
    hop = 512
    fr = 22050 / hop
    n = len(x) // hop
    if n < fr * 4:
        return 0.5, 0.0, len(x) / 22050
    frames = x[: n * hop].reshape(n, hop) * np.hanning(hop)
    spec = np.log1p(np.abs(np.fft.rfft(frames, axis=1)) * 10)
    flux = np.maximum(0, np.diff(spec, axis=0)).sum(axis=1)
    flux -= np.convolve(flux, np.ones(16) / 16, mode="same")
    onset = np.maximum(flux, 0)

    ac = np.correlate(onset, onset, mode="full")[len(onset) - 1:]
    best, best_lag = -1, int(fr * 0.5)
    for bpm in np.arange(70, 181, 0.5):
        lag = 60 * fr / bpm
        i0, frac = int(lag), lag - int(lag)
        if i0 + 1 >= len(ac):
            continue
        val = ac[i0] * (1 - frac) + ac[i0 + 1] * frac
        weight = np.exp(-0.5 * (np.log2(bpm / 120) / 0.6) ** 2)      # preferencia ~120 BPM
        if val * weight > best:
            best, best_lag = val * weight, lag
    period = best_lag / fr
    phases = np.arange(0, best_lag, 0.5)
    scores = [onset[np.round(np.arange(p, len(onset), best_lag)).astype(int).clip(0, len(onset) - 1)].sum()
              for p in phases]
    phase = phases[int(np.argmax(scores))] / fr
    return period, phase, len(x) / 22050


# ---------------------------------------------------------------------------
# Movimiento en los clips
# ---------------------------------------------------------------------------
def motion_profile(path):
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-vf",
         "fps=8,scale=160:-2,tblend=all_mode=difference,signalstats,"
         "metadata=print:key=lavfi.signalstats.YAVG:file=-", "-an", "-f", "null", "-"],
        capture_output=True, text=True).stdout
    times, vals, t = [], [], None
    for line in out.splitlines():
        m = re.search(r"pts_time:([\d.]+)", line)
        if m:
            t = float(m.group(1))
        m = re.search(r"YAVG=([\d.]+)", line)
        if m and t is not None:
            times.append(t)
            vals.append(float(m.group(1)))
    return np.array(times), np.array(vals)


def window_score(times, vals, a, b, cap=None):
    """Movimiento medio del tramo; por encima de 'cap' (sacudidas de cámara) se penaliza."""
    sel = (times >= a) & (times <= b)
    if not sel.any():
        return 0.0
    m = float(vals[sel].mean())
    return m if cap is None or m <= cap else max(0.0, 2 * cap - m)


def choose_windows(clips, shots):
    """Asigna a cada plano el tramo de clip con más movimiento, sin solapes entre planos."""
    used = {i: [] for i in range(len(clips))}
    allv = np.concatenate([c["vals"] for c in clips if len(c["vals"])]) if clips else np.array([])
    cap = float(np.percentile(allv, 70)) if len(allv) else None
    chosen, last_clip = [], None
    for shot in shots:
        need = shot["src_dur"]
        best = None
        for ci, c in enumerate(clips):
            dur = c["info"]["duration"]
            for a in np.arange(0.0, max(0.0, dur - need - 0.05) + 1e-6, 0.25):
                b = a + need
                if any(a < ub + 0.15 and b > ua - 0.15 for ua, ub in used[ci]):
                    continue
                score = window_score(c["times"], c["vals"], a, b, cap)
                if ci == last_clip and len(clips) > 1:
                    score *= 0.7                                      # variedad de clips
                if best is None or score > best[0]:
                    best = (score, ci, a)
        if best is None:                                              # material agotado: reutilizar
            ci = (last_clip + 1) % len(clips) if last_clip is not None else 0
            dur = clips[ci]["info"]["duration"]
            a = (len(chosen) * 1.37) % max(0.01, dur - need)
            best = (0, ci, a)
        _, ci, a = best
        used[ci].append((a, a + need))
        chosen.append({**shot, "clip": ci, "src_start": float(a)})
        last_clip = ci
    # se conserva el orden de la grilla (planos hero repartidos cada 4)
    durs = [s["dur"] for s in chosen]
    result, t = list(chosen), 0.0
    for s in result:
        s["out_start"] = t
        t += s["dur"]
    return result, sum(durs)


# ---------------------------------------------------------------------------
# Efectos por plano
# ---------------------------------------------------------------------------
RAMP = 1.5   # rampa de velocidad: en el centro del plano va a 1/(1+RAMP) = 0.4x


def assign_effects(shots, period):
    """Decide el efecto de cada plano según su posición en el compás."""
    for n, s in enumerate(shots):
        beat = int(round(s["out_start"] / period)) % 8      # posición en la frase de 2 compases
        if n == 0 or (n == len(shots) - 1 and len(shots) > 2):
            s["fx"] = "impact"                 # arranque (hook) y cierre con golpe
        elif s["hero"]:
            s["fx"] = "ramp"                   # rampa de velocidad + push-in lento
        elif beat == 0:
            s["fx"] = "impact"                 # flash + zoom de impacto + sacudida (cada 2 compases)
        elif beat == 4:
            s["fx"] = "glitch" if n % 2 else "cut"   # separación RGB, o corte seco para respirar
        else:
            s["fx"] = "whip"                   # barrido con desenfoque de movimiento
    for n, s in enumerate(shots):
        s["whip_out"] = n + 1 < len(shots) and shots[n + 1]["fx"] == "whip"


def shot_filter(s, W, H, hdr):
    dur = s["dur"]
    nfr = max(1, round(dur * FPS))
    tm = f",{TONEMAP}" if hdr else ""
    chain = f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H}{tm}"
    if s["fx"] == "ramp":
        D = s["src_dur"]
        chain += (f",setpts='((T-STARTT)+{RAMP}*((T-STARTT)/2-{D:.4f}/(4*PI)*sin(2*PI*(T-STARTT)/{D:.4f})))/TB'")
    chain += f",fps={FPS}"
    # zoom / sacudida con zoompan (un frame de salida por frame de entrada)
    if s["fx"] == "impact":
        p = int(FPS * 0.32)
        z = f"1.05+0.16*pow(max(0,1-on/{p}),2)"
        sx = f"16*sin(on*2.3)*exp(-on/5)"
        sy = f"12*cos(on*1.9)*exp(-on/5)"
    elif s["fx"] == "ramp":
        z = f"1.0+0.09*on/{nfr}"
        sx = sy = "0"
    elif s["fx"] == "glitch":
        z = "1.05"
        sx = f"18*sin(on*3.1)*exp(-on/3)"
        sy = "0"
    else:
        z = "1.0"
        sx = sy = "0"
    if z != "1.0" or sx != "0":
        chain += (f",zoompan=z='{z}':d=1:s={W}x{H}:fps={FPS}"
                  f":x='iw/2-(iw/zoom/2)+{sx}':y='ih/2-(ih/zoom/2)+{sy}'")
    end = dur - 0.1
    if s["fx"] == "impact":
        chain += ",fade=t=in:st=0:d=0.13:color=white"
    if s["fx"] == "whip":
        chain += ",dblur=angle=0:radius=70:enable='lt(t,0.1)'"
    if s["whip_out"]:
        chain += f",dblur=angle=180:radius=70:enable='gt(t,{end:.3f})'"
    if s["fx"] == "glitch":
        chain += ",rgbashift=rh=-16:bh=16:gv=6:enable='lt(t,0.14)'"
    return chain + ",setsar=1,format=yuv420p"


def synth_riser(dur=1.6, seed=11):
    rnd = np.random.default_rng(seed)
    n = int(dur * SR)
    t = np.arange(n) / SR
    x = t / dur
    noise = rnd.uniform(-1, 1, n)
    out, low, band = np.zeros(n), 0.0, 0.0
    fc = 300 + 6000 * x ** 2
    f = 2 * np.sin(np.pi * fc / SR)
    for i in range(n):                       # filtro paso banda que sube
        low += f[i] * band
        high = noise[i] - low - 0.5 * band
        band += f[i] * high
        out[i] = band
    tone = np.sin(2 * np.pi * np.cumsum(180 + 700 * x ** 2) / SR) * 0.35
    return (out + tone) * x ** 2.2


def build_sfx_track(path, shots, total):
    track = np.zeros(int((total + 1) * SR))

    def put(sig, at, gain):
        sig = np.asarray(sig, dtype=np.float64)
        sig = sig / max(1e-9, np.abs(sig).max())
        i = int(max(0, at) * SR)
        j = min(len(track), i + len(sig))
        if j > i:
            track[i:j] += sig[: j - i] * gain

    whoosh = editor.synth_whoosh(0.45)
    impact = editor.synth_impact(0.9)
    for n, s in enumerate(shots):
        if s["fx"] == "whip":
            put(whoosh, s["out_start"] - 0.22, 0.35)
        elif s["fx"] == "impact":
            put(impact, s["out_start"], 0.45 if n else 0.6)
        elif s["fx"] == "glitch":
            put(whoosh[::2], s["out_start"] - 0.05, 0.25)
    if len(shots) > 2:                       # riser hacia el último plano y golpe final
        last = shots[-1]["out_start"]
        put(synth_riser(1.6), last - 1.6, 0.30)
        put(impact, last, 0.7)
    editor.write_wav(path, list(np.clip(track, -1, 1)))
    return path


# ---------------------------------------------------------------------------
# Texto al ritmo
# ---------------------------------------------------------------------------
def build_text_ass(events, W, H):
    size = int(W * 0.075)
    header = (
        "[Script Info]\nScriptType: v4.00+\nWrapStyle: 0\nScaledBorderAndShadow: yes\n"
        f"PlayResX: {W}\nPlayResY: {H}\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: T,Inter Semi Bold,{size},&H00FFFFFF,&H00FFFFFF,&HA0000000,&H90000000,"
        f"0,0,0,0,100,100,-0.5,0,1,{max(2, size * 0.07):.1f},0,5,40,40,0,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    # entrada tipo keynote de Apple: el espaciado se cierra, escala y opacidad se asientan
    anim = "\\fad(60,90)\\blur5\\fsp14\\fscx106\\fscy106\\1a&H50&\\t(0,280,0.5,\\fsp-0.5\\fscx100\\fscy100\\1a&H00&)"
    lines = [f"Dialogue: 0,{ass_time(a)},{ass_time(b)},T,,0,0,0,,{{\\pos({W // 2},{int(H * 0.5)}){anim}}}{txt}"
             for a, b, txt in events]
    return header + "\n".join(lines) + "\n"


def text_from_lines(lines, shots):
    phrases = [ass_escape(p.strip()).replace("/", "\\N") for p in lines.split("|") if p.strip()]
    if not phrases:
        return []
    events = []
    for k, ph in enumerate(phrases):                     # reparto proporcional de planos por frase
        i0 = round(k * len(shots) / len(phrases))
        i1 = round((k + 1) * len(shots) / len(phrases))
        group = shots[i0:max(i1, i0 + 1)]
        if not group:
            break
        events.append((group[0]["out_start"], group[-1]["out_start"] + group[-1]["dur"], ph.lower()))
    return events


def text_from_words(words, total):
    events, cur = [], []
    for w in words:
        if w["start"] >= total:
            break
        cur.append(w)
        if len(cur) >= 2 or re.search(r"[.,!?]$", w["word"]):
            events.append((cur[0]["start"], min(total, cur[-1]["end"] + 0.25),
                           ass_escape(" ".join(re.sub(r"[.,;:!?]+$", "", x["word"].strip()) for x in cur)).lower()))
            cur = []
    if cur:
        events.append((cur[0]["start"], min(total, cur[-1]["end"] + 0.25),
                       ass_escape(" ".join(x["word"].strip() for x in cur)).lower()))
    return events


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
def process_sport(inputs, output_path, aspect="9:16", music="auto", lines=None, duration=None,
                  transcript_json=None):
    log(f"🏃 ViralFactory SPORT: {len(inputs)} clip(s) · formato {aspect}")
    work = tempfile.mkdtemp(prefix="vf_sport_")
    try:
        clips = []
        for path in inputs:
            info = probe(path)
            times, vals = motion_profile(path)
            clips.append({"path": path, "info": info, "times": times, "vals": vals})
            log(f"📐 {os.path.basename(path)}: {info['w']}x{info['h']} @ {info['fps']}fps · {info['duration']:.1f}s")
        material = sum(c["info"]["duration"] for c in clips)

        # Música: archivo/biblioteca/generada, o el audio del propio clip
        if music == "source":
            music_path, source = inputs[0], "audio original"
        else:
            music_path, source = pick_music(music if music != "none" else "auto", "deportiva", work, os.path.basename(inputs[0]))
        target = duration or min(20.0, material * 1.3)
        period, phase, music_len = detect_beats(music_path, max(target + 5, 30))
        if source == "generada":
            period, phase = 0.46875, 0.0
        bpm = 60 / period
        log(f"🎵 Música ({source}) · {bpm:.0f} BPM")

        # Grilla de cortes: cada 2 beats (4 si el tempo es muy rápido); cada 4º plano es "hero" lento
        step = period * (2 if period >= 0.33 else 4)
        cut_times = [0.0]
        t = phase if phase > 0.05 else step
        while t < target - 0.3:
            if t - cut_times[-1] >= step * 0.9:
                cut_times.append(t)
            t += step
        shots, k = [], 0
        i = 0
        while i < len(cut_times):
            hero = (k % 4 == 3) and i + 2 < len(cut_times)
            j = i + (2 if hero else 1)
            end = cut_times[j] if j < len(cut_times) else target
            a, b = round(cut_times[i] * FPS) / FPS, round(end * FPS) / FPS
            if b - a > 2 / FPS:
                src = (b - a) / (1 + RAMP / 2) if hero else (b - a)
                shots.append({"dur": b - a, "speed": src / (b - a), "src_dur": src, "hero": hero})
            i, k = j, k + 1
        shots, total = choose_windows(clips, shots)
        log(f"✂️  {len(shots)} planos al beat · {sum(s['hero'] for s in shots)} con rampa de velocidad · {total:.1f}s")

        W, H = (1080, 1920) if aspect == "9:16" else (1080, 1080) if aspect == "1:1" else (1920, 1080)
        assign_effects(shots, period)
        log("✨ Efectos: " + " ".join(s["fx"] for s in shots))
        files = []
        for n, s in enumerate(shots):
            c = clips[s["clip"]]
            vf = shot_filter(s, W, H, c["info"].get("hdr"))
            out = os.path.join(work, f"shot_{n:03d}.mkv")
            run(["ffmpeg", "-y", "-v", "error", "-ss", f"{s['src_start']:.3f}", "-t", f"{s['src_dur'] + 0.05:.3f}",
                 "-i", c["path"], "-an", "-vf", vf, "-frames:v", str(round(s["dur"] * FPS)),
                 "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", "-g", "1", out])
            files.append(out)
        lst = os.path.join(work, "shots.txt")
        with open(lst, "w") as f:
            f.writelines(f"file '{p}'\n" for p in files)

        # Texto
        events = []
        if lines:
            events = text_from_lines(lines, shots)
        elif transcript_json:
            with open(transcript_json) as f:
                events = text_from_words(editor.clean_words(json.load(f)), total)
        elif source != "generada" and os.getenv("VF_SPORT_LYRICS", "1") == "1":
            try:
                lyr = os.path.join(work, "lyrics.mp3")
                run(["ffmpeg", "-y", "-v", "error", "-i", music_path, "-t", f"{total:.2f}", "-vn",
                     "-ac", "1", "-ar", "16000", lyr])
                events = text_from_words(editor.clean_words(editor.transcribe(lyr)), total)
            except Exception as e:
                log(f"ℹ️  Sin letra detectada ({e.__class__.__name__}).")
        ass_path = os.path.join(work, "text.ass")
        with open(ass_path, "w", encoding="utf-8") as f:
            f.write(build_text_ass(events, W, H))
        log(f"🔤 {len(events)} textos al ritmo")

        sfx_path = build_sfx_track(os.path.join(work, "sfx.wav"), shots, total)
        pulse = f"eq=brightness='0.03*exp(-9*mod(t+{period - phase % period:.4f},{period:.4f}))':eval=frame"
        graph = (f"[0:v]{GRADE},{pulse},{editor.ass_filter(ass_path)},"
                 f"fade=t=out:st={max(0, total - 0.45):.3f}:d=0.45,format=yuv420p[vout];"
                 f"[1:a]aformat=sample_rates={SR}:channel_layouts=stereo,atrim=0:{total:.3f},asetpts=PTS-STARTPTS,"
                 f"afade=t=out:st={max(0, total - 1.2):.3f}:d=1.2[mus];"
                 f"[2:a]aformat=sample_rates={SR}:channel_layouts=stereo,atrim=0:{total:.3f}[sfx];"
                 f"[mus][sfx]amix=inputs=2:duration=first:normalize=0,"
                 f"loudnorm=I=-14:TP=-1.0:LRA=11,aresample=48000[aout]")
        log("🎞️  Render final: color + pulso al beat + texto + música + SFX...")
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst,
             "-stream_loop", "-1", "-i", music_path, "-i", sfx_path,
             "-filter_complex", graph, "-map", "[vout]", "-map", "[aout]", "-t", f"{total:.3f}",
             "-c:v", "libx264", "-preset", os.getenv("VF_PRESET", "fast"), "-crf", "18", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", output_path])
        log(f"✅ Éxito total: {output_path} ({W}x{H}, {total:.1f}s)")
    except Exception as e:
        log(f"❌ Error en el motor: {e}")
        sys.exit(1)
    finally:
        shutil.rmtree(work, ignore_errors=True)
