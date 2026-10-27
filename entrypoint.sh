#!/bin/bash
set -e

echo "=== Iniciando Linux Server Admin (Beta) ==="
mkdir -p /config
touch /config/.htpasswd

# Se n?o houver nginx.conf customizado em /config, copia o padr?o
if [ ! -f /etc/nginx/nginx.conf ]; then
    cp /app/config/nginx.conf /etc/nginx/nginx.conf
fi

# Inicia o Nginx em background
nginx &
NGINX_PID=$!
echo "Nginx iniciado (PID $NGINX_PID)"

# Fun??o para desligar processos ao parar o container
trap "kill -TERM $NGINX_PID; exit 0" SIGTERM SIGINT

# Inicia a API Python em primeiro plano
python3 /app/backend/api.py
