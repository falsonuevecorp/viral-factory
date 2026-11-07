# Biblioteca de música

El motor elige la música según el *mood* del template:

| Template  | Mood          | Carpeta               |
|-----------|---------------|-----------------------|
| cinematic | emocional     | `music/emocional/`    |
| hormozi   | motivacional  | `music/motivacional/` |
| neon      | oscura        | `music/oscura/`       |

Pon pistas (`.mp3`, `.wav`, `.m4a`, `.aac`, `.ogg`, `.flac`) en la carpeta del mood. El motor
elige una de forma estable según el nombre del video, la repite en bucle si hace falta, le hace
fade in/out y la baja automáticamente cuando alguien habla (ducking).

Si la carpeta está vacía, el motor **genera** una pista original libre de derechos con la
progresión del mood (por ejemplo, vi–IV–I–V para la emocional).

Usa sólo música con licencia para redes sociales (por ejemplo, tu propia música o bibliotecas
royalty-free con licencia comercial). Las plataformas silencian o bloquean videos con música
con copyright.

Opciones de línea de comandos: `--music none` (sin música), `--music ruta/al/archivo.mp3`,
o la variable de entorno `VF_MUSIC_DIR` para usar otra carpeta.
