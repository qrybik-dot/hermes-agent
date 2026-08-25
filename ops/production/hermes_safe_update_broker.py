#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
from typing import Iterable

REPO = Path('/home/hermes/.hermes/hermes-agent')
RUNTIME_ROOT = Path('/home/hermes/hermes-runtime')
CHANGE_ROOT = Path('/home/hermes/.hermes/ops/change-control')
PREFLIGHT = CHANGE_ROOT / 'shared-context/preflight.py'
CONTROL = Path('/opt/hermes-change-control/control_plane.py')
DEPLOY = Path('/opt/hermes-change-control/managed_deploy.py')
RUNTIME_MANIFEST = Path('/home/hermes/.hermes/ops/HERMES_RUNTIME_MANIFEST.json')
VENV_PYTHON = RUNTIME_ROOT / 'shared/venv/bin/python'
OWNER = 'update-broker'
TAG_RE = re.compile(r'^v\d{4}\.\d+(?:\.\d+)*$')
EXIT_OK = 0
EXIT_CANCELLED = 10
EXIT_BLOCKED = 20
EXIT_TIMEOUT = 124

class BrokerError(RuntimeError):
    pass

class CommandError(BrokerError):
    def __init__(self, argv: list[str], returncode: int, output: str):
        self.argv = argv
        self.returncode = returncode
        self.output = output
        super().__init__(f"command failed ({returncode}): {' '.join(argv)}")

def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)

def stamp() -> str:
    return utc_now().strftime('%Y%m%dT%H%M%SZ')

def atomic_write(path: Path, text: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)

def run(argv: Iterable[str | Path], *, cwd: Path | None = None,
        env: dict[str, str] | None = None, timeout: int = 120,
        check: bool = True) -> subprocess.CompletedProcess[str]:
    args = [str(x) for x in argv]
    merged_env = dict(os.environ)
    if env:
        merged_env.update(env)
    proc = subprocess.Popen(
        args,
        cwd=str(cwd) if cwd else None,
        env=merged_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    try:
        output, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=5)
        except Exception:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except Exception:
                pass
        raise BrokerError(f"timeout after {timeout}s: {' '.join(args)}")
    result = subprocess.CompletedProcess(args, proc.returncode, output or '', '')
    if check and result.returncode != 0:
        raise CommandError(args, result.returncode, result.stdout[-5000:])
    return result

def git(*args: str, cwd: Path = REPO, timeout: int = 120, check: bool = True) -> str:
    return run(['git', '-C', cwd, *args], timeout=timeout, check=check).stdout.strip()

def current_release() -> str:
    current = RUNTIME_ROOT / 'current'
    if not current.is_symlink():
        raise BrokerError('runtime current link is missing')
    value = current.resolve(strict=True).name
    if not re.fullmatch(r'[0-9a-f]{40}', value):
        raise BrokerError('runtime current release is invalid')
    return value

def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise BrokerError(f'invalid JSON object: {path}')
    return value

def json_command(argv: list[str | Path], timeout: int = 60) -> dict:
    value = json.loads(run(argv, timeout=timeout).stdout)
    if not isinstance(value, dict):
        raise BrokerError('command did not return a JSON object')
    return value

def control(action: str, *args: str, timeout: int = 30) -> dict:
    return json_command([sys.executable, CONTROL, action, *args], timeout)

def heartbeat(task: str) -> None:
    control('lease-heartbeat', '--owner', OWNER, '--task', task)

def release_lease(task: str) -> bool:
    try:
        if control('transition-status').get('state') != 'stable':
            return False
        control('lease-release', '--owner', OWNER, '--task', task)
        return True
    except Exception:
        return False

def preflight() -> None:
    result = run([sys.executable, PREFLIGHT, '--check', '--actor', OWNER], timeout=30)
    if 'READY' not in result.stdout:
        raise BrokerError('shared preflight did not return READY')
    if control('transition-status').get('state') != 'stable':
        raise BrokerError('release transition is not stable')
    lease = control('lease-status')
    if lease.get('active'):
        held = lease.get('lease') or {}
        raise BrokerError(f"writer lease already active: {held.get('owner')}/{held.get('task')}")

def acquire_lease(task: str, base: str) -> None:
    args = [
        '--owner', OWNER, '--task', task,
        '--runtime-release', base, '--base-commit', base,
        '--ttl-seconds', '3600', '--publication-authority', 'none',
    ]
    for resource in (
        'filesystem:/home/hermes/.hermes/hermes-agent',
        'filesystem:/home/hermes/.hermes/ops/HERMES_RUNTIME_MANIFEST.json',
        'filesystem:/home/hermes/.hermes/ops/change-control',
        'filesystem:/home/hermes/hermes-runtime/current',
        'filesystem:/home/hermes/hermes-runtime/previous',
        'filesystem:/home/hermes/hermes-runtime/releases',
        'hermes-runtime',
        'systemd:hermes-gateway.service',
    ):
        args += ['--resource', resource]
    for operation in ('backup', 'fetch', 'worktree', 'test', 'managed-deploy',
                      'rollback', 'smoke', 'verify'):
        args += ['--operation', operation]
    control('lease-acquire', *args)

def latest_stable() -> tuple[str, str]:
    remote = git('remote', 'get-url', 'upstream').rstrip('/')
    if remote != 'https://github.com/NousResearch/hermes-agent.git':
        raise BrokerError(f'unexpected upstream remote: {remote}')
    git('fetch', '--tags', '--prune', 'upstream', timeout=240)
    for tag in git('tag', '--list', 'v2026.*', '--sort=-v:refname').splitlines()[:40]:
        tag = tag.strip()
        if not TAG_RE.fullmatch(tag):
            continue
        commit = git('rev-parse', f'{tag}^{{commit}}')
        if run(['git', '-C', REPO, 'merge-base', '--is-ancestor', commit,
                'upstream/main'], check=False).returncode == 0:
            return tag, commit
    raise BrokerError('no trusted stable upstream tag found')

def wait_for_confirmation(home: Path, task: str, prompt: str,
                          seconds: int = 300) -> bool:
    prompt_path = home / '.update_prompt.json'
    response_path = home / '.update_response'
    response_path.unlink(missing_ok=True)
    atomic_write(prompt_path, json.dumps({'prompt': prompt, 'default': 'n'}, ensure_ascii=False))
    deadline = time.monotonic() + seconds
    last_hb = 0.0
    try:
        while time.monotonic() < deadline:
            if response_path.exists():
                value = response_path.read_text(encoding='utf-8', errors='replace').strip().lower()
                return value in {'y', 'yes', 'да', 'д', '1', 'true'}
            if time.monotonic() - last_hb >= 30:
                heartbeat(task)
                last_hb = time.monotonic()
            time.sleep(1)
        raise TimeoutError('update confirmation timed out')
    finally:
        prompt_path.unlink(missing_ok=True)
        response_path.unlink(missing_ok=True)

def isolated_env(root: Path, candidate: Path) -> dict[str, str]:
    home = root / 'home'
    hhome = root / 'hermes-home'
    tmp = root / 'tmp'
    for path in (home, hhome, tmp):
        path.mkdir(parents=True, exist_ok=True)
    return {
        'HOME': str(home), 'HERMES_HOME': str(hhome), 'TMPDIR': str(tmp),
        'PYTHONPATH': str(candidate), 'TERM': 'dumb',
    }

def test_candidate(candidate: Path, log) -> None:
    with tempfile.TemporaryDirectory(prefix='hermes-update-test-') as temp:
        env = isolated_env(Path(temp), candidate)
        run([VENV_PYTHON, '-m', 'compileall', '-q', 'cron', 'tools', 'gateway',
             'plugins', 'hermes_cli'], cwd=candidate, env=env, timeout=180)
        log('🧪 55% Python compile PASS')
        heartbeat(log.task)
        tests = [
            'tests/cron/test_scheduler.py',
            'tests/tools/test_cronjob_tools.py',
            'tests/cron/test_jobs_changed_notify.py',
            'tests/gateway/test_update_command.py',
            'tests/gateway/test_update_streaming.py',
            'tests/gateway/test_telegram_totp_fastpath.py',
            'tests/gateway/test_mosreg_totp_bridge_status.py',
            'tests/plugins/test_mosreg_plugin.py',
        ]
        result = run([VENV_PYTHON, '-m', 'pytest', '-q', *tests], cwd=candidate,
                     env=env, timeout=720)
        tail = '\n'.join(result.stdout.strip().splitlines()[-8:])
        if tail:
            log(tail)
        log('🧪 70% Regression suite PASS')
        heartbeat(log.task)
        version = run([VENV_PYTHON, '-m', 'hermes_cli.main', '--version'],
                      cwd=candidate, env=env, timeout=60).stdout.strip().splitlines()[0]
        log(f'🔎 75% Candidate CLI: {version}')

def deploy_candidate(candidate_sha: str, task: str, log) -> None:
    plan = json_command([sys.executable, DEPLOY, 'plan', '--owner', OWNER,
                         '--task', task, '--revision', candidate_sha], 60)
    if plan.get('from_release') != current_release() or plan.get('to_release') != candidate_sha:
        raise BrokerError('managed deploy plan does not match runtime/candidate')
    log(f"📦 80% Deploy plan: {plan.get('from_release','')[:10]} → {candidate_sha[:10]}")
    heartbeat(task)
    result = json_command([sys.executable, DEPLOY, 'deploy', '--owner', OWNER,
                           '--task', task, '--revision', candidate_sha], 600)
    if result.get('state') != 'completed' or result.get('observed_release') != candidate_sha:
        raise BrokerError('managed deploy did not complete on requested release')
    log('🚀 90% managed_deploy completed')

def smoke(candidate_sha: str, log) -> None:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        active = run(['systemctl', 'is-active', '--quiet', 'hermes-gateway.service'],
                     check=False, timeout=10).returncode == 0
        pid = run(['systemctl', 'show', 'hermes-gateway.service', '-p', 'MainPID',
                   '--value'], timeout=10).stdout.strip() if active else '0'
        if active and pid.isdigit() and int(pid) > 0:
            break
        time.sleep(2)
    else:
        raise BrokerError('gateway did not become active after deploy')
    status = json_command([sys.executable, DEPLOY, 'status'], 30)
    runtime = status.get('runtime') or {}
    transition = status.get('transition') or {}
    if Path(str(runtime.get('current', ''))).name != candidate_sha:
        raise BrokerError('runtime current does not match candidate')
    if transition.get('state') != 'stable' or transition.get('active_release') != candidate_sha:
        raise BrokerError('transition is not stable on candidate')
    manifest_current = (((read_json(RUNTIME_MANIFEST).get('runtime') or {}).get('current') or {}).get('name'))
    if manifest_current != candidate_sha:
        raise BrokerError('runtime manifest does not match candidate')
    models = run(['curl', '-fsS', '--max-time', '10',
                  'http://127.0.0.1:8317/v1/models'], timeout=15).stdout
    if not models.strip():
        raise BrokerError('CLI proxy model endpoint smoke failed')
    log('✅ 98% Runtime, transition, manifest, gateway and model endpoint PASS')

def rollback_if_possible(task: str, base: str, log) -> bool:
    try:
        if control('transition-status').get('state') != 'stable':
            return False
        live = current_release()
        if live == base:
            return True
        log(f'↩️ Smoke failed on {live[:10]}; canonical rollback requested')
        result = json_command([sys.executable, DEPLOY, 'rollback', '--owner', OWNER,
                               '--task', task], 600)
        return result.get('state') == 'completed' and current_release() == base
    except Exception as exc:
        log(f'⚠️ Rollback could not be confirmed: {exc}')
        return False

def write_handoff(task: str, verdict: str, base: str, current: str,
                  details: str = '') -> None:
    payload = {
        'schema': 1, 'task': task, 'owner': OWNER, 'verdict': verdict,
        'base_release': base, 'current_release': current,
        'publication_authority': 'none', 'completed_at': utc_now().isoformat(),
        'details': details[:2000],
    }
    atomic_write(CHANGE_ROOT / f'handoff-{task}.json',
                 json.dumps(payload, ensure_ascii=False, indent=2) + '\n')

class Logger:
    def __init__(self, output: Path, task: str):
        self.output = output
        self.task = task
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text('', encoding='utf-8')
    def __call__(self, text: str) -> None:
        with self.output.open('a', encoding='utf-8') as handle:
            handle.write(text.rstrip() + '\n')
            handle.flush()
            os.fsync(handle.fileno())

def finish(home: Path, code: int) -> None:
    atomic_write(home / '.update_exit_code', str(code))

def main() -> int:
    parser = argparse.ArgumentParser(description='Hermes fail-closed safe update broker')
    parser.add_argument('--hermes-home', required=True)
    args = parser.parse_args()
    home = Path(args.hermes_home).expanduser().resolve()
    task = f'telegram-update-{stamp().lower()}'
    log = Logger(home / '.update_output.txt', task)
    base = ''
    lease_acquired = False
    candidate_dir: Path | None = None
    try:
        log('🧭 5% Safe update broker started')
        preflight()
        base = current_release()
        acquire_lease(task, base)
        lease_acquired = True
        log(f'🔒 12% Preflight READY; lease acquired; production {base[:10]}')
        heartbeat(task)
        tag, stable = latest_stable()
        log(f'🌐 22% Official stable: {tag} ({stable[:10]})')
        if run(['git', '-C', REPO, 'merge-base', '--is-ancestor', stable, base],
               check=False).returncode == 0:
            log(f'✅ 100% Already current: production already contains {tag}')
            git('update-ref', 'refs/hermes/runtime-current', base)
            write_handoff(task, 'READY', base, base, f'already contains {tag}')
            release_lease(task)
            lease_acquired = False
            finish(home, EXIT_OK)
            return EXIT_OK
        prompt = (f'Доступно безопасное обновление Hermes до {tag}. '
                  f'Текущий production {base[:10]}. Продолжить?')
        log('👤 30% Waiting for your confirmation…')
        try:
            approved = wait_for_confirmation(home, task, prompt)
        except TimeoutError:
            log('⏱️ Confirmation timed out; production unchanged')
            write_handoff(task, 'BLOCKED', base, base, 'confirmation timeout')
            release_lease(task)
            lease_acquired = False
            finish(home, EXIT_TIMEOUT)
            return EXIT_TIMEOUT
        if not approved:
            log('⏹ Update cancelled; production unchanged')
            write_handoff(task, 'READY', base, base, 'cancelled by user')
            release_lease(task)
            lease_acquired = False
            finish(home, EXIT_CANCELLED)
            return EXIT_CANCELLED
        heartbeat(task)
        candidate_dir = CHANGE_ROOT / 'worktrees' / task
        candidate_dir.parent.mkdir(parents=True, exist_ok=True)
        run(['git', '-C', REPO, 'worktree', 'add', '--detach', candidate_dir, base],
            timeout=180)
        log('🧱 40% Clean candidate created from current production')
        merge = run(['git', '-C', candidate_dir, 'merge', '--no-commit', '--no-ff',
                     stable], timeout=300, check=False)
        if merge.returncode != 0:
            conflicts = git('diff', '--name-only', '--diff-filter=U',
                            cwd=candidate_dir, check=False)
            run(['git', '-C', candidate_dir, 'merge', '--abort'], timeout=60,
                check=False)
            raise BrokerError('merge conflicts; no deploy. Files: ' +
                              (conflicts.replace('\n', ', ') or 'unknown'))
        log('🧩 48% Upstream merge is conflict-free')
        test_candidate(candidate_dir, log)
        run(['git', '-C', candidate_dir,
             '-c', 'user.name=Hermes Update Broker',
             '-c', 'user.email=hermes-update@localhost',
             'commit', '--no-edit'], timeout=120)
        candidate_sha = git('rev-parse', 'HEAD', cwd=candidate_dir)
        git('update-ref', f'refs/hermes/update-candidates/{task}', candidate_sha)
        log(f'📌 78% Candidate anchored: {candidate_sha[:10]}')
        deploy_candidate(candidate_sha, task, log)
        try:
            smoke(candidate_sha, log)
        except Exception:
            if not rollback_if_possible(task, base, log):
                raise
            raise BrokerError('post-deploy smoke failed; rollback completed')
        git('update-ref', 'refs/hermes/runtime-current', candidate_sha)
        write_handoff(task, 'READY', base, candidate_sha,
                      f'updated to {tag}; smoke PASS')
        if not release_lease(task):
            raise BrokerError('production healthy but lease release not confirmed')
        lease_acquired = False
        log(f'✅ 100% READY — Hermes {tag}; production {candidate_sha[:10]}')
        finish(home, EXIT_OK)
        return EXIT_OK
    except Exception as exc:
        detail = str(exc)
        if isinstance(exc, CommandError) and exc.output:
            detail = f'{detail}\n{exc.output[-2500:]}'
        log(f'⛔ BLOCKED safely: {detail}')
        try:
            live = current_release() if base else 'unknown'
            transition = control('transition-status')
            if lease_acquired and transition.get('state') == 'stable' and live == base:
                write_handoff(task, 'BLOCKED', base, live, detail)
                release_lease(task)
                lease_acquired = False
            elif lease_acquired:
                log('⚠️ Lease retained because production/transition needs operator recovery')
        except Exception as cleanup_exc:
            log(f'⚠️ Cleanup status check failed: {cleanup_exc}')
        finish(home, EXIT_BLOCKED)
        return EXIT_BLOCKED
    finally:
        if candidate_dir is not None and candidate_dir.exists():
            run(['git', '-C', REPO, 'worktree', 'remove', '--force', candidate_dir],
                timeout=120, check=False)
            run(['git', '-C', REPO, 'worktree', 'prune'], timeout=60, check=False)

if __name__ == '__main__':
    raise SystemExit(main())
