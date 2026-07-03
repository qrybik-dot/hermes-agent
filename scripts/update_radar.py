#!/usr/bin/env python3
import sys
import os
import json
import subprocess
import urllib.request
import urllib.error
from datetime import datetime
from zoneinfo import ZoneInfo
from packaging import version

STATE_FILE = os.path.expanduser("~/.hermes/cron/state/update_radar_v2_state.json")

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"components": {}, "weekly_report_date": ""}

def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def check_ubuntu_security():
    try:
        res = subprocess.run(["apt-get", "-s", "upgrade"], capture_output=True, text=True, timeout=30)
        security_pkgs = []
        for line in res.stdout.splitlines():
            if "Inst" in line and "security" in line.lower():
                parts = line.split()
                if len(parts) >= 2:
                    security_pkgs.append(parts[1])
        return security_pkgs
    except Exception as e:
        return {"error": str(e)}

def get_github_latest_release(repo):
    try:
        url = f"https://api.github.com/repos/{repo}/releases/latest"
        req = urllib.request.Request(url, headers={"User-Agent": "Hermes-Update-Radar"})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode())
            return data.get("tag_name", "").lstrip("v")
    except Exception as e:
        return {"error": str(e)}

def get_pypi_latest(package):
    try:
        url = f"https://pypi.org/pypi/{package}/json"
        req = urllib.request.Request(url, headers={"User-Agent": "Hermes-Update-Radar"})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode())
            return data.get("info", {}).get("version")
    except Exception as e:
        return {"error": str(e)}

def get_local_version(command):
    try:
        res = subprocess.run(command, capture_output=True, text=True, timeout=10)
        if res.returncode == 0:
            return res.stdout.strip()
        return {"error": res.stderr.strip()}
    except Exception as e:
        return {"error": str(e)}

def call_agent_review(updates_found):
    prompt = "Проведи агентскую прожарку для следующих найденных обновлений. Тебе нужно проверить changelog на GitHub/PyPI, найти breaking changes, возможные регрессии (issues) и вынести вердикт (обновлять, подождать, протестировать в ветке). Формат ответа - короткий Markdown. Не используй вызовы инструментов, отвечай на основе знаний если инструменты недоступны. Обновления:\n"
    for u in updates_found:
        prompt += f"- {u['name']}: {u['details']}\n"
    
    try:
        res = subprocess.run(["hermes", "--agent", prompt], capture_output=True, text=True, timeout=300)
        return res.stdout.strip()
    except Exception as e:
        return f"Ошибка вызова агента: {e}"

def main():
    state = load_state()
    updates_found = []
    
    now = datetime.now(ZoneInfo("Europe/Stockholm"))
    now_str = now.strftime("%Y-%m-%d")
    is_weekly = now.weekday() == 0 # Monday

    # 1. Ubuntu Security
    sec_pkgs = check_ubuntu_security()
    if isinstance(sec_pkgs, list) and sec_pkgs:
        last_sec_pkgs = state.get("ubuntu_security_pkgs", [])
        if set(sec_pkgs) != set(last_sec_pkgs):
            updates_found.append({
                "type": "ubuntu_security",
                "name": "Ubuntu Security Updates",
                "details": f"{len(sec_pkgs)} packages pending (e.g. {', '.join(sec_pkgs[:3])})"
            })
            state["ubuntu_security_pkgs"] = sec_pkgs

    # 2. Hermes Agent
    hermes_latest = get_github_latest_release("NousResearch/hermes-agent")
    if isinstance(hermes_latest, str):
        current_hermes_res = get_local_version(["hermes", "--version"])
        if isinstance(current_hermes_res, str):
            current_hermes = current_hermes_res.split()[-1]
            try:
                if version.parse(hermes_latest) > version.parse(current_hermes):
                    if state["components"].get("hermes-agent") != hermes_latest or is_weekly:
                        updates_found.append({
                            "name": "Hermes Agent",
                            "details": f"Update available: {current_hermes} -> {hermes_latest}"
                        })
                        state["components"]["hermes-agent"] = hermes_latest
            except: pass

    # 3. cloudflared
    cloudflared_latest = get_github_latest_release("cloudflare/cloudflared")
    if isinstance(cloudflared_latest, str):
        current_cf_res = get_local_version(["cloudflared", "--version"])
        if isinstance(current_cf_res, str):
            try:
                current_cf = current_cf_res.split("version ")[1].split()[0]
                if cloudflared_latest not in current_cf:
                    if state["components"].get("cloudflared") != cloudflared_latest or is_weekly:
                        updates_found.append({
                            "name": "cloudflared",
                            "details": f"Update available: {current_cf} -> {cloudflared_latest}"
                        })
                        state["components"]["cloudflared"] = cloudflared_latest
            except: pass

    # 4. uv
    uv_latest = get_github_latest_release("astral-sh/uv")
    if isinstance(uv_latest, str):
        current_uv_res = get_local_version(["uv", "--version"])
        if isinstance(current_uv_res, str):
            try:
                current_uv = current_uv_res.split()[-1]
                if version.parse(uv_latest) > version.parse(current_uv):
                    if state["components"].get("uv") != uv_latest or is_weekly:
                        updates_found.append({
                            "name": "uv (Python)",
                            "details": f"Update available: {current_uv} -> {uv_latest}"
                        })
                        state["components"]["uv"] = uv_latest
            except: pass

    # 5. NotebookLM MCP (PyPI)
    notebooklm_latest = get_pypi_latest("notebooklm-mcp-cli")
    if isinstance(notebooklm_latest, str):
        current_nl_res = get_local_version(["notebooklm", "--version"])
        if isinstance(current_nl_res, str):
            try:
                current_nl = current_nl_res.split()[-1]
                if version.parse(notebooklm_latest) > version.parse(current_nl):
                    if state["components"].get("notebooklm-mcp-cli") != notebooklm_latest or is_weekly:
                        updates_found.append({
                            "name": "NotebookLM MCP CLI",
                            "details": f"Update available: {current_nl} -> {notebooklm_latest}"
                        })
                        state["components"]["notebooklm-mcp-cli"] = notebooklm_latest
            except: pass
            
    # 6. Python
    python_latest = get_github_latest_release("python/cpython")
    if isinstance(python_latest, str):
        current_py_res = get_local_version(["python3", "--version"])
        if isinstance(current_py_res, str):
            try:
                current_py = current_py_res.split()[1]
                # very simplified check
                if python_latest not in current_py:
                    if state["components"].get("python") != python_latest or is_weekly:
                        updates_found.append({
                            "name": "Python",
                            "details": f"Update available: {current_py} -> {python_latest}"
                        })
                        state["components"]["python"] = python_latest
            except: pass

    # 7. Docker (xray)
    xray_latest = get_github_latest_release("XTLS/Xray-core")
    if isinstance(xray_latest, str):
        if state["components"].get("xray") != xray_latest or is_weekly:
            updates_found.append({
                "name": "Xray-core (Docker)",
                "details": f"New release available on GitHub: {xray_latest}"
            })
            state["components"]["xray"] = xray_latest

    if is_weekly:
        if state.get("weekly_report_date") == now_str:
            pass
        else:
            state["weekly_report_date"] = now_str
            if not updates_found and state["components"]:
                deferred = [f"{k}: {v}" for k, v in state["components"].items()]
                updates_found.append({
                    "name": "Weekly Status Report",
                    "details": "Known deferred updates: " + ", ".join(deferred)
                })

    if not updates_found:
        print("[SILENT]")
        sys.exit(0)

    print("🛰 **Update Radar Report**\n")
    agent_output = call_agent_review(updates_found)
    print(agent_output)

    save_state(state)

if __name__ == "__main__":
    main()