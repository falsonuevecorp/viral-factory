#!/bin/bash
cd "/Users/armandoluzardo/Documents/Gemi ANT/viral-factory"
echo "🚀 Iniciando Lanzamiento de ViralFactory..."
git init
git add .
git commit -m "Lanzamiento Sovereign OS ViralFactory"
git branch -M main
git remote add origin https://github.com/falsonuevecorp/viral-factory.git
git push -u origin main --force
echo "✅ ¡Código en GitHub! Avisa a Antigravity para terminar el despliegue."
