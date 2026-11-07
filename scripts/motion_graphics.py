"""
Motion graphics automáticos (estilo "explainer") renderizados como ASS vectorial.

Tipos:
  - stat:  tarjeta glass sobre el video con etiqueta + cifra que cuenta hacia arriba + mini gráfica.
  - board: pantalla blanca a pantalla completa con etiquetas en caja negra, cifras que se
           escriben solas y barras que crecen (comparación de valores).

Los momentos se eligen con un LLM (OPENAI_API_KEY) a partir de la transcripción, o con
heurísticas (cifras, %, $) si no hay API key.
"""
import json
import os
import re

NUM_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(%|\$|€|k|mil|millones)?", re.I)


# ---------------------------------------------------------------------------
# Selección de momentos
# ---------------------------------------------------------------------------
def _sentences(words):
    out, cur = [], []
    for w in words:
        cur.append(w)
        if re.search(r"[.!?…]$", w["word"]) or len(cur) >= 25:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def _value_tokens(sentence):
    vals = []
    for i, w in enumerate(sentence):
        raw = w["word"].strip(" ,.;:¿?¡!")
        m = re.fullmatch(r"\$?(\d+(?:[.,]\d+)?)\s*(%|\$|€)?", raw)
        if m:
            unit = m.group(2) or ("$" if raw.startswith("$") else "")
            nxt = sentence[i + 1]["word"].lower().strip(" ,.") if i + 1 < len(sentence) else ""
            if not unit and nxt in ("%", "por", "dólares", "dolares", "euros"):
                unit = {"%": "%", "por": "%", "euros": "€"}.get(nxt, "$")
            vals.append((i, m.group(1), unit))
    return vals


STOP = {"el", "la", "los", "las", "un", "una", "de", "del", "al", "a", "en", "y", "que", "es", "por",
        "con", "para", "te", "se", "lo", "su", "tu", "mi", "the", "of", "a", "to", "is"}


def _label_before(sentence, idx):
    for j in range(idx - 1, max(-1, idx - 5), -1):
        tok = re.sub(r"[^\wáéíóúñü]", "", sentence[j]["word"].lower())
        if len(tok) > 3 and tok not in STOP:
            return tok.capitalize()
    return "Dato"


def heuristic_specs(words, total):
    specs, last = [], -99.0
    for sent in _sentences(words):
        vals = _value_tokens(sent)
        if not vals:
            continue
        start = sent[vals[0][0]]["start"]
        if start - last < 3.5 or start > total - 2:
            continue
        uniq = {(v, u) for _, v, u in vals}
        if len(uniq) >= 2:
            vals = [x for k, x in enumerate(vals) if (x[1], x[2]) not in {(y[1], y[2]) for y in vals[:k]}]
            items = [{"label": _label_before(sent, i), "value": f"{v}{u}"} for i, v, u in vals[:2]]
            if items[0]["label"] == items[1]["label"]:          # "pasa de 70 a 30" -> antes / después
                items[0]["label"], items[1]["label"] = "Antes", "Después"
            specs.append({"type": "board", "start": start, "duration": 3.2, "items": items})
        else:
            i, v, u = vals[0]
            specs.append({"type": "stat", "start": start, "duration": 2.6,
                          "label": _label_before(sent, i), "value": f"{v}{u}"})
        last = start
        if len(specs) >= 5:
            break
    return specs


def llm_specs(words, total):
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    lines = [f"[{s[0]['start']:.1f}s] " + " ".join(w["word"] for w in s) for s in _sentences(words)]
    prompt = (
        "Eres un motion designer de videos virales educativos. A partir de la transcripción con "
        "tiempos, elige de 2 a 5 momentos donde un gráfico ayude a entender (cifras, porcentajes, "
        "comparaciones, conceptos clave). Separa los gráficos al menos 6 segundos. Devuelve JSON: "
        '{"graphics":[{"type":"stat","start":seg,"duration":2.5,"label":"Descuento","value":"20%"},'
        '{"type":"board","start":seg,"duration":3.2,"items":[{"label":"PRODUCTO","value":"200$"},'
        '{"label":"COSTE","value":"130$"}]}]}. "stat" = un dato sobre el video; "board" = pantalla '
        "blanca con 1-2 elementos (label corto en 1-2 palabras, value corto). Usa el idioma del video.\n\n"
        + "\n".join(lines)
    )
    resp = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.3,
    )
    data = json.loads(resp.choices[0].message.content)
    specs = []
    for g in data.get("graphics", [])[:5]:
        if g.get("type") not in ("stat", "board"):
            continue
        g["start"] = max(0.0, min(float(g.get("start", 0)), total - 2))
        g["duration"] = max(1.8, min(float(g.get("duration", 2.8)), 4.5))
        specs.append(g)
    return specs


def pick_graphics(words, total, log=print):
    if os.getenv("OPENAI_API_KEY"):
        try:
            specs = llm_specs(words, total)
            if specs:
                return specs
        except Exception as e:  # el video sale igual, con heurísticas
            log(f"⚠️  LLM de gráficos falló ({e}); usando heurísticas.")
    return heuristic_specs(words, total)


# ---------------------------------------------------------------------------
# Render ASS
# ---------------------------------------------------------------------------
def _t(sec):
    cs = int(round(max(0.0, sec) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _rrect(w, h, r):
    r = min(r, w / 2, h / 2)
    return (f"m {r:.0f} 0 l {w - r:.0f} 0 b {w:.0f} 0 {w:.0f} 0 {w:.0f} {r:.0f} l {w:.0f} {h - r:.0f} "
            f"b {w:.0f} {h:.0f} {w:.0f} {h:.0f} {w - r:.0f} {h:.0f} l {r:.0f} {h:.0f} b 0 {h:.0f} 0 {h:.0f} 0 {h - r:.0f} "
            f"l 0 {r:.0f} b 0 0 0 0 {r:.0f} 0")


def _circle(r):
    k = r * 0.5523
    return (f"m 0 {-r:.1f} b {k:.1f} {-r:.1f} {r:.1f} {-k:.1f} {r:.1f} 0 b {r:.1f} {k:.1f} {k:.1f} {r:.1f} 0 {r:.1f} "
            f"b {-k:.1f} {r:.1f} {-r:.1f} {k:.1f} {-r:.1f} 0 b {-r:.1f} {-k:.1f} {-k:.1f} {-r:.1f} 0 {-r:.1f}")


def _ev(layer, a, b, text):
    return f"Dialogue: {layer},{_t(a)},{_t(b)},MG,,0,0,0,,{text}"


def _count_steps(value, steps=8):
    m = re.match(r"^(\D*)(\d+(?:[.,]\d+)?)(.*)$", value)
    if not m:
        return [value]
    pre, num, post = m.groups()
    target = float(num.replace(",", "."))
    dec = len(num.split(".")[1]) if "." in num else 0
    out = []
    for k in range(1, steps + 1):
        v = target * (1 - (1 - k / steps) ** 2)
        out.append(f"{pre}{v:.{dec}f}{post}" if dec else f"{pre}{int(round(v))}{post}")
    out[-1] = value
    return out


def stat_events(g, W, H):
    a, b = g["start"], g["start"] + g["duration"]
    cw, ch = W * 0.60, H * 0.105
    x0, y0 = (W - cw) / 2, H * 0.40
    fade = "\\fad(180,160)"
    mv = lambda x, y: f"\\move({x:.0f},{y + 18:.0f},{x:.0f},{y:.0f},0,220)"
    ev = [
        # tarjeta glass: fondo oscuro translúcido + borde claro
        _ev(5, a, b, f"{{\\an7{mv(x0, y0)}{fade}\\bord1.5\\shad0\\blur1\\1c&H151515&\\1a&H50&\\3c&HFFFFFF&\\3a&H90&\\p1}}"
                     f"{_rrect(cw, ch, ch * 0.28)}{{\\p0}}"),
        _ev(6, a, b, f"{{\\an7{mv(x0 + cw * 0.07, y0 + ch * 0.14)}{fade}\\fnInter Medium\\fs{W * 0.034:.0f}"
                     f"\\bord0\\shad0\\1c&HFFFFFF&\\1a&H30&}}{g.get('label', '')}"),
    ]
    steps = _count_steps(str(g.get("value", "")))
    step_t = 0.45 / len(steps)
    for k, val in enumerate(steps):
        sa = a + k * step_t
        sb = b if k == len(steps) - 1 else sa + step_t
        f = "\\fad(0,160)" if k == len(steps) - 1 else ""
        ev.append(_ev(6, sa, sb, f"{{\\an1\\pos({x0 + cw * 0.07:.0f},{y0 + ch * 0.92:.0f}){f}\\fnInter Bold"
                                  f"\\b0\\fs{W * 0.085:.0f}\\bord0\\shad0\\1c&HFFFFFF&}}{val}"))
    # mini gráfica: línea ascendente + punto que la recorre
    lx0, ly0, lx1, ly1 = x0 + cw * 0.58, y0 + ch * 0.78, x0 + cw * 0.92, y0 + ch * 0.22
    ev.append(_ev(6, a, b, f"{{\\an7\\pos(0,0){fade}\\bord1.2\\shad0\\1a&HFF&\\3c&HFFFFFF&\\p1}}"
                           f"m {lx0:.0f} {ly0:.0f} l {lx1:.0f} {ly1:.0f} l {lx1:.0f} {ly1 + 1:.0f} l {lx0:.0f} {ly0 + 1:.0f}{{\\p0}}"))
    ev.append(_ev(7, a, b, f"{{\\an7\\move({lx0:.0f},{ly0:.0f},{(lx0 + lx1) / 2 + cw * 0.08:.0f},{(ly0 + ly1) / 2 - ch * 0.08:.0f},150,900)"
                           f"{fade}\\bord0\\shad0\\1c&HFFFFFF&\\p1}}{_circle(W * 0.011)}{{\\p0}}"))
    return ev


def _box_label(text, cx, cy, size, a, b, W, delay=0.0, filled=True):
    tw = len(text) * size * 0.62 + size * 0.6
    th = size * 1.25
    x0 = cx - tw / 2
    y0 = cy - th / 2
    wipe = (f"\\clip({x0 - 2:.0f},{y0 - 2:.0f},{x0:.0f},{y0 + th + 2:.0f})"
            f"\\t({int(delay * 1000)},{int(delay * 1000) + 260},\\clip({x0 - 2:.0f},{y0 - 2:.0f},{x0 + tw + 2:.0f},{y0 + th + 2:.0f}))")
    if filled:
        box = f"\\bord0\\1c&H000000&"
        txt_color = "&HFFFFFF&"
    else:
        box = f"\\bord1.5\\1a&HFF&\\3c&H000000&"
        txt_color = "&H000000&"
    return [
        _ev(12, a, b, f"{{\\an7\\pos({x0:.0f},{y0:.0f}){wipe}\\shad0{box}\\p1}}m 0 0 l {tw:.0f} 0 l {tw:.0f} {th:.0f} l 0 {th:.0f}{{\\p0}}"),
        _ev(13, a, b, f"{{\\an5\\pos({cx:.0f},{cy:.0f}){wipe}\\fnLiberation Serif\\b{1 if filled else 0}\\fs{size:.0f}"
                      f"\\bord0\\shad0\\1c{txt_color}}}{text}"),
    ]


def _typewriter(text, x, y, size, a, b, delay, an=5, bold=0):
    ev, step = [], 0.06
    for k in range(1, len(text) + 1):
        sa = a + delay + (k - 1) * step
        sb = b if k == len(text) else sa + step
        ev.append(_ev(13, sa, sb, f"{{\\an{an}\\pos({x:.0f},{y:.0f})\\fnInter\\b{bold}\\fs{size:.0f}"
                                   f"\\bord0\\shad0\\1c&H000000&}}{text[:k]}"))
    return ev


def _num(value):
    m = re.search(r"\d+(?:[.,]\d+)?", value)
    return float(m.group().replace(",", ".")) if m else None


def board_events(g, W, H):
    a, b = g["start"], g["start"] + g["duration"]
    ev = [_ev(10, a, b, f"{{\\an7\\pos(0,0)\\fad(120,140)\\bord0\\shad0\\1c&HFFFFFF&\\p1}}"
                        f"m 0 0 l {W} 0 l {W} {H} l 0 {H}{{\\p0}}")]
    items = (g.get("items") or [])[:2]
    if not items:
        items = [{"label": g.get("label", ""), "value": g.get("value", "")}]
    nums = [_num(str(it.get("value", ""))) for it in items]
    if len(items) == 2 and all(n is not None for n in nums) and max(nums) > 0:
        # comparación: dos columnas con barras que crecen
        maxh = H * 0.30
        for k, (it, n) in enumerate(zip(items, nums)):
            cx = W * (0.30 if k == 0 else 0.70)
            delay = 0.15 + k * 0.7
            ev += _box_label(str(it["label"]).upper(), cx, H * 0.27, W * 0.062, a, b, W, delay)
            ev += _typewriter(str(it["value"]), cx, H * 0.36, W * 0.12, a, b, delay + 0.25)
            bw, bh = W * 0.24, max(H * 0.02, maxh * n / max(nums))
            grow = f"\\fscy0\\t({int((delay + 0.3) * 1000)},{int((delay + 0.9) * 1000)},0.6,\\fscy100)"
            ev.append(_ev(11, a, b, f"{{\\an2\\pos({cx:.0f},{H * 0.80:.0f}){grow}\\bord0\\shad0\\1c&H000000&\\p1}}"
                                    f"m 0 0 l {bw:.0f} 0 l {bw:.0f} {bh:.0f} l 0 {bh:.0f}{{\\p0}}"))
    else:
        for k, it in enumerate(items):
            cy = H * (0.42 if len(items) == 1 else 0.33 + k * 0.28)
            delay = 0.15 + k * 0.8
            ev += _box_label(str(it["label"]).upper(), W / 2, cy, W * 0.075, a, b, W, delay, filled=(k == 0))
            ev += _typewriter(str(it["value"]), W / 2, cy + W * 0.16, W * 0.15, a, b, delay + 0.3)
    return ev


def build_graphics_ass(specs, W, H):
    header = (
        "[Script Info]\nScriptType: v4.00+\nWrapStyle: 2\nScaledBorderAndShadow: yes\n"
        f"PlayResX: {W}\nPlayResY: {H}\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: MG,Inter,40,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events = []
    for g in specs:
        events += board_events(g, W, H) if g["type"] == "board" else stat_events(g, W, H)
    return header + "\n".join(events) + "\n"


def board_windows(specs):
    """Intervalos donde la pantalla blanca tapa el video (para ocultar subtítulos)."""
    return [(g["start"], g["start"] + g["duration"]) for g in specs if g["type"] == "board"]
