#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import requests
from openai import OpenAI

ENV_FILE = Path('/home/hermes/.hermes/.env')
OUT_DIR = Path('/home/hermes/media_downloader/transcripts')
SUPADATA_BASE = 'https://api.supadata.ai/v1'
CLI_PROXY_BASE = 'http://127.0.0.1:8317/v1'


def _load_env_file() -> None:
    if not ENV_FILE.exists():
        return
    try:
        for raw in ENV_FILE.read_text(encoding='utf-8').splitlines():
            line = raw.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception:
        pass


def _safe_id(url: str) -> str:
    patterns = [
        r'youtu\.be/([A-Za-z0-9_-]{6,})',
        r'[?&]v=([A-Za-z0-9_-]{6,})',
        r'youtube\.com/shorts/([A-Za-z0-9_-]{6,})',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)[:40]
    return f'youtube_{abs(hash(url)) % 100000000}'


def _error_message(response: requests.Response) -> str:
    try:
        payload = response.json()
        if isinstance(payload, dict):
            value = payload.get('message') or payload.get('error') or payload.get('code')
            if value:
                return str(value)[:300]
    except Exception:
        pass
    return f'HTTP {response.status_code}'


def fetch_transcript(url: str, timeout_seconds: int = 240) -> dict[str, Any]:
    api_key = os.environ.get('SUPADATA_API_KEY') or os.environ.get('SUPADATA_KEY')
    if not api_key:
        raise RuntimeError('SUPADATA_API_KEY не настроен на VPS')

    headers = {'x-api-key': api_key}
    response = requests.get(
        f'{SUPADATA_BASE}/transcript',
        headers=headers,
        params={'url': url, 'text': 'true', 'mode': 'auto'},
        timeout=(10, 90),
    )

    if response.status_code == 200:
        data = response.json()
    elif response.status_code == 202:
        job_id = str((response.json() or {}).get('jobId') or '').strip()
        if not job_id:
            raise RuntimeError('Supadata вернул 202 без jobId')
        deadline = time.monotonic() + timeout_seconds
        data = None
        while time.monotonic() < deadline:
            time.sleep(2)
            poll = requests.get(
                f'{SUPADATA_BASE}/transcript/{job_id}',
                headers=headers,
                timeout=(10, 30),
            )
            if poll.status_code != 200:
                if poll.status_code == 404:
                    continue
                raise RuntimeError(f'Supadata job: {_error_message(poll)}')
            payload = poll.json() or {}
            status = str(payload.get('status') or '').lower()
            if status == 'completed':
                data = payload
                break
            if status == 'failed':
                raise RuntimeError(f"Supadata job failed: {payload.get('error') or 'unknown error'}")
        if data is None:
            raise RuntimeError('Supadata не завершил транскрибацию за 4 минуты')
    else:
        raise RuntimeError(f'Supadata transcript: {_error_message(response)}')

    content = data.get('content') if isinstance(data, dict) else None
    if isinstance(content, list):
        content = ' '.join(str(item.get('text') or '') for item in content if isinstance(item, dict))
    transcript = str(content or '').strip()
    if not transcript:
        raise RuntimeError('В видео не найдено речи или транскрипт пуст')
    return {
        'transcript': transcript,
        'lang': str(data.get('lang') or '') if isinstance(data, dict) else '',
        'available_langs': data.get('availableLangs') or [] if isinstance(data, dict) else [],
    }


def summarize(transcript: str) -> str:
    api_key = os.environ.get('HERMES_CLI_PROXY_API_KEY') or 'local-proxy'
    client = OpenAI(api_key=api_key, base_url=CLI_PROXY_BASE, timeout=120.0)
    prompt = (
        'Сделай полезное саммари YouTube-видео на русском языке строго по транскрипту.\n\n'
        'Формат:\n'
        'Суть: 2-4 предложения.\n'
        'Что показано и какой результат: конкретно, без общих слов.\n'
        'Чем полезно: кому и в каких задачах пригодится.\n'
        'Ключевые тезисы: 3-7 коротких пунктов.\n'
        'Ограничения или сомнительные места: только если они реально видны из транскрипта.\n\n'
        'Не придумывай факты, которых нет в тексте. Не пиши вступление о том, что ты посмотрел видео.\n\n'
        f'ТРАНСКРИПТ:\n{transcript[:120000]}'
    )
    candidates = []
    configured = os.environ.get('HERMES_YOUTUBE_SUMMARY_MODEL')
    if configured:
        candidates.append(configured)
    candidates.extend(['gemini-3.5-flash-low', 'gemini-3-flash', 'gemini-3.1-pro-low'])
    last_error = None
    for model in dict.fromkeys(candidates):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{'role': 'user', 'content': prompt}],
                temperature=0.2,
            )
            text = str(response.choices[0].message.content or '').strip()
            if text:
                return text
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f'Не удалось создать саммари через CLI proxy: {last_error}')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('url')
    parser.add_argument('--action', choices=('summary', 'text'), default='summary')
    args = parser.parse_args()

    _load_env_file()
    result = fetch_transcript(args.url)
    transcript = result['transcript']
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    transcript_path = OUT_DIR / f'{_safe_id(args.url)}_{int(time.time())}.txt'
    transcript_path.write_text(transcript, encoding='utf-8')

    payload: dict[str, Any] = {
        'status': 'success',
        'source': 'supadata',
        'lang': result.get('lang') or '',
        'transcript_path': str(transcript_path),
        'transcript_chars': len(transcript),
    }
    if args.action == 'summary':
        payload['summary'] = summarize(transcript)
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({'status': 'error', 'message': str(exc)[:500]}, ensure_ascii=False))
        raise SystemExit(1)
