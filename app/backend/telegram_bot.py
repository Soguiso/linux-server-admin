#!/usr/bin/env python3
"""
Linux Server Admin - Backend API & Agent
Portable Server Administration Microservice
"""

import os
import sys
import json
import time
import socket
import urllib.request
import urllib.error
import subprocess
import threading
import platform
import re
import html
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

# Prefer IPv4
_orig_getaddrinfo = socket.getaddrinfo
def _ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if family == 0:
        family = socket.AF_INET
    return _orig_getaddrinfo(host, port, family, type, proto, flags)
socket.getaddrinfo = _ipv4_getaddrinfo

# Environments & Paths
SERVER_NAME = os.environ.get("SERVER_NAME", platform.node())
DATA_DIR = os.environ.get("DATA_DIR", "/data")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
SERVICES_FILE = os.path.join(DATA_DIR, "services.json")
HTPASSWD_PATH = os.environ.get("HTPASSWD_PATH", "/config/.htpasswd")
API_PORT = int(os.environ.get("API_PORT", 8765))
DEFAULT_WORKDIR = os.environ.get("DEFAULT_WORKDIR", "/workspace" if os.path.exists("/workspace") else "/root")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(os.path.dirname(HTPASSWD_PATH), exist_ok=True)
if not os.path.exists(HTPASSWD_PATH) or os.path.getsize(HTPASSWD_PATH) == 0:
    admin_user = os.environ.get("WEB_USER", "admin")
    admin_pass = os.environ.get("WEB_PASSWORD", "admin")
    try:
        subprocess.run(["htpasswd", "-bc", HTPASSWD_PATH, admin_user, admin_pass], capture_output=True, text=True, timeout=10)
        print(f"[Segurança] Criado usuário web inicial: {admin_user}")
    except Exception as e:
        print(f"[Segurança] Erro ao criar .htpasswd inicial: {e}")

# Default Services Template if services.json does not exist
# Clean default for new server installs (Auto-Discovery will detect real running containers)
DEFAULT_SERVICES = []

def is_host_accessible() -> bool:
    """Checks if container has privileged/nsenter access to the host system."""
    try:
        res = subprocess.run(
            ["nsenter", "-t", "1", "-m", "-u", "-i", "-n", "-p", "--", "true"],
            capture_output=True,
            timeout=2
        )
        return res.returncode == 0
    except Exception:
        return False

def host_cmd(args):
    """Executes command on host OS via nsenter if accessible, else locally."""
    if is_host_accessible():
        return ["nsenter", "-t", "1", "-m", "-u", "-i", "-n", "-p", "--"] + list(args)
    return list(args)

def get_host_os() -> str:
    for p in ["/host/os-release", "/etc/os-release"]:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.startswith("PRETTY_NAME="):
                            return line.split("=", 1)[1].strip().strip('"')
            except Exception:
                pass
    return f"{platform.system()} {platform.release()}"



GEMINI_TOOLS = [
    {
        "functionDeclarations": [
            {
                "name": "run_bash",
                "description": "Executa um comando bash como root no servidor Linux. Use para checar logs, gerenciar Docker, inspecionar arquivos, diagnosticar recursos.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "command": {
                            "type": "STRING",
                            "description": "Comando bash a ser executado."
                        },
                        "cwd": {
                            "type": "STRING",
                            "description": "Diretório de trabalho opcional."
                        }
                    },
                    "required": ["command"]
                }
            },
            {
                "name": "read_file",
                "description": "Lê o conteúdo de um arquivo de texto no servidor.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "path": {
                            "type": "STRING",
                            "description": "Caminho absoluto do arquivo."
                        }
                    },
                    "required": ["path"]
                }
            },
            {
                "name": "write_file",
                "description": "Cria ou substitui um arquivo de texto no servidor.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "path": {
                            "type": "STRING",
                            "description": "Caminho absoluto do arquivo."
                        },
                        "content": {
                            "type": "STRING",
                            "description": "Conteúdo a ser gravado."
                        }
                    },
                    "required": ["path", "content"]
                }
            }
        ]
    }
]

def execute_agent_tool(name: str, args: dict) -> dict:
    try:
        if name == "run_bash":
            cmd = args.get("command", "")
            cwd = args.get("cwd") or DEFAULT_WORKDIR
            wrapped = f'cd "{cwd}" 2>/dev/null || cd /; {cmd}'
            full_cmd = host_cmd(["bash", "-c", wrapped])
            res = subprocess.run(full_cmd, capture_output=True, text=True, timeout=45)
            return {"exit_code": res.returncode, "stdout": res.stdout[-6000:], "stderr": res.stderr[-4000:]}
        elif name == "read_file":
            path = args.get("path", "")
            if not os.path.exists(path):
                return {"error": f"Arquivo não encontrado: {path}"}
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return {"content": f.read(12000)}
        elif name == "write_file":
            path = args.get("path", "")
            content = args.get("content", "")
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return {"success": True, "bytes_written": len(content)}
        else:
            return {"error": f"Ferramenta desconhecida: {name}"}
    except Exception as e:
        return {"error": str(e)}

def run_gemini_agent(channel: str, user_message: str, reset: bool = False) -> dict:
    cfg = load_config()
    api_key = cfg.get("gemini_api_key", "").strip()
    if not api_key:
        return {
            "reply": "⚠️ Nenhuma chave da API Google Gemini configurada. Adicione sua chave nas configurações para ativar o Agente Administrador.",
            "steps": []
        }

    model = cfg.get("gemini_model", "gemini-3.5-flash")
    candidate_models = [model]
    for fb in ["gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-flash-latest"]:
        if fb not in candidate_models:
            candidate_models.append(fb)
    current_model_idx = 0

    system_instruction = f"""Você é o Agente Administrador do Servidor Linux '{SERVER_NAME}', um copiloto inteligente de infraestrutura e DevOps.
Você possui acesso root através das ferramentas `run_bash`, `read_file` e `write_file`.
Sempre que o usuário pedir status de containers, diagnósticos, alteração de configs ou tarefas de sistema, use as ferramentas proativamente antes de responder.
Responda em português de forma clara, direta e objetiva."""

    with CHAT_LOCK:
        if reset or channel not in CHAT_HISTORY:
            CHAT_HISTORY[channel] = []
        history = CHAT_HISTORY[channel]
        history.append({
            "role": "user",
            "parts": [{"text": user_message}]
        })
        if len(history) > 30:
            history = history[-30:]
            CHAT_HISTORY[channel] = history

    steps_taken = []
    current_contents = list(history)

    for step in range(5):
        payload = {
            "contents": current_contents,
            "tools": GEMINI_TOOLS,
            "systemInstruction": {
                "parts": [{"text": system_instruction}]
            },
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 2048
            }
        }
        data = json.dumps(payload).encode("utf-8")
        result = None
        last_error = ""
        while current_model_idx < len(candidate_models):
            active_model = candidate_models[current_model_idx]
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{active_model}:generateContent?key={api_key}"
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=45) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
                    break
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8", errors="replace")
                last_error = f"Erro na API Gemini ({e.code}): {err_body}"
                if e.code in (503, 500, 404, 429) and current_model_idx + 1 < len(candidate_models):
                    next_model = candidate_models[current_model_idx + 1]
                    print(f"[{SERVER_NAME}] Gemini {active_model} falhou ({e.code}). Tentando fallback para {next_model}...")
                    current_model_idx += 1
                    continue
                return {"reply": last_error, "steps": steps_taken}
            except Exception as e:
                last_error = f"Erro de conex?o com Gemini: {e}"
                if current_model_idx + 1 < len(candidate_models):
                    next_model = candidate_models[current_model_idx + 1]
                    print(f"[{SERVER_NAME}] Gemini {active_model} falhou ({e}). Tentando fallback para {next_model}...")
                    current_model_idx += 1
                    continue
                return {"reply": last_error, "steps": steps_taken}

        candidates = result.get("candidates", [])
        if not candidates:
            return {"reply": "Não recebi resposta do modelo.", "steps": steps_taken}

        content_obj = candidates[0].get("content", {})
        parts = content_obj.get("parts", [])
        current_contents.append(content_obj)

        function_calls = [p["functionCall"] for p in parts if "functionCall" in p]
        if not function_calls:
            text_parts = [p["text"] for p in parts if "text" in p]
            final_reply = "".join(text_parts).strip()
            with CHAT_LOCK:
                history.append({
                    "role": "model",
                    "parts": [{"text": final_reply}]
                })
            return {"reply": final_reply, "steps": steps_taken}

        tool_responses = []
        for fc in function_calls:
            fn_name = fc.get("name", "")
            fn_args = fc.get("args", {})
            steps_taken.append({"tool": fn_name, "args": fn_args})
            tool_res = execute_agent_tool(fn_name, fn_args)
            tool_responses.append({
                "functionResponse": {
                    "name": fn_name,
                    "response": tool_res
                }
            })

        current_contents.append({
            "role": "user",
            "parts": tool_responses
        })

    return {"reply": "Concluí as operações solicitadas no servidor.", "steps": steps_taken}

# =====================================================================
# HTTP API HANDLER
# =====================================================================

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


# TELEGRAM BOT & DOCKER EVENTS MONITOR
# =====================================================================

def load_config() -> dict:
    cfg = {
        "gemini_api_key": os.environ.get("GEMINI_API_KEY", ""),
        "gemini_model": os.environ.get("GEMINI_MODEL", "gemini-3.5-flash"),
        "telegram_bot_token": os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        "telegram_chat_id": os.environ.get("TELEGRAM_CHAT_ID", ""),
        "server_name": SERVER_NAME
    }
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
                cfg.update(saved)
        except Exception as e:
            print(f"Erro ao ler config: {e}")
    return cfg

def save_config(new_data: dict):
    cfg = load_config()
    cfg.update(new_data)
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        print(f"Erro ao salvar config: {e}")

def get_telegram_creds():
    cfg = load_config()
    token = cfg.get("telegram_bot_token") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = cfg.get("telegram_chat_id") or os.environ.get("TELEGRAM_CHAT_ID", "")
    try:
        chat_id_int = int(str(chat_id).strip())
    except (ValueError, TypeError):
        chat_id_int = None
    return token.strip(), chat_id_int

def markdown_to_telegram_html(md: str) -> str:
    """Converte Markdown do Gemini para tags HTML nativas suportadas pelo Telegram."""
    if not md:
        return ""
    text = html.escape(md)
    # Fenced code
    text = re.sub(r"```(?:\w+)?\n?(.*?)```", r"<pre>\1</pre>", text, flags=re.DOTALL)
    # Inline code
    text = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", text)
    # Bold **text**
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__([^_]+)__", r"<b>\1</b>", text)
    # Italic *text*
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", text)
    # Headers ###
    text = re.sub(r"^#{1,6}\s*(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)
    # List bullets * or -
    text = re.sub(r"^[\*\-]\s+", "• ", text, flags=re.MULTILINE)
    return text

def telegram_api(method: str, payload: dict) -> dict:
    token, _ = get_telegram_creds()
    if not token:
        return {}
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=35) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"Erro na chamada da API ({method}): {e}")
        return {}

def send_message(text: str, reply_markup=None):
    if len(text) > 4000:
        text = text[:3950] + "\n\n<i>...(truncado)</i>"
    _, chat_id = get_telegram_creds()
    if not chat_id:
        return {}
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    res = telegram_api("sendMessage", payload)
    if not res.get("ok"):
        # Fallback sem parse_mode caso haja caractere especial HTML
        payload.pop("parse_mode", None)
        res = telegram_api("sendMessage", payload)
    return res

def edit_message_text(message_id: int, text: str, reply_markup=None):
    if len(text) > 4000:
        text = text[:3950] + "\n\n<i>...(truncado)</i>"
    _, chat_id = get_telegram_creds()
    if not chat_id:
        return {}
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML"
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    res = telegram_api("editMessageText", payload)
    if not res.get("ok"):
        payload.pop("parse_mode", None)
        res = telegram_api("editMessageText", payload)
    return res

def answer_callback_query(callback_query_id: str, text: str = "", show_alert: bool = False):
    payload = {
        "callback_query_id": callback_query_id,
        "text": text,
        "show_alert": show_alert
    }
    return telegram_api("answerCallbackQuery", payload)

def setup_bot_commands():
    commands = [
        {"command": "containers", "description": "📦 Containers com consumo de CPU, RAM e Disco"},
        {"command": "escolher", "description": "🛠️ Gerenciar/parar/reiniciar containers"},
        {"command": "status", "description": "📊 Resumo de containers e métricas"},
        {"command": "cmd", "description": "💻 Executar comando bash direto (ex: /cmd df -h)"},
        {"command": "limpar", "description": "🧹 Limpar memória do chat com a IA"},
        {"command": "help", "description": "ℹ️ Comandos e ajuda do Agente IA"}
    ]
    telegram_api("setMyCommands", {"commands": commands})

def get_docker_stats():
    stats = {}
    try:
        cmd = [
            "docker", "stats", "--no-stream",
            "--format", "{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}|{{.MemPerc}}|{{.BlockIO}}|{{.NetIO}}"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if res.returncode == 0:
            for line in res.stdout.strip().split("\n"):
                if not line.strip():
                    continue
                parts = line.split("|")
                if len(parts) >= 6:
                    stats[parts[0].strip()] = {
                        "cpu": parts[1].strip(),
                        "mem_usage": parts[2].strip(),
                        "mem_perc": parts[3].strip(),
                        "disk_io": parts[4].strip(),
                        "net_io": parts[5].strip()
                    }
    except Exception as e:
        print(f"Erro ao obter stats do Docker: {e}")
    return stats

def get_single_container_stats(container_name: str):
    try:
        cmd = [
            "docker", "stats", "--no-stream", container_name,
            "--format", "{{.CPUPerc}}|{{.MemUsage}}|{{.MemPerc}}|{{.BlockIO}}|{{.NetIO}}"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
        if res.returncode == 0 and res.stdout.strip():
            parts = res.stdout.strip().split("\n")[0].split("|")
            if len(parts) >= 5:
                return {
                    "cpu": parts[0].strip(),
                    "mem_usage": parts[1].strip(),
                    "mem_perc": parts[2].strip(),
                    "disk_io": parts[3].strip(),
                    "net_io": parts[4].strip()
                }
    except Exception as e:
        print(f"Erro ao obter stats do container {container_name}: {e}")
    return None

def get_choose_menu():
    try:
        result = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}|{{.Status}}"],
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            err = result.stderr.strip() or "Erro ao listar containers"
            return f"❌ <b>Erro:</b>\n<code>{html.escape(err)}</code>", None

        lines = [l.strip() for l in result.stdout.strip().split("\n") if l.strip()]
        if not lines:
            return f"📦 Nenhum container encontrado no servidor <code>{SERVER_NAME}</code>.", None

        text = (
            f"🛠️ <b>Gerenciador de Containers</b> — <code>{SERVER_NAME}</code>\n\n"
            f"Toque em um container para ver detalhes, métricas e opções:"
        )

        keyboard = []
        row = []
        for line in lines:
            parts = line.split("|")
            name = parts[0]
            status = parts[1] if len(parts) > 1 else ""
            icon = "🟢" if status.startswith("Up") else "🔴"

            btn = {"text": f"{icon} {name}", "callback_data": f"select:{name}"}
            if len(name) > 15:
                if row:
                    keyboard.append(row)
                    row = []
                keyboard.append([btn])
            else:
                row.append(btn)
                if len(row) == 2:
                    keyboard.append(row)
                    row = []
        if row:
            keyboard.append(row)

        keyboard.append([{"text": "🔄 Atualizar Lista", "callback_data": "back:menu"}])
        return text, {"inline_keyboard": keyboard}
    except Exception as e:
        return f"❌ <b>Erro:</b> {html.escape(str(e))}", None

def get_container_action_menu(container_name: str):
    try:
        result = subprocess.run(
            ["docker", "ps", "-a", "--filter", f"name=^/{container_name}$", "--format", "{{.Names}}|{{.Status}}|{{.Image}}"],
            capture_output=True,
            text=True
        )
        safe_name = html.escape(container_name)
        lines = [l.strip() for l in result.stdout.strip().split("\n") if l.strip()]
        if not lines:
            return f"❌ Container <code>{safe_name}</code> não foi encontrado.", None

        parts = lines[0].split("|")
        status_str = parts[1] if len(parts) > 1 else "Desconhecido"
        image_str = parts[2] if len(parts) > 2 else ""

        is_running = status_str.startswith("Up")
        status_icon = "🟢" if is_running else "🔴"

        if is_running:
            st = get_single_container_stats(container_name)
            if st:
                stats_info = (
                    f"📊 <b>Consumo de Recursos:</b>\n"
                    f"• 💻 <b>CPU:</b> <code>{st['cpu']}</code>\n"
                    f"• 🧠 <b>Memória:</b> <code>{st['mem_usage']}</code> (<code>{st['mem_perc']}</code>)\n"
                    f"• 💾 <b>Disco (I/O):</b> <code>{st['disk_io']}</code>\n"
                    f"• 🌐 <b>Rede (I/O):</b> <code>{st['net_io']}</code>\n\n"
                )
            else:
                stats_info = "📊 <b>Consumo de Recursos:</b>\n• <i>Calculando métricas...</i>\n\n"
        else:
            stats_info = "📊 <b>Consumo de Recursos:</b>\n• <i>Container parado (sem consumo ativo)</i>\n\n"

        text = (
            f"⚙️ <b>Gerenciar Container:</b> <code>{safe_name}</code>\n"
            f"<b>Servidor:</b> <code>{SERVER_NAME}</code>\n\n"
            f"<b>Status:</b> {status_icon} <i>{html.escape(status_str)}</i>\n"
            f"<b>Imagem:</b> <code>{html.escape(image_str)}</code>\n\n"
            f"{stats_info}"
            f"Escolha a ação desejada:"
        )

        buttons = []
        if is_running:
            buttons.append([
                {"text": "🔄 Reiniciar", "callback_data": f"act:restart:{container_name}"},
                {"text": "⏹️ Parar", "callback_data": f"act:stop:{container_name}"}
            ])
        else:
            buttons.append([
                {"text": "▶️ Iniciar", "callback_data": f"act:start:{container_name}"}
            ])

        buttons.append([
            {"text": "📋 Ver Logs", "callback_data": f"act:logs:{container_name}"},
            {"text": "🔄 Atualizar Status", "callback_data": f"select:{container_name}"}
        ])

        buttons.append([
            {"text": "🔙 Voltar à lista de containers", "callback_data": "back:menu"}
        ])

        return text, {"inline_keyboard": buttons}
    except Exception as e:
        return f"❌ <b>Erro:</b> {html.escape(str(e))}", None

def get_containers_list():
    try:
        result = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}|{{.Status}}|{{.Image}}"],
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            err = result.stderr.strip() or "Erro ao executar docker ps"
            return f"❌ <b>Erro ao listar containers:</b>\n<code>{html.escape(err)}</code>", None

        lines = [l.strip() for l in result.stdout.strip().split("\n") if l.strip()]
        if not lines:
            return f"📦 Nenhum container encontrado no servidor <code>{SERVER_NAME}</code>.", None

        stats_map = get_docker_stats()

        text_lines = [
            f"📦 <b>Containers & Recursos</b> — <code>{SERVER_NAME}</code>",
            f"🕒 Atualizado: <i>{datetime.now().strftime('%H:%M:%S')}</i>\n"
        ]
        keyboard = []
        total_up = 0
        total_down = 0

        for line in lines:
            parts = line.split("|")
            if len(parts) < 3:
                continue
            name, status_str, image = parts[0], parts[1], parts[2]
            safe_name = html.escape(name)
            safe_image = html.escape(image)
            safe_status = html.escape(status_str)

            if status_str.startswith("Up"):
                icon = "🟢"
                total_up += 1
                st = stats_map.get(name)
                if st:
                    entry = (
                        f"{icon} <b>{safe_name}</b>\n"
                        f"   ├ 💻 <b>CPU:</b> <code>{st['cpu']}</code> | 🧠 <b>RAM:</b> <code>{st['mem_usage']}</code> (<code>{st['mem_perc']}</code>)\n"
                        f"   ├ 💾 <b>Disco (I/O):</b> <code>{st['disk_io']}</code>\n"
                        f"   └ <i>{safe_status}</i> (<code>{safe_image}</code>)"
                    )
                else:
                    entry = (
                        f"{icon} <b>{safe_name}</b>\n"
                        f"   └ <i>{safe_status}</i> (<code>{safe_image}</code>)"
                    )
                text_lines.append(entry)

            elif status_str.startswith("Exited"):
                icon = "🔴"
                total_down += 1
                entry = f"{icon} <b>{safe_name}</b>\n   └ <i>{safe_status}</i> (<code>{safe_image}</code>)"
                text_lines.append(entry)
                keyboard.append([{"text": f"▶️ Iniciar {name}", "callback_data": f"act:start:{name}"}])
            else:
                icon = "🟡"
                total_down += 1
                entry = f"{icon} <b>{safe_name}</b>\n   └ <i>{safe_status}</i> (<code>{safe_image}</code>)"
                text_lines.append(entry)

        summary = f"\n📊 <b>Total:</b> {len(lines)} | 🟢 Ativos: {total_up} | 🔴 Parados: {total_down}"
        text_lines.append(summary)

        keyboard.append([
            {"text": "🛠️ Gerenciar Containers", "callback_data": "back:menu"},
            {"text": "🔄 Atualizar", "callback_data": "refresh_list"}
        ])

        return "\n".join(text_lines), {"inline_keyboard": keyboard}
    except Exception as e:
        return f"❌ <b>Erro:</b> {html.escape(str(e))}", None

def ensure_grass_chromium():
    """Garante que o Chromium esteja rodando no DISPLAY=:0 dentro do container grass-node."""
    def _runner():
        time.sleep(6)
        try:
            st = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Running}}", "grass-node"],
                capture_output=True, text=True, timeout=8
            )
            if st.returncode != 0 or st.stdout.strip() != "true":
                return
            chk = subprocess.run(
                ["docker", "exec", "grass-node", "pgrep", "-f", "chromium"],
                capture_output=True, text=True, timeout=8
            )
            if chk.returncode != 0 or not chk.stdout.strip():
                subprocess.run(
                    ["docker", "exec", "-d", "grass-node", "bash", "-c", "DISPLAY=:0 chromium --no-sandbox --disable-dev-shm-usage"],
                    timeout=10
                )
                send_message("🌱 <b>Grass Node:</b> Comando <code>DISPLAY=:0 chromium --no-sandbox --disable-dev-shm-usage</code> executado automaticamente!")
        except Exception as e:
            print(f"Erro em ensure_grass_chromium: {e}")
    threading.Thread(target=_runner, daemon=True).start()

def handle_docker_events():
    print("Iniciando monitoramento de eventos Docker...")
    while True:
        token, chat_id = get_telegram_creds()
        if not token or not chat_id:
            time.sleep(10)
            continue
        try:
            cmd = [
                "docker", "events",
                "--filter", "type=container",
                "--filter", "event=start",
                "--filter", "event=die",
                "--filter", "event=restart",
                "--format", "{{.Action}}|{{.Actor.Attributes.name}}|{{.Actor.Attributes.image}}|{{.Actor.Attributes.exitCode}}"
            ]
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1
            )

            for line in process.stdout:
                line = line.strip()
                if not line:
                    continue

                parts = line.split("|")
                if len(parts) < 4:
                    continue

                action, name, image, exit_code = parts[0], parts[1], parts[2], parts[3]
                if not name:
                    continue

                now_str = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
                safe_name = html.escape(name)
                safe_image = html.escape(image)

                if action in ("start", "restart") and name == "grass-node":
                    ensure_grass_chromium()

                if action == "restart":
                    msg = (
                        f"🔄 <b>Container Reiniciado!</b>\n\n"
                        f"<b>Container:</b> <code>{safe_name}</code>\n"
                        f"<b>Imagem:</b> <code>{safe_image}</code>\n"
                        f"<b>Servidor:</b> <code>{SERVER_NAME}</code>\n"
                        f"<b>Horário:</b> {now_str}"
                    )
                    send_message(msg)

                elif action == "die":
                    if exit_code == "0":
                        status = "⏹️ Parado normalmente (Exit Code 0)"
                    else:
                        status = f"💥 Queda / Erro (Exit Code {exit_code})"

                    msg = (
                        f"⚠️ <b>Alerta: Container Parou!</b>\n\n"
                        f"<b>Container:</b> <code>{safe_name}</code>\n"
                        f"<b>Imagem:</b> <code>{safe_image}</code>\n"
                        f"<b>Status:</b> {status}\n"
                        f"<b>Servidor:</b> <code>{SERVER_NAME}</code>\n"
                        f"<b>Horário:</b> {now_str}"
                    )

                    buttons = {
                        "inline_keyboard": [
                            [
                                {"text": f"🔄 Reiniciar {name}", "callback_data": f"act:restart:{name}"},
                                {"text": "📋 Ver Logs", "callback_data": f"act:logs:{name}"}
                            ]
                        ]
                    }
                    send_message(msg, reply_markup=buttons)

            process.wait()
        except Exception as e:
            print(f"Erro no monitor de eventos Docker: {e}")
        time.sleep(3)

def handle_telegram_updates():
    print("Iniciando escuta de comandos do Telegram...")
    offset = 0

    while True:
        token, chat_id = get_telegram_creds()
        if not token or not chat_id:
            time.sleep(5)
            continue
        try:
            url = f"https://api.telegram.org/bot{token}/getUpdates?offset={offset}&timeout=25"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            for update in data.get("result", []):
                offset = update["update_id"] + 1

                if "callback_query" in update:
                    cb = update["callback_query"]
                    cb_id = cb["id"]
                    user_id = cb.get("from", {}).get("id")
                    cb_data = cb.get("data", "")
                    message_id = cb.get("message", {}).get("message_id")

                    if user_id != chat_id:
                        answer_callback_query(cb_id, text="Acesso não autorizado.", show_alert=True)
                        continue

                    if cb_data.startswith("select:"):
                        container_name = cb_data.split(":", 1)[1]
                        answer_callback_query(cb_id, text="Carregando métricas...")
                        text, kb = get_container_action_menu(container_name)
                        if message_id:
                            edit_message_text(message_id, text, kb)

                    elif cb_data == "back:menu":
                        answer_callback_query(cb_id)
                        text, kb = get_choose_menu()
                        if message_id:
                            edit_message_text(message_id, text, kb)

                    elif cb_data.startswith("act:restart:") or cb_data.startswith("restart:"):
                        container_name = cb_data.split(":", 2)[-1]
                        answer_callback_query(cb_id, text=f"Reiniciando {container_name}...")
                        result = subprocess.run(["docker", "restart", container_name], capture_output=True, text=True)
                        if result.returncode == 0:
                            send_message(f"✅ Container <code>{html.escape(container_name)}</code> foi reiniciado com sucesso via Telegram!")
                        else:
                            err_msg = result.stderr.strip() or "Erro desconhecido"
                            send_message(f"❌ Falha ao reiniciar <code>{html.escape(container_name)}</code>:\n<code>{html.escape(err_msg)}</code>")
                        if message_id:
                            text, kb = get_container_action_menu(container_name)
                            if kb:
                                edit_message_text(message_id, text, kb)

                    elif cb_data.startswith("act:stop:"):
                        container_name = cb_data.split(":", 2)[-1]
                        answer_callback_query(cb_id, text=f"Parando {container_name}...")
                        result = subprocess.run(["docker", "stop", container_name], capture_output=True, text=True)
                        if result.returncode == 0:
                            send_message(f"⏹️ Container <code>{html.escape(container_name)}</code> parado com sucesso!")
                        else:
                            err_msg = result.stderr.strip() or "Erro desconhecido"
                            send_message(f"❌ Falha ao parar <code>{html.escape(container_name)}</code>:\n<code>{html.escape(err_msg)}</code>")
                        if message_id:
                            text, kb = get_container_action_menu(container_name)
                            if kb:
                                edit_message_text(message_id, text, kb)

                    elif cb_data.startswith("act:start:"):
                        container_name = cb_data.split(":", 2)[-1]
                        answer_callback_query(cb_id, text=f"Iniciando {container_name}...")
                        result = subprocess.run(["docker", "start", container_name], capture_output=True, text=True)
                        if result.returncode == 0:
                            send_message(f"▶️ Container <code>{html.escape(container_name)}</code> iniciado com sucesso!")
                        else:
                            err_msg = result.stderr.strip() or "Erro desconhecido"
                            send_message(f"❌ Falha ao iniciar <code>{html.escape(container_name)}</code>:\n<code>{html.escape(err_msg)}</code>")
                        if message_id:
                            text, kb = get_container_action_menu(container_name)
                            if kb:
                                edit_message_text(message_id, text, kb)

                    elif cb_data.startswith("act:logs:") or cb_data.startswith("logs:"):
                        container_name = cb_data.split(":", 2)[-1]
                        answer_callback_query(cb_id, text=f"Buscando logs de {container_name}...")
                        result = subprocess.run(["docker", "logs", "--tail", "15", container_name], capture_output=True, text=True)
                        output = (result.stdout + "\n" + result.stderr).strip() or "(Nenhum log recente encontrado)"
                        if len(output) > 3000:
                            output = output[-3000:]
                        send_message(
                            f"📋 <b>Últimos logs de</b> <code>{html.escape(container_name)}</code>:\n\n"
                            f"<pre>{html.escape(output)}</pre>"
                        )

                    elif cb_data == "refresh_list":
                        answer_callback_query(cb_id, text="Atualizando consumo de recursos...")
                        msg_text, reply_markup = get_containers_list()
                        if message_id:
                            edit_message_text(message_id, msg_text, reply_markup)

                elif "message" in update:
                    msg = update["message"]
                    user_id = msg.get("from", {}).get("id")
                    text = msg.get("text", "").strip()

                    if user_id != chat_id or not text:
                        continue

                    cmd = text.lower().split()[0].split("@")[0]

                    if cmd in ["/escolher", "/gerenciar"]:
                        menu_text, keyboard = get_choose_menu()
                        send_message(menu_text, reply_markup=keyboard)

                    elif cmd in ["/containers", "/status", "/ps", "/top"]:
                        list_text, keyboard = get_containers_list()
                        send_message(list_text, reply_markup=keyboard)

                    elif cmd in ["/limpar", "/clear"]:
                        with CHAT_LOCK:
                            CHAT_HISTORY["telegram"] = []
                        send_message("🧹 <b>Memória da conversa com o Agente IA foi limpa!</b>")

                    elif cmd == "/apikey":
                        parts = text.split(maxsplit=1)
                        if len(parts) < 2:
                            send_message("ℹ️ Use: <code>/apikey SUA_CHAVE_GEMINI</code>")
                        else:
                            save_config({"gemini_api_key": parts[1].strip()})
                            send_message("✅ <b>Chave da API do Gemini atualizada com sucesso!</b>")

                    elif cmd == "/cmd":
                        parts = text.split(maxsplit=1)
                        if len(parts) < 2:
                            send_message("ℹ️ Use: <code>/cmd comando bash</code>\nExemplo: <code>/cmd df -h</code>")
                        else:
                            raw_cmd = parts[1]
                            res = execute_agent_tool("run_bash", {"command": raw_cmd})
                            out = (res.get("stdout", "") + "\n" + res.get("stderr", "")).strip() or "(Sem saída)"
                            if len(out) > 3200:
                                out = out[-3200:]
                            send_message(
                                f"💻 <b>Comando:</b> <code>{html.escape(raw_cmd)}</code>\n"
                                f"<b>Exit Code:</b> <code>{res.get('exit_code')}</code>\n\n"
                                f"<pre>{html.escape(out)}</pre>"
                            )

                    elif cmd in ["/start", "/help", "/ajuda"]:
                        help_msg = (
                            f"🤖 <b>Agente IA & Docker Bot — {SERVER_NAME}</b>\n\n"
                            f"✨ <b>Modo Agente IA (Google Gemini):</b>\n"
                            f"Basta digitar qualquer pedido em linguagem natural (ex: <i>\"qual o uso de disco e RAM?\"</i>, <i>\"reinicie o nginx\"</i>, <i>\"veja os logs do n8n\"</i>) que eu executo os comandos no servidor e te respondo!\n\n"
                            f"<b>Comandos rápidos:</b>\n"
                            f"• <code>/containers</code> — Lista containers com CPU, RAM e Disco\n"
                            f"• <code>/escolher</code> — Painel interativo de containers\n"
                            f"• <code>/cmd &lt;comando&gt;</code> — Executa comando bash direto\n"
                            f"• <code>/limpar</code> — Limpa o histórico da conversa com a IA\n"
                            f"• <code>/help</code> — Mostra este menu de ajuda"
                        )
                        send_message(help_msg)

                    else:
                        # Mensagem livre -> envia para o Agente IA Gemini!
                        wait_res = send_message("🤖 <i>Analisando e executando no servidor...</i>")
                        wait_msg_id = wait_res.get("result", {}).get("message_id")

                        agent_out = run_gemini_agent("telegram", text)
                        steps = agent_out.get("steps", [])
                        reply_text = agent_out.get("reply", "")

                        steps_summary = ""
                        if steps:
                            cmds = []
                            for st in steps:
                                if st["tool"] == "run_bash":
                                    cmds.append(f"<code>$ {html.escape(st['args'].get('command', ''))}</code>")
                                else:
                                    cmds.append(f"<code>{html.escape(st['tool'])}({html.escape(str(st['args'].get('path', '')))})</code>")
                            steps_summary = "⚙️ <b>Comandos executados:</b>\n" + "\n".join(f"• {c}" for c in cmds) + "\n\n"

                        final_msg = f"{steps_summary}🤖 <b>Resposta:</b>\n{markdown_to_telegram_html(reply_text)}"
                        if wait_msg_id:
                            edit_message_text(wait_msg_id, final_msg)
                        else:
                            send_message(final_msg)

        except Exception as e:
            time.sleep(2)



def main():
    token, chat_id = get_telegram_creds()
    print(f"[{SERVER_NAME}] Iniciando serviço dedicado Telegram Bot...")
    if not token or not chat_id:
        print(f"[{SERVER_NAME}] Aguardando configuração de TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID em config.json...")

    setup_bot_commands()
    threading.Thread(target=handle_telegram_updates, daemon=True).start()
    threading.Thread(target=handle_docker_events, daemon=True).start()
    ensure_grass_chromium()

    if token and chat_id:
        send_message(
            f"""🚀 <b>Agente IA Gemini & Monitor Telegram Dedicado Ativado!</b>\n"""
            f"""Servidor: <code>{SERVER_NAME}</code>\n\n"""
            f"""🤖 O serviço de monitoramento agora roda de forma independente 24/7!"""
        )

    while True:
        time.sleep(1)

if __name__ == "__main__":
    main()
