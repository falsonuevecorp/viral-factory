# Usar una imagen que ya tiene Node y Python (Ahorra tiempo y RAM)
FROM nikolaik/python-nodejs:python3.10-nodejs18

# Instalar ImageMagick y fuentes para MoviePy
RUN apt-get update && apt-get install -y \
    imagemagick \
    fonts-liberation \
    fontconfig \
    && rm -rf /var/lib/apt/lists/*

# Configurar ImageMagick para que permita el procesado de MoviePy
RUN sed -i 's/domain="coder" rights="none" pattern="PDF"/domain="coder" rights="read|write" pattern="PDF"/' /etc/ImageMagick-6/policy.xml \
    && sed -i 's/domain="coder" rights="none" pattern="LABEL"/domain="coder" rights="read|write" pattern="LABEL"/' /etc/ImageMagick-6/policy.xml \
    && sed -i 's/domain="path" rights="none" pattern="@\*"/domain="path" rights="read|write" pattern="@\*"/' /etc/ImageMagick-6/policy.xml

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
