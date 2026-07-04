#!/usr/bin/env python3
import os
import sys
import time
import subprocess
import json
import urllib.request
import urllib.error
import socket
import random
from datetime import datetime, timedelta

# Capture exact start time before waiting
start_time_dt = datetime.now()
# Format for journalctl (e.g. "2026-06-09 15:30:00")
start_time_str = start_time_dt.strftime("%Y-%m-%d %H:%M:%S")

# Wait for gateway to settle and initialize
time.sleep(5)

def check_gateway_active():
    try:
        subprocess.run(['systemctl', 'is-active', '--quiet', 'hermes-gateway.service'], check=True)
        return True
    except subprocess.CalledProcessError:
        return False

def check_journal_errors(since_time):
    error_patterns = [
        "telegram failed",
        "no connected platforms",
        "AttributeError",
        "UnboundLocalError",
        "Traceback",
        "ERROR gateway.platforms.telegram"
    ]
    
    try:
        result = subprocess.run(
            ['journalctl', '-u', 'hermes-gateway.service', '--since', since_time, '--no-pager', '-n', '250'],
            capture_output=True, text=True, timeout=12
        )
        
        if result.returncode != 0:
            return [f"journalctl_failed (exit {result.returncode})"]
            
        output = result.stdout + result.stderr
        found_errors = []
        for pattern in error_patterns:
            if pattern in output:
                found_errors.append(pattern)
                
        return found_errors
    except subprocess.TimeoutExpired:
        print("journal_check_timeout")
        return []
    except Exception as e:
        print(f"journal_check_error: {type(e).__name__}")
        return []

def get_telegram_config():
    token = None
    chat_id = None
    
    try:
        # Check environment first (if passed via EnvironmentFile)
        token = os.environ.get('TELEGRAM_BOT_TOKEN')
        chat_id = os.environ.get('TELEGRAM_HOME_CHANNEL') or os.environ.get('TELEGRAM_CHAT_ID')
        
        # Fallback to parsing .env file safely
        if not token or not chat_id:
            env_path = '/home/hermes/.hermes/.env'
            if os.path.exists(env_path):
                with open(env_path, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith('TELEGRAM_BOT_TOKEN='):
                            token = line.split('=', 1)[1].strip().strip('"\'')
                        elif line.startswith('TELEGRAM_CHAT_ID='):
                            chat_id = line.split('=', 1)[1].strip().strip('"\'')
        
        if token:
            print("token_present")
        else:
            print("token_missing")
            
        if chat_id:
            print("chat_id_present")
        else:
            print("chat_id_missing")
            
        return token, chat_id
    except Exception as e:
        print(f"config_extraction_failed: {e}")
    return token, chat_id

def send_telegram_message(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "disable_notification": True
    }).encode('utf-8')
    
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
    try:
        urllib.request.urlopen(req, timeout=10)
        print("telegram_send_ok")
    except urllib.error.HTTPError as e:
        print(f"telegram_send_failed: http_error (status {e.code})")
    except urllib.error.URLError as e:
        if isinstance(e.reason, socket.timeout):
            print("telegram_send_failed: timeout")
        else:
            print(f"telegram_send_failed: url_error ({e.reason})")
    except socket.timeout:
        print("telegram_send_failed: timeout")
    except Exception as e:
        print(f"telegram_send_failed: other ({type(e).__name__})")

def main():
    print("startup_notify_started")
    token, chat_id = get_telegram_config()
    if not token or not chat_id:
        print("missing_config_exit")
        sys.exit(0)
        
    is_active = check_gateway_active()
    print(f"gateway_active={is_active}")
    
    errors = check_journal_errors(start_time_str)
    print(f"journal_errors={len(errors)}")
    
    success_messages = [
        "🟢 Я снова в сети. Ребут был, паники не было.",
        "🟢 Вернулся. Systemd сделал вид, что так и было.",
        "🟢 Онлайн. Один ребут, без драм.",
        "🟢 Дышу. Логи пока не кричат.",
        "🟢 Поднялся. Можно работать, но аккуратно."
    ]
    
    if not is_active:
        send_telegram_message(token, chat_id, "🔴 Gateway down. Сервис не active.")
    elif errors:
        cause = errors[0]
        send_telegram_message(token, chat_id, f"🔴 Gateway поднялся, но обнаружена ошибка в логах: {cause}")
    else:
        send_telegram_message(token, chat_id, random.choice(success_messages))

if __name__ == "__main__":
    main()
