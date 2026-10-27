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


def load_services() -> list:
    if os.path.exists(SERVICES_FILE):
        try:
            with open(SERVICES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Erro ao carregar services.json: {e}")
    return DEFAULT_SERVICES

def save_services(services: list):
    try:
        with open(SERVICES_FILE, "w", encoding="utf-8") as f:
            json.dump(services, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Erro ao salvar services.json: {e}")
def get_enriched_services() -> list:
    services = load_services()
    try:
        res = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}|{{.Status}}|{{.Ports}}"],
            capture_output=True,
            text=True,
            timeout=5
        )
        running_ports = set()
        running_names = set()
        if res.returncode == 0:
            for line in res.stdout.strip().splitlines():
                if not line.strip():
                    continue
                parts = line.split("|")
                name = parts[0].strip().lower()
                status = parts[1].strip() if len(parts) > 1 else ""
                ports_str = parts[2].strip() if len(parts) > 2 else ""
                if status.startswith("Up"):
                    running_names.add(name)
                    if ports_str:
                        for p in re.findall(r"\d+", ports_str):
                            running_ports.add(int(p))

        aliases = {
            "npm": ["nginx-proxy-manager", "npm"],
            "mysterium": ["myst", "mysterium"],
            "authentik": ["authentik-server", "authentik-server-1", "authentik"]
        }

        enriched = []
        for s in services:
            s_copy = dict(s)
            s_id = (s.get("id") or "").lower()
            s_name = (s.get("name") or "").lower()
            port = int(s.get("port") or 0)

            is_active = (port in running_ports)
            if not is_active:
                if s_id in running_names or s_name in running_names:
                    is_active = True
                else:
                    extra = aliases.get(s_id, [])
                    if any(any(an in cn for cn in running_names) for an in extra):
                        is_active = True
                    elif any((s_id and s_id in cn) or (cn in s_id and len(cn) > 2) for cn in running_names):
                        is_active = True
            s_copy["active"] = is_active
            enriched.append(s_copy)
        return enriched
    except Exception as e:
        print(f"Erro ao enriquecer servicos: {e}")
        return services


# Ensure initial services file exists
if not os.path.exists(SERVICES_FILE):
    save_services(DEFAULT_SERVICES)

DISMISSED_FILE = os.path.join(DATA_DIR, "dismissed_services.json")

def load_dismissed() -> list:
    if os.path.exists(DISMISSED_FILE):
        try:
            with open(DISMISSED_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Erro ao carregar dismissed: {e}")
    return []

def save_dismissed(dismissed: list):
    try:
        with open(DISMISSED_FILE, "w", encoding="utf-8") as f:
            json.dump(dismissed, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Erro ao salvar dismissed: {e}")

def guess_service_info(container_name: str, image_name: str, port: int):
    c_lower = container_name.lower()
    img_lower = image_name.lower()
    presets = {
        "grafana": ("Grafana", "Monitoramento & M?tricas", "??"),
        "prometheus": ("Prometheus", "M?tricas & S?ries Temporais", "??"),
        "pihole": ("Pi-hole", "DNS & Bloqueador de An?ncios", "???"),
        "adguard": ("AdGuard Home", "DNS & Privacidade", "???"),
        "plex": ("Plex Media Server", "M?dia & Streaming", "??"),
        "jellyfin": ("Jellyfin", "M?dia & Streaming", "??"),
        "emby": ("Emby Server", "M?dia & Streaming", "??"),
        "syncthing": ("Syncthing", "Sincroniza??o de Arquivos", "??"),
        "nextcloud": ("Nextcloud", "Nuvem Privada & Arquivos", "??"),
        "owncloud": ("OwnCloud", "Nuvem Privada & Arquivos", "??"),
        "transmission": ("Transmission", "Cliente BitTorrent", "??"),
        "qbittorrent": ("qBittorrent", "Cliente BitTorrent", "??"),
        "deluge": ("Deluge", "Cliente BitTorrent", "??"),
        "vaultwarden": ("Vaultwarden", "Cofre de Senhas", "??"),
        "bitwarden": ("Bitwarden", "Cofre de Senhas", "??"),
        "homeassistant": ("Home Assistant", "Automa??o Residencial", "??"),
        "nodered": ("Node-RED", "Automa??o IoT", "??"),
        "wireguard": ("WireGuard", "VPN & Rede", "??"),
        "openvpn": ("OpenVPN", "VPN & Rede", "??"),
        "redis": ("Redis Commander", "Banco de Dados Redis", "???"),
        "mongo": ("Mongo Express", "Banco de Dados Mongo", "??"),
        "pgadmin": ("pgAdmin", "Administra??o PostgreSQL", "??"),
        "phpmyadmin": ("phpMyAdmin", "Administra??o MySQL", "??"),
        "dozzle": ("Dozzle", "Visualizador de Logs Docker", "??"),
        "glances": ("Glances", "Monitoramento de Sistema", "??"),
        "code": ("VS Code Server", "Desenvolvimento / IDE", "??"),
        "filebrowser": ("File Browser", "Gerenciador de Arquivos", "??"),
        "guacamole": ("Apache Guacamole", "Acesso Remoto", "???"),
        "portainer": ("Portainer", "Gerenciamento Docker", "??"),
        "authentik": ("Authentik", "Identidade & SSO", "??"),
        "npm": ("Nginx Proxy Manager", "Proxy Reverso", "??"),
        "uptime": ("Uptime Kuma", "Monitoramento", "??"),
    }
    for key, (display_name, category, icon) in presets.items():
        if key in c_lower or key in img_lower:
            return display_name, category, icon
    clean_name = container_name.replace("-", " ").replace("_", " ").title()
    return clean_name, "Aplica??o Web", "??"

def discover_docker_services() -> list:
    configured_services = load_services()
    dismissed = set(load_dismissed())
    configured_ports = set()
    configured_ids = set()
    for s in configured_services:
        if s.get("port"):
            try:
                configured_ports.add(int(s.get("port")))
            except (ValueError, TypeError):
                pass
        if s.get("id"):
            configured_ids.add(str(s.get("id")).lower())
        if s.get("name"):
            configured_ids.add(str(s.get("name")).lower())

    skip_ports = {22, 53, 3306, 5432, 6379, 27017, 11211, 8888, 8088}
    skip_containers = {"linux-server-admin", "nginx", "nginx-proxy-manager", "authentik-worker-1", "authentik-postgresql-1"}
    discovered = []

    try:
        res = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}|{{.Image}}|{{.Ports}}|{{.Status}}"],
            capture_output=True,
            text=True,
            timeout=8
        )
        if res.returncode != 0:
            return []
        for line in res.stdout.strip().splitlines():
            if not line.strip(): continue
            parts = line.split("|")
            if len(parts) < 3: continue
            name, image, ports_str = parts[0].strip(), parts[1].strip(), parts[2]
            if name in skip_containers: continue
            
            # Check if strongly matched by name
            name_lower = name.lower()
            matched = False
            for cid in configured_ids:
                if cid in name_lower or name_lower in cid:
                    matched = True
                    break
            if matched:
                continue

            host_ports = set()
            for m in re.finditer(r":(\d+)->", ports_str):
                host_ports.add(int(m.group(1)))
            for m in re.finditer(r":(\d+)-(\d+)->", ports_str):
                for p in range(int(m.group(1)), int(m.group(2))+1):
                    host_ports.add(p)

            for port in sorted(list(host_ports)):
                if port in skip_ports or port in configured_ports:
                    continue
                item_id = f"{name}-{port}"
                if item_id in dismissed:
                    continue
                display_name, category, icon = guess_service_info(name, image, port)
                protocol = "https" if port in (443, 8443, 9443) else "http"
                discovered.append({
                    "id": item_id,
                    "container": name,
                    "image": image,
                    "name": display_name,
                    "category": category,
                    "desc": f"Container '{name}' detectado em execu??o na porta {port}.",
                    "port": port,
                    "protocol": protocol,
                    "icon": icon,
                    "color": "rgba(56, 189, 248, 0.18)",
                    "path": ""
                })
    except Exception as e:
        print(f"Erro no auto-discover: {e}")
    return discovered

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

# In-memory chat history
CHAT_HISTORY = {
    "web": [],
    "telegram": []
}
CHAT_LOCK = threading.Lock()

# =====================================================================
# SYSTEM OVERVIEW & METRICS
# =====================================================================

def get_system_overview() -> dict:
    overview = {
        "hostname": SERVER_NAME,
        "uptime": "",
        "cpu": {"model": "", "cores": 0, "usage": ""},
        "memory": {"total": "", "used": "", "percent": ""},
        "disk": {"total": "", "used": "", "percent": ""},
        "containers": [],
        "os": get_host_os(),
        "background_containers": []
    }

    # Uptime
    try:
        res = subprocess.run(host_cmd(["uptime", "-p"]), capture_output=True, text=True, timeout=3)
        overview["uptime"] = res.stdout.strip() if res.returncode == 0 else ""
    except Exception:
        pass

    # CPU
    try:
        with open("/proc/cpuinfo", "r") as f:
            lines = f.readlines()
        cores = sum(1 for line in lines if line.startswith("processor"))
        model = ""
        for line in lines:
            if "model name" in line:
                model = line.split(":", 1)[1].strip()
                break
        overview["cpu"]["cores"] = cores
        overview["cpu"]["model"] = model
    except Exception:
        pass

    try:
        with open("/proc/loadavg", "r") as f:
            load = f.read().split()[:3]
            overview["cpu"]["usage"] = " / ".join(load)
            overview["load"] = overview["cpu"]["usage"]
    except Exception:
        pass

    # Memory
    try:
        meminfo = {}
        with open("/proc/meminfo", "r") as f:
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    meminfo[parts[0].strip()] = int(parts[1].strip().split()[0])
        total = meminfo.get("MemTotal", 0) / 1024
        avail = meminfo.get("MemAvailable", 0) / 1024
        used = total - avail
        pct = (used / total * 100) if total > 0 else 0
        overview["memory"] = {
            "total": f"{total/1024:.1f} GB" if total >= 1024 else f"{total:.0f} MB",
            "used": f"{used/1024:.1f} GB" if used >= 1024 else f"{used:.0f} MB",
            "percent": f"{pct:.0f}%"
        }
    except Exception:
        pass

    # Disk
    try:
        df_out = subprocess.check_output(host_cmd(["df", "-h", "/"]), text=True, timeout=5).splitlines()
        if len(df_out) >= 2:
            cols = df_out[1].split()
            overview["disk"] = {
                "total": cols[1],
                "used": cols[2],
                "percent": cols[4]
            }
    except Exception:
        pass

    # Docker Containers
    try:
        res = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}|{{.Status}}|{{.Image}}|{{.Ports}}"],
            capture_output=True,
            text=True,
            timeout=8
        )
        if res.returncode == 0:
            for line in res.stdout.strip().splitlines():
                if not line.strip():
                    continue
                p = line.split("|")
                if len(p) >= 4:
                    is_running = p[1].startswith("Up")
                    c_info = {
                        "name": p[0],
                        "status": p[1],
                        "image": p[2],
                        "ports": p[3],
                        "running": is_running
                    }
                    overview["containers"].append(c_info)
                    if is_running and "->" not in p[3] and p[0] != "linux-server-admin":
                        overview["background_containers"].append(c_info)
    except Exception:
        pass

    return overview

# =====================================================================
# USERS DATA (.htpasswd & Linux)
# =====================================================================

def get_users_data() -> dict:
    data = {
        "web_users": [],
        "system_users": []
    }

    # 1. Web Users (.htpasswd)
    if os.path.exists(HTPASSWD_PATH):
        try:
            with open(HTPASSWD_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and ":" in line:
                        user = line.split(":", 1)[0].strip()
                        if user:
                            data["web_users"].append(user)
        except Exception as e:
            data["web_error"] = str(e)

    # 2. System Users (Host or Container)
    try:
        passwd_content = ""
        if is_host_accessible():
            res = subprocess.run(host_cmd(["cat", "/etc/passwd"]), capture_output=True, text=True, timeout=5)
            if res.returncode == 0:
                passwd_content = res.stdout
        if not passwd_content and os.path.exists("/etc/passwd"):
            with open("/etc/passwd", "r", encoding="utf-8") as f:
                passwd_content = f.read()

        for line in passwd_content.splitlines():
            parts = line.strip().split(":")
            if len(parts) >= 7:
                uname, _, uid_s, gid_s, gecos, home, shell = parts[:7]
                try:
                    uid = int(uid_s)
                except ValueError:
                    continue
                if (uid >= 1000 and uid != 65534) or uid == 0:
                    groups = []
                    try:
                        res = subprocess.run(host_cmd(["groups", uname]), capture_output=True, text=True, timeout=3)
                        if res.returncode == 0:
                            grp_str = res.stdout.split(":", 1)[-1].strip()
                            groups = [g.strip() for g in grp_str.split() if g.strip()]
                    except Exception:
                        pass

                    data["system_users"].append({
                        "username": uname,
                        "uid": uid,
                        "home": home,
                        "shell": shell,
                        "gecos": gecos.split(",")[0] if gecos else "",
                        "groups": groups,
                        "is_sudo": "sudo" in groups or uid == 0,
                        "is_docker": "docker" in groups
                    })
    except Exception as e:
        data["system_error"] = str(e)

    return data

# =====================================================================
# AI AGENT (GEMINI FUNCTION CALLING)
# =====================================================================

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

class AdminAPIHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send_json({"ok": True})

    def do_GET(self):
        if self.path.startswith("/api/overview"):
            self._send_json(get_system_overview())

        elif self.path.startswith("/api/services/discover"):
            self._send_json({"discovered": discover_docker_services()})
        elif self.path.startswith("/api/services/discover/dismiss"):
            item_id = str(body.get("id", "")).strip()
            dismissed = load_dismissed()
            if item_id and item_id not in dismissed:
                dismissed.append(item_id)
                save_dismissed(dismissed)
            self._send_json({"ok": True, "dismissed": dismissed})
        elif self.path.startswith("/api/services"):
            self._send_json({"services": get_enriched_services()})

        elif self.path.startswith("/api/users"):
            self._send_json(get_users_data())

        elif self.path.startswith("/api/config"):
            cfg = load_config()
            key = cfg.get("gemini_api_key", "")
            masked = (key[:6] + "..." + key[-4:]) if len(key) > 12 else ("Configurada" if key else "")
            tg_token = cfg.get("telegram_bot_token", "")
            masked_tg = (tg_token[:6] + "..." + tg_token[-4:]) if len(tg_token) > 12 else ("Configurado" if tg_token else "")
            self._send_json({
                "server_name": cfg.get("server_name", SERVER_NAME),
                "hasGeminiKey": bool(key),
                "maskedGeminiKey": masked,
                "geminiModel": cfg.get("gemini_model", "gemini-3.5-flash"),
                "hasTelegram": bool(tg_token),
                "maskedTelegram": masked_tg,
                "telegramChatId": cfg.get("telegram_chat_id", "")
            })

        else:
            self._send_json({"error": "Endpoint não encontrado"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        try:
            body = json.loads(raw)
        except Exception:
            body = {}

        # 1. Chat AI Agent
        if self.path.startswith("/api/chat"):
            msg = str(body.get("message", "")).strip()
            reset = bool(body.get("reset", False))
            if reset and not msg:
                with CHAT_LOCK:
                    CHAT_HISTORY["web"] = []
                self._send_json({"reply": "Histórico limpo! Como posso ajudar?", "steps": []})
                return
            if not msg:
                self._send_json({"error": "Mensagem vazia"}, 400)
                return
            result = run_gemini_agent("web", msg, reset=reset)
            self._send_json(result)

        # 2. Terminal
        elif self.path.startswith("/api/terminal"):
            cmd = str(body.get("command", "")).strip()
            cwd = str(body.get("cwd", "/root")).strip() or "/root"
            if not cmd:
                self._send_json({"exitCode": 0, "stdout": "", "stderr": "", "cwd": cwd})
                return
            try:
                wrapped = f'cd "{cwd}" 2>/dev/null || cd /; {cmd}\necho "___CWD_MARKER___$(pwd)"'
                full_cmd = host_cmd(["bash", "-c", wrapped])
                res = subprocess.run(full_cmd, capture_output=True, text=True, timeout=60)
                stdout = res.stdout
                new_cwd = cwd
                if "___CWD_MARKER___" in stdout:
                    parts = stdout.rsplit("___CWD_MARKER___", 1)
                    stdout = parts[0]
                    new_cwd = parts[1].strip() or cwd
                self._send_json({
                    "exitCode": res.returncode,
                    "stdout": stdout,
                    "stderr": res.stderr,
                    "cwd": new_cwd
                })
            except Exception as e:
                self._send_json({"exitCode": 1, "stdout": "", "stderr": str(e), "cwd": cwd})

        # 3. Docker Containers Control
        elif self.path.startswith("/api/container"):
            name = str(body.get("name", "")).strip()
            action = str(body.get("action", "")).strip()
            if not name or action not in ("start", "stop", "restart", "logs"):
                self._send_json({"error": "Parâmetros inválidos"}, 400)
                return
            try:
                if action == "logs":
                    res = subprocess.run(["docker", "logs", "--tail", "80", name], capture_output=True, text=True, timeout=15)
                    out = (res.stdout + "\n" + res.stderr).strip() or "(Sem logs recentes)"
                    self._send_json({"ok": True, "logs": out})
                else:
                    res = subprocess.run(["docker", action, name], capture_output=True, text=True, timeout=30)
                    self._send_json({"ok": res.returncode == 0, "message": res.stdout.strip() or res.stderr.strip()})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, 500)

        # 4. Services Manager (Cards)
        elif self.path.startswith("/api/services/discover"):
            self._send_json({"discovered": discover_docker_services()})
        elif self.path.startswith("/api/services/discover/dismiss"):
            item_id = str(body.get("id", "")).strip()
            dismissed = load_dismissed()
            if item_id and item_id not in dismissed:
                dismissed.append(item_id)
                save_dismissed(dismissed)
            self._send_json({"ok": True, "dismissed": dismissed})
        elif self.path.startswith("/api/services"):
            action = str(body.get("action", "save")).strip()
            services = load_services()
            if action == "save":
                new_list = body.get("services")
                if isinstance(new_list, list):
                    save_services(new_list)
                    self._send_json({"ok": True, "services": new_list})
                else:
                    self._send_json({"ok": False, "error": "Formato inválido"}, 400)
            elif action == "add":
                svc = body.get("service")
                if isinstance(svc, dict) and svc.get("name"):
                    services.append(svc)
                    save_services(services)
                    self._send_json({"ok": True, "services": services})
                else:
                    self._send_json({"ok": False, "error": "Serviço inválido"}, 400)
            elif action == "delete":
                idx = int(body.get("index", -1))
                if 0 <= idx < len(services):
                    services.pop(idx)
                    save_services(services)
                    self._send_json({"ok": True, "services": services})
                else:
                    self._send_json({"ok": False, "error": "Índice inválido"}, 400)

        # 5. Web Users (.htpasswd)
        elif self.path.startswith("/api/users/web"):
            action = str(body.get("action", "")).strip()
            username = str(body.get("username", "")).strip()
            password = str(body.get("password", "")).strip()

            if not username or not re.match(r'^[a-zA-Z0-9_\-\.]+$', username):
                self._send_json({"ok": False, "error": "Nome de usuário inválido."}, 400)
                return

            os.makedirs(os.path.dirname(HTPASSWD_PATH), exist_ok=True)
            file_is_new = (not os.path.exists(HTPASSWD_PATH)) or (os.path.getsize(HTPASSWD_PATH) == 0)

            if action in ("add", "update", "password"):
                if not password:
                    self._send_json({"ok": False, "error": "A senha não pode ser vazia."}, 400)
                    return
                cmd = ["htpasswd", "-bc", HTPASSWD_PATH, username, password] if file_is_new else ["htpasswd", "-b", HTPASSWD_PATH, username, password]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                if res.returncode != 0 and "use '-c'" in res.stderr:
                    res = subprocess.run(["htpasswd", "-bc", HTPASSWD_PATH, username, password], capture_output=True, text=True, timeout=10)
                if res.returncode == 0:
                    subprocess.run(["nginx", "-s", "reload"], capture_output=True, text=True)
                    self._send_json({"ok": True, "message": f"Usuário web '{username}' salvo com sucesso!"})
                else:
                    self._send_json({"ok": False, "error": res.stderr.strip() or "Erro no htpasswd"}, 500)
            elif action == "delete":
                if not os.path.exists(HTPASSWD_PATH) or os.path.getsize(HTPASSWD_PATH) == 0:
                    self._send_json({"ok": True, "message": f"Usuário web '{username}' excluído!"})
                    return
                res = subprocess.run(["htpasswd", "-D", HTPASSWD_PATH, username], capture_output=True, text=True, timeout=10)
                if res.returncode == 0:
                    subprocess.run(["nginx", "-s", "reload"], capture_output=True, text=True)
                    self._send_json({"ok": True, "message": f"Usuário web '{username}' excluído!"})
                else:
                    self._send_json({"ok": False, "error": res.stderr.strip() or "Falha ao remover usuário"}, 500)
            else:
                self._send_json({"ok": False, "error": "Ação inválida."}, 400)

        # 6. Linux System Users
        elif self.path.startswith("/api/users/system"):
            action = str(body.get("action", "")).strip()
            username = str(body.get("username", "")).strip()
            password = str(body.get("password", "")).strip()
            is_sudo = bool(body.get("is_sudo", False))
            is_docker = bool(body.get("is_docker", False))

            if not username or not re.match(r'^[a-z_][a-z0-9_\-]*$', username):
                self._send_json({"ok": False, "error": "Nome de usuário Linux inválido."}, 400)
                return

            if action == "add":
                if not password:
                    self._send_json({"ok": False, "error": "Senha obrigatória."}, 400)
                    return
                add_cmd = host_cmd(["useradd", "-m", "-s", "/bin/bash", username])
                res = subprocess.run(add_cmd, capture_output=True, text=True, timeout=10)
                if res.returncode != 0:
                    self._send_json({"ok": False, "error": res.stderr.strip() or "Erro ao criar usuário."}, 500)
                    return
                pass_cmd = host_cmd(["chpasswd"])
                proc = subprocess.run(pass_cmd, input=f"{username}:{password}\n", text=True, capture_output=True, timeout=10)
                if proc.returncode != 0:
                    self._send_json({"ok": False, "error": proc.stderr.strip() or "Erro ao definir senha."}, 500)
                    return
                if is_sudo:
                    subprocess.run(host_cmd(["usermod", "-aG", "sudo", username]), timeout=5)
                if is_docker:
                    subprocess.run(host_cmd(["usermod", "-aG", "docker", username]), timeout=5)
                self._send_json({"ok": True, "message": f"Usuário Linux '{username}' criado com sucesso no host!"})

            elif action == "password":
                if not password:
                    self._send_json({"ok": False, "error": "Senha vazia."}, 400)
                    return
                pass_cmd = host_cmd(["chpasswd"])
                proc = subprocess.run(pass_cmd, input=f"{username}:{password}\n", text=True, capture_output=True, timeout=10)
                if proc.returncode == 0:
                    self._send_json({"ok": True, "message": f"Senha de '{username}' atualizada!"})
                else:
                    self._send_json({"ok": False, "error": proc.stderr.strip() or "Erro ao alterar senha."}, 500)

            elif action == "delete":
                if username == "root":
                    self._send_json({"ok": False, "error": "O usuário 'root' é protegido e não pode ser excluído."}, 400)
                    return
                del_cmd = host_cmd(["userdel", "-r", username])
                res = subprocess.run(del_cmd, capture_output=True, text=True, timeout=15)
                if res.returncode == 0:
                    self._send_json({"ok": True, "message": f"Usuário Linux '{username}' excluído com sucesso!"})
                else:
                    self._send_json({"ok": False, "error": res.stderr.strip() or "Erro ao remover usuário."}, 500)
            else:
                self._send_json({"ok": False, "error": "Ação inválida."}, 400)

        # 7. Config
        elif self.path.startswith("/api/config"):
            updates = {}
            if "gemini_api_key" in body:
                updates["gemini_api_key"] = str(body["gemini_api_key"]).strip()
            if "gemini_model" in body:
                updates["gemini_model"] = str(body["gemini_model"]).strip()
            if "server_name" in body:
                updates["server_name"] = str(body["server_name"]).strip()
            if "telegram_bot_token" in body:
                updates["telegram_bot_token"] = str(body["telegram_bot_token"]).strip()
            if "telegram_chat_id" in body:
                updates["telegram_chat_id"] = str(body["telegram_chat_id"]).strip()
            save_config(updates)
            self._send_json({"ok": True})

        else:
            self._send_json({"error": "Endpoint desconhecido"}, 404)

def run_server():
    server = ThreadingHTTPServer(("0.0.0.0", API_PORT), AdminAPIHandler)
    print(f"[{SERVER_NAME}] Linux Server Admin API ouvindo em 0.0.0.0:{API_PORT}...")
    server.serve_forever()

if __name__ == "__main__":
    run_server()
