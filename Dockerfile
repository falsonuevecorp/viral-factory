# Usar una imagen de Python + Node.js (Imagen Híbrida)
FROM nikolaik/python-nodejs:latest

# Instalar FFmpeg (Vital para procesamiento de video)
RUN apt-get update && apt-get install -y ffmpeg imagemagick

# Directorio de trabajo
WORKDIR /app

# Copiar archivos de configuración
COPY package*.json ./
COPY requirements.txt ./

# Instalar dependencias
RUN npm install
RUN pip3 install -r requirements.txt

# Copiar el resto del código
COPY . .

# Crear carpetas necesarias
RUN mkdir -p uploads outputs

# Exponer el puerto
EXPOSE 5000

# Comando de inicio
CMD ["npm", "start"]
