#!/bin/bash
# ==============================================================================
# Linux Server Admin & Telegram Bot - Instalador Automatizado Unificado
# ==============================================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${BLUE}==============================================================================${NC}"
echo -e "${CYAN}        🚀 INSTALADOR: LINUX SERVER ADMIN & TELEGRAM BOT COPILOT            ${NC}"
echo -e "${BLUE}==============================================================================${NC}"

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

# 1. Verificar Docker e Docker Compose
echo -e "\n${YELLOW}[1/5] Verificando pré-requisitos do sistema...${NC}"

if ! command -v docker &> /dev/null; then
    echo -e "${RED}Docker não encontrado!${NC} Deseja instalar o Docker automaticamente agora? (s/n)"
    read -r install_docker
    if [[ "$install_docker" =~ ^[sSyY]$ ]]; then
        echo -e "${CYAN}Instalando Docker oficial...${NC}"
        curl -fsSL https://get.docker.com | sh
        sudo usermod -aG docker "$USER" || true
    else
        echo -e "${RED}Erro: O Docker é obrigatório para executar o Linux Server Admin.${NC}"
        exit 1
    fi
fi

if docker compose version &> /dev/null; then
    DOCKER_COMPOSE_CMD="docker compose"
elif command -v docker-compose &> /dev/null; then
    DOCKER_COMPOSE_CMD="docker-compose"
else
    echo -e "${RED}Erro: Docker Compose não encontrado.${NC}"
    echo -e "Instale o plugin docker-compose-plugin do Docker."
    exit 1
fi

echo -e "${GREEN}✓ Docker e Docker Compose verificados com sucesso!${NC}"

# 2. Configuração do Arquivo .env
echo -e "\n${YELLOW}[2/5] Configurando variáveis de ambiente (.env)...${NC}"

if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        cp .env.example .env
    else
        cat << 'ENV_EOF' > .env
PORT=8888
SERVER_NAME="Meu Servidor Linux"
WEB_USER="admin"
WEB_PASSWORD="admin"
DISABLE_AUTH="false"
GEMINI_API_KEY=""
GEMINI_MODEL="gemini-3.5-flash"
TELEGRAM_BOT_TOKEN=""
TELEGRAM_CHAT_ID=""
ENV_EOF
    fi

    # Se estiver em terminal interativo, pergunta os valores essenciais
    if [ -t 0 ]; then
        echo -e "${CYAN}Configuração Rápida Interativa:${NC}"
        
        read -r -p "Porta Web externa [padrão: 8888]: " input_port
        [ -n "$input_port" ] && sed -i "s/^PORT=.*/PORT=$input_port/" .env

        read -r -p "Nome deste Servidor [padrão: $(hostname)]: " input_sname
        [ -n "$input_sname" ] && sed -i "s/^SERVER_NAME=.*/SERVER_NAME=\"$input_sname\"/" .env || sed -i "s/^SERVER_NAME=.*/SERVER_NAME=\"$(hostname)\"/" .env

        read -r -p "Usuário Web do Painel [padrão: admin]: " input_user
        [ -n "$input_user" ] && sed -i "s/^WEB_USER=.*/WEB_USER=\"$input_user\"/" .env

        read -r -p "Senha Web do Painel [padrão: admin]: " input_pass
        [ -n "$input_pass" ] && sed -i "s/^WEB_PASSWORD=.*/WEB_PASSWORD=\"$input_pass\"/" .env

        echo -e "\n${BLUE}--- Integração com IA (Google Gemini) ---${NC}"
        echo -e "Obtenha uma chave grátis em: ${CYAN}https://aistudio.google.com/${NC}"
        read -r -p "Chave API Gemini (deixe em branco se não tiver): " input_gemini
        [ -n "$input_gemini" ] && sed -i "s/^GEMINI_API_KEY=.*/GEMINI_API_KEY=\"$input_gemini\"/" .env

        echo -e "\n${BLUE}--- Integração com Bot do Telegram (Opcional) ---${NC}"
        echo -e "Crie seu bot pelo @BotFather no Telegram para obter o token."
        read -r -p "Token do Bot Telegram (deixe em branco se não quiser): " input_tg_token
        if [ -n "$input_tg_token" ]; then
            sed -i "s/^TELEGRAM_BOT_TOKEN=.*/TELEGRAM_BOT_TOKEN=\"$input_tg_token\"/" .env
            echo -e "${YELLOW}Dica: Descubra seu Chat ID numérico enviando /start para o bot @userinfobot no Telegram.${NC}"
            read -r -p "Seu Chat ID numérico do Telegram: " input_tg_chat
            [ -n "$input_tg_chat" ] && sed -i "s/^TELEGRAM_CHAT_ID=.*/TELEGRAM_CHAT_ID=\"$input_tg_chat\"/" .env
        fi
    fi
    echo -e "${GREEN}✓ Arquivo .env criado com sucesso!${NC}"
else
    echo -e "${GREEN}✓ Arquivo .env já existe, mantendo configurações atuais.${NC}"
fi

# 3. Criação de Pastas de Dados e Permissões
echo -e "\n${YELLOW}[3/5] Preparando diretórios persistentes...${NC}"
mkdir -p data config
echo -e "${GREEN}✓ Diretórios 'data/' e 'config/' prontos.${NC}"

# 4. Construção e Inicialização dos Containers
echo -e "\n${YELLOW}[4/5] Iniciando os serviços desacoplados (Dashboard + Telegram Bot)...${NC}"
$DOCKER_COMPOSE_CMD up -d --build

# 5. Validação do Status dos Serviços
echo -e "\n${YELLOW}[5/5] Verificando status dos containers...${NC}"
sleep 3

PORT_ACTUAL=$(grep -E '^PORT=' .env | cut -d '=' -f2 | tr -d '"' | tr -d "'" || echo "8888")
[ -z "$PORT_ACTUAL" ] && PORT_ACTUAL="8888"

SERVER_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "127.0.0.1")

echo -e "\n${BLUE}==============================================================================${NC}"
echo -e "${GREEN}🎉 INSTALAÇÃO CONCLUÍDA COM SUCESSO!${NC}"
echo -e "${BLUE}==============================================================================${NC}"
echo -e "🌐 ${CYAN}Painel de Administração Web:${NC}"
echo -e "   URL Local:   ${GREEN}http://localhost:${PORT_ACTUAL}${NC}"
echo -e "   URL na Rede: ${GREEN}http://${SERVER_IP}:${PORT_ACTUAL}${NC}"
echo -e ""
echo -e "🤖 ${CYAN}Status dos Serviços Desacoplados:${NC}"
$DOCKER_COMPOSE_CMD ps
echo -e ""
echo -e "💡 ${CYAN}Comandos Úteis de Gerenciamento:${NC}"
echo -e "   • Reiniciar apenas o Painel Web:    ${YELLOW}$DOCKER_COMPOSE_CMD restart linux-server-admin${NC}"
echo -e "   • Reiniciar apenas o Telegram Bot: ${YELLOW}$DOCKER_COMPOSE_CMD restart server-telegram-bot${NC}"
echo -e "   • Ver logs em tempo real:           ${YELLOW}$DOCKER_COMPOSE_CMD logs -f${NC}"
echo -e "   • Parar todos os serviços:          ${YELLOW}$DOCKER_COMPOSE_CMD down${NC}"
echo -e "${BLUE}==============================================================================${NC}"
