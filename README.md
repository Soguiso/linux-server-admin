# 🚀 Linux Server Admin (Beta)

<p align="center">
  <img src="https://raw.githubusercontent.com/walkxcode/dashboard-icons/main/png/docker.png" width="90" alt="Linux Server Admin Logo" />
</p>

<p align="center">
  <b>Painel moderno, leve e unificado para administração de servidores Linux e orquestração Docker, com Agente IA Copiloto (Google Gemini) e Auto-Discovery de containers.</b>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white" />
  <img src="https://img.shields.io/badge/Python_3.11-3776AB?style=for-the-badge&logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/Nginx-009639?style=for-the-badge&logo=nginx&logoColor=white" />
  <img src="https://img.shields.io/badge/Google_Gemini-8E75C2?style=for-the-badge&logo=google&logoColor=white" />
  <img src="https://img.shields.io/badge/License-MIT-green.svg?style=for-the-badge" />
</p>

---

## 🌟 Visão Geral

O **Linux Server Admin** foi desenvolvido para centralizar a gestão do seu servidor doméstico ou VPS em um único painel visual moderno (Dark Glassmorphism), eliminando a necessidade de múltiplos terminais e aplicações separadas.

Empacotado em um **único container Docker autossuficiente**, ele pode ser implantado em qualquer máquina Linux em menos de 1 minuto.

---

## ✨ Principais Funcionalidades

### 1. 🌐 Central de Aplicações & Auto-Discovery
- **Auto-Discovery com Notificações:** Sempre que um novo container Docker com porta web for iniciado, o painel detecta automaticamente e exibe uma notificação interativa com **1 clique para adicionar aos atalhos**.
- **Logos Oficiais em Alta Definição:** Suporte nativo a ícones vetoriais de aplicações self-hosted (*Portainer, Authentik, Nginx Proxy Manager, n8n, Uptime Kuma, Netdata, etc.*).
- **Atalhos Personalizáveis:** Adicione, edite ou remova serviços manualmente pelo botão **`＋ Adicionar Serviço`**.
- **Detecção Inteligente de Host:** Os links adaptam-se automaticamente ao IP ou domínio pelo qual você acessa (LAN, Tailscale ou DNS público).

### 2. ⚡ Telemetria em Tempo Real
- Média de carga da CPU (*Load Average* de 1m, 5m e 15m).
- Consumo e total de **Memória RAM** com percentual dinâmico.
- Uso de **Armazenamento em Disco** da partição raiz.
- Tempo de atividade ininterrupto (*Uptime*).

### 3. 🐳 Gestão Completa de Containers Docker
- Listagem em tempo real de todos os containers do servidor host através do socket `/var/run/docker.sock`.
- Indicadores visuais de status (*Rodando* / *Parado*).
- Ações rápidas com 1 clique: **Iniciar**, **Parar**, **Reiniciar** e **Visualizador de Logs ao Vivo**.

### 4. 🤖 Agente Administrador IA (Google Gemini)
- Copiloto com *Function Calling* integrado diretamente ao sistema.
- Peça diagnósticos, status de serviços, inspeções de logs ou tarefas de manutenção em linguagem natural (Português).
- Integração opcional com **Bot do Telegram** para suporte remoto pelo celular.

### 5. 💻 Terminal Web Integrado
- Terminal bash interativo direto pelo navegador com navegação de diretórios (`cd`, `ls -la`, etc.) e comandos rápidos.

### 6. 👥 Controle de Usuários
- **Acesso Web (.htpasswd):** Crie e gerencie senhas de acesso HTTP para proteger o painel através do Nginx.
- **Usuários do Sistema (Linux):** Crie novas contas de usuário locais com opções de privilégios de **Administrador (`sudo`)** e **Gerenciador Docker (`docker`)** em 1 clique.

---

## 🚀 Instalação Rápida (Qualquer Servidor com Docker)

### 1. Clonar o Repositório
```bash
git clone https://github.com/Soguiso/linux-server-admin.git
cd linux-server-admin
```

### 2. Configurar o Arquivo de Ambiente (.env)
```bash
cp .env.example .env
nano .env
```

Ajuste as variáveis conforme a sua preferência:
```ini
# Porta em que o painel responderá no servidor host (padrão: 8888)
PORT=8888

# Nome do servidor exibido no cabeçalho
SERVER_NAME="Meu Servidor Linux"

# Chave de API Google Gemini (opcional, para ativar o Agente IA)
# Obtenha gratuitamente em: https://aistudio.google.com/
GEMINI_API_KEY=""
GEMINI_MODEL="gemini-1.5-flash"

# Notificações no Telegram (opcional)
TELEGRAM_BOT_TOKEN=""
TELEGRAM_CHAT_ID=""
```

### 3. Iniciar o Painel
```bash
docker compose up -d
```

Pronto! Acesse pelo navegador:
👉 **`http://SEU-IP:8888`**

---

## 🔒 Acesso Externo e HTTPS Recomendado

Para acessar de forma segura fora da rede local:

1. **Via Nginx Proxy Manager (Recomendado):**
   - Crie um *Proxy Host* apontando para `http://IP_DO_SERVIDOR:8888` (ou `http://linux-server-admin:80`).
   - Ative o certificado SSL Let's Encrypt e a opção **Websockets Support**.
2. **Via Tailscale Funnel:**
   ```bash
   sudo tailscale funnel --bg 8888
   ```

---

## 📁 Estrutura do Projeto

```text
linux-server-admin/
├── Dockerfile                  # Imagem unificada (Debian 12 + Python 3.11 + Nginx + Docker CLI)
├── docker-compose.yml          # Manifesto Compose com volumes persistentes
├── entrypoint.sh               # Script de inicialização dos serviços internos
├── .env.example                # Modelo de variáveis de ambiente
├── .gitignore                  # Proteção de credenciais e dados locais
├── README.md                   # Documentação do projeto
├── config/
│   └── nginx.conf              # Proxy reverso interno e servidor SPA
├── app/
│   ├── backend/
│   │   ├── api.py              # Backend REST, Agente IA e Docker Engine
│   │   └── requirements.txt    # Dependências mínimas
│   └── frontend/
│       └── index.html          # Interface responsiva Glassmorphism
└── data/                       # Diretório persistente montado no host
    ├── services.example.json   # Template de serviços cadastrados
    ├── services.json           # Serviços salvos dinamicamente (ignorado pelo git)
    └── dismissed_services.json # Containers dispensados do auto-discovery
```

---

## 📄 Licença

Distribuído sob a licença **MIT**. Consulte o arquivo `LICENSE` para mais informações.
