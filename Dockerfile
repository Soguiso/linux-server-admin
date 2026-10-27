FROM python:3.11-slim-bookworm

LABEL maintainer="Linux Server Admin"
LABEL description="Painel Unificado de Administra??o Linux & Containers"

# Instala Nginx, Docker CLI, ferramentas de sistema e utilit?rios
RUN apt-get update && apt-get install -y --no-install-recommends \
    nginx \
    docker.io \
    apache2-utils \
    procps \
    curl \
    bash \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Cria diret?rios de dados e configura??es persistentes
RUN mkdir -p /data /config /app/frontend /app/backend /app/config

# Copia configura??es e arquivos da aplica??o
COPY config/nginx.conf /etc/nginx/nginx.conf
COPY config/nginx.conf /app/config/nginx.conf
COPY app/frontend/ /app/frontend/
COPY app/backend/ /app/backend/
COPY entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh /app/backend/api.py

EXPOSE 80

VOLUME ["/data", "/config"]

ENTRYPOINT ["/entrypoint.sh"]
