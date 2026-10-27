#!/bin/bash
set -e

echo "=== Iniciando Linux Server Admin ==="
mkdir -p /config

# Sempre copia /config/nginx.conf (do volume do host) para /etc/nginx/nginx.conf
if [ -f /config/nginx.conf ]; then
    echo "=== [Config] Sincronizando /config/nginx.conf para /etc/nginx/ ==="
    cp -f /config/nginx.conf /etc/nginx/nginx.conf
else
    echo "=== [Config] Inicializando /config/nginx.conf padrao ==="
    cp -f /app/config/nginx.conf /etc/nginx/nginx.conf
    cp -f /app/config/nginx.conf /config/nginx.conf
fi

# Gerenciamento de Usuário Web Inicial (.htpasswd)
if [ ! -f /config/.htpasswd ] || [ ! -s /config/.htpasswd ]; then
    ADMIN_USER="${WEB_USER:-admin}"
    ADMIN_PASS="${WEB_PASSWORD:-admin}"
    echo "=== [Seguranca] Criando usuario web inicial: ${ADMIN_USER} ==="
    htpasswd -bc /config/.htpasswd "$ADMIN_USER" "$ADMIN_PASS"
    echo "=== [Seguranca] Acesso ao painel protegido com HTTP Basic Auth ==="
    echo "=== [Seguranca] Usuario: ${ADMIN_USER} | Senha inicial: ${ADMIN_PASS} ==="
fi

# Desativa autenticacao se explicitamente solicitado (ex: uso de SSO externo / Authentik)
if [ "${DISABLE_AUTH:-false}" = "true" ]; then
    echo "=== [Aviso] Autenticacao basica desativada via DISABLE_AUTH=true ==="
    sed -i '/auth_basic/d' /etc/nginx/nginx.conf
fi

# Inicia o Nginx em background
nginx &
NGINX_PID=$!
echo "Nginx iniciado (PID $NGINX_PID)"

# Funcao para desligar processos ao parar o container
trap "kill -TERM $NGINX_PID; exit 0" SIGTERM SIGINT

# Inicia a API Python em primeiro plano
python3 /app/backend/api.py
