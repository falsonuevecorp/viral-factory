# Usar una imagen que ya tiene Node y Python (Ahorra tiempo y RAM)
FROM nikolaik/python-nodejs:python3.10-nodejs18

# FFmpeg (con libass para subtítulos ASS) y fuentes para el motor de edición
RUN apt-get update && apt-get install -y \
    ffmpeg \
    fonts-liberation \
    fontconfig \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copiar archivos de dependencias primero para cachear capas
COPY package.json requirements.txt ./
RUN npm install
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el resto del proyecto
COPY . .

# Puerto dinámico
ENV PORT=8080
EXPOSE 8080

CMD ["npm", "start"]
