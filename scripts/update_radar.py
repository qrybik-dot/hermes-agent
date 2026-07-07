#!/usr/bin/env python3
"""Lightweight update observer for the Hermes VPS.

No component is ever updated by this program. A model is called only when a
new release, security package set, model-catalog change, or health regression
is first observed. Identical findings are suppressed by a persisted fingerprint.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml
from packaging.version import InvalidVersion, Version

HERMES_HOME = Path(os.environ.get("HERMES_HOME", "/home/hermes/.hermes"))
REPO = Path("/home/hermes/hermes-v018-integration")
PYTHON = REPO / ".venv/bin/python"
STATE_FILE = HERMES_HOME / "update-radar/state.json"
REPORT_DIR = HERMES_HOME / "update-radar/reports"
USER_AGENT = "Hermes-Update-Radar/3.0"
ANALYSIS_MODEL = os.environ.get("UPDATE_RADAR_MODEL", "claude-sonnet-4-6")


@dataclass
class Observation:
    key: str
    name: str
    kind: str
    current: Any = None
    latest: Any = None
    status: str = "ok"
    source: str = ""
    published_at: str = ""
    release_notes: str = ""
    details: Any = None
    error: str = ""


@dataclass
class Finding:
    key: str
    name: str
    finding_type: str
    current: Any
    latest: Any
    status: str
    verdict: str
    reason: str
    source: str
    published_at: str = ""
    release_notes: str = ""
    details: Any = None


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run(command: list[str], timeout: int = 30) -> tuple[int, str]:
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
        return proc.returncode, ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 124, f"{type(exc).__name__}: {exc}"


def http_json(url: str, timeout: int = 20) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def numeric_version(text: Any) -> str:
    value = str(text or "").strip()
    # Debian/apt versions may include an epoch, e.g. 1:150.0.7871.46-1.
    had_epoch = bool(re.match(r"^\d+:", value))
    if had_epoch:
        value = value.split(":", 1)[1]
        # Debian package revisions (e.g. -1) do not change the upstream
        # application version reported by the executable.
        value = re.sub(r"-\d+$", "", value)
    match = re.search(r"\d+(?:\.\d+){0,4}(?:[-+._][0-9A-Za-z.-]+)?", value)
    return match.group(0) if match else value.lstrip("vV")


def is_newer(latest: Any, current: Any) -> bool:
    latest_v, current_v = numeric_version(latest), numeric_version(current)
    if not latest_v or not current_v:
        return False
    try:
        return Version(latest_v) > Version(current_v)
    except InvalidVersion:
        return latest_v != current_v


def github_release(repo: str) -> dict[str, str]:
    data = http_json(f"https://api.github.com/repos/{repo}/releases/latest")
    return {
        "version": str(data.get("tag_name") or "").lstrip("vV"),
        "source": str(data.get("html_url") or f"https://github.com/{repo}/releases"),
        "published_at": str(data.get("published_at") or ""),
        "release_notes": str(data.get("body") or "")[:12000],
    }


def pypi_release(package: str) -> dict[str, str]:
    data = http_json(f"https://pypi.org/pypi/{package}/json")
    info = data.get("info") or {}
    project_urls = info.get("project_urls") or {}
    source = project_urls.get("Changelog") or project_urls.get("Homepage") or info.get("project_url") or f"https://pypi.org/project/{package}/"
    return {
        "version": str(info.get("version") or ""),
        "source": str(source),
        "published_at": "",
        "release_notes": str(info.get("summary") or "")[:2000],
    }


def observe_release(
    *, key: str, name: str, current: str, latest_loader: Callable[[], dict[str, str]], kind: str = "release"
) -> Observation:
    try:
        latest = latest_loader()
        return Observation(
            key=key,
            name=name,
            kind=kind,
            current=current,
            latest=latest.get("version"),
            source=latest.get("source", ""),
            published_at=latest.get("published_at", ""),
            release_notes=latest.get("release_notes", ""),
        )
    except Exception as exc:
        return Observation(key=key, name=name, kind="health", current=current, status="unknown", error=f"{type(exc).__name__}: {exc}")


def parse_command_version(command: list[str], pattern: str, timeout: int = 20) -> str:
    code, output = run(command, timeout=timeout)
    if code not in (0, 1, 2):
        return ""
    match = re.search(pattern, output, re.I)
    return match.group(1) if match else numeric_version(output)


def current_hermes() -> str:
    # Hermes release tags use a calendar version (v2026.7.1), while the Python
    # package uses a product version (0.18.0). Compare like with like.
    code, output = run(["git", "-C", str(REPO), "describe", "--tags", "--match", "v20*", "--abbrev=0"], timeout=15)
    if code == 0 and output.strip():
        return output.strip().splitlines()[0].lstrip("vV")
    try:
        with (REPO / "pyproject.toml").open("rb") as handle:
            return str(tomllib.load(handle)["project"]["version"])
    except Exception:
        return ""


def current_uv_tool(package: str) -> str:
    code, output = run(["/home/hermes/.local/bin/uv", "tool", "list"], timeout=20)
    if code != 0:
        return ""
    match = re.search(rf"^{re.escape(package)}\s+v?([^\s]+)", output, re.M)
    return match.group(1) if match else ""


def apt_candidate(package: str) -> str:
    code, output = run(["apt-cache", "policy", package], timeout=20)
    if code != 0:
        return ""
    match = re.search(r"Candidate:\s*(\S+)", output)
    return match.group(1) if match else ""


def apt_upstream_version(package: str) -> str:
    value = apt_candidate(package)
    if re.match(r"^\d+:", value):
        value = value.split(":", 1)[1]
    return re.sub(r"-\d+$", "", value)


def active_xray_version() -> tuple[str, str]:
    code, output = run(["docker", "ps", "--format", "{{.Names}}|{{.Image}}"], timeout=15)
    if code != 0:
        return "", ""
    container = ""
    for line in output.splitlines():
        name, _, image = line.partition("|")
        if "xray" in (name + " " + image).lower():
            container = name.strip()
            break
    if not container:
        return "", ""
    code, version_output = run(["docker", "exec", container, "xray", "version"], timeout=20)
    if code != 0:
        return container, ""
    match = re.search(r"^Xray\s+([0-9.]+)", version_output, re.M)
    return container, match.group(1) if match else ""


def ubuntu_security() -> Observation:
    code, output = run(["apt-get", "-s", "upgrade"], timeout=45)
    packages: list[str] = []
    if code == 0:
        for line in output.splitlines():
            if line.startswith("Inst ") and ("security" in line.lower() or "ubuntu" in line.lower() and "-security" in line.lower()):
                parts = line.split()
                if len(parts) > 1:
                    packages.append(parts[1])
    return Observation(
        key="ubuntu_security",
        name="Ubuntu security updates",
        kind="security",
        status="ok" if code == 0 else "unknown",
        latest=sorted(set(packages)),
        source="apt security repositories",
        error="" if code == 0 else output[-500:],
    )


def docker_inventory() -> Observation:
    code, output = run(["docker", "ps", "--format", "{{.Image}}|{{.Names}}|{{.Status}}"], timeout=15)
    rows = sorted(line.strip() for line in output.splitlines() if line.strip()) if code == 0 else []
    return Observation(
        key="docker_inventory",
        name="Active Docker containers",
        kind="inventory",
        status="ok" if code == 0 else "unknown",
        current=rows,
        details={"count": len(rows)},
        error="" if code == 0 else output[-500:],
    )


def configured_mcp() -> Observation:
    try:
        config = yaml.safe_load((HERMES_HOME / "config.yaml").read_text()) or {}
        servers = sorted((config.get("mcp_servers") or {}).keys())
        return Observation(key="mcp_inventory", name="Configured MCP servers", kind="inventory", current=servers, details={"count": len(servers)})
    except Exception as exc:
        return Observation(key="mcp_inventory", name="Configured MCP servers", kind="health", status="unknown", error=str(exc))


def _resolve_local_proxy_key() -> tuple[str, str]:
    try:
        from hermes_cli.env_loader import load_hermes_dotenv
        load_hermes_dotenv(hermes_home=HERMES_HOME)
    except Exception:
        pass
    config = yaml.safe_load((HERMES_HOME / "config.yaml").read_text()) or {}
    model = config.get("model") or {}
    base = str(model.get("base_url") or "http://127.0.0.1:8317/v1").rstrip("/")
    raw = str(model.get("api_key") or "")
    match = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", raw)
    key = os.environ.get(match.group(1), "") if match else raw
    return base, key


def active_routing_models() -> list[str]:
    """Return models that are currently selected or configured as fallbacks."""
    try:
        config = yaml.safe_load((HERMES_HOME / "config.yaml").read_text()) or {}
    except Exception:
        return []
    models: set[str] = set()

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            model = value.get("model")
            if isinstance(model, str) and model.strip() and model != "no_llm":
                models.add(model.strip())
            for nested in value.values():
                collect(nested)
        elif isinstance(value, list):
            for nested in value:
                collect(nested)

    for key in ("model_roles", "role_fallbacks", "codex_quality_fallback", "image_gen"):
        collect(config.get(key))
    return sorted(models)


def antigravity_model_catalog() -> Observation:
    """Read the live Gemini/Claude catalog exposed by CLIProxyAPI."""
    try:
        base, key = _resolve_local_proxy_key()
        if not key:
            raise RuntimeError("local proxy key unresolved")
        request = urllib.request.Request(base + "/models", headers={"Authorization": "Bearer " + key})
        with urllib.request.urlopen(request, timeout=15) as response:
            data = json.load(response)
        models = sorted(str(item.get("id")) for item in data.get("data", []) if item.get("id"))
        selected = [m for m in models if any(token in m.lower() for token in ("gemini", "claude"))]
        return Observation(
            key="antigravity_model_catalog",
            name="Antigravity model catalog",
            kind="inventory",
            current=selected,
            source="CLIProxyAPI live /models catalog",
            details={"catalog_count": len(models), "configured_models": active_routing_models()},
        )
    except Exception as exc:
        return Observation(key="antigravity_model_catalog", name="Antigravity model catalog", kind="health", status="error", error=f"{type(exc).__name__}: {exc}")


def codex_model_catalog() -> Observation:
    """Read the live model catalog available to the ChatGPT/Codex subscription."""
    try:
        sys.path.insert(0, str(REPO))
        from hermes_cli.models import provider_model_ids

        models = sorted(provider_model_ids("openai-codex", force_refresh=True))
        return Observation(
            key="codex_model_catalog",
            name="Codex subscription model catalog",
            kind="inventory",
            current=models,
            source="ChatGPT Codex live model catalog",
            details={"catalog_count": len(models), "configured_models": active_routing_models()},
        )
    except Exception as exc:
        return Observation(key="codex_model_catalog", name="Codex subscription model catalog", kind="health", status="error", error=f"{type(exc).__name__}: {exc}")


def model_catalog() -> Observation:
    """Backward-compatible alias for the former combined catalog probe."""
    return antigravity_model_catalog()


def suggested_model_roles(model: str) -> list[str]:
    """Produce conservative qualification targets from a model slug."""
    value = model.lower()
    roles: list[str] = []
    if any(token in value for token in ("image", "vision")):
        roles.append("image_generation")
    if any(token in value for token in ("codex", "gpt-5", "agent")):
        roles.extend(["coding", "server_debug"])
    if any(token in value for token in ("opus", "thinking", "reasoning")):
        roles.extend(["critical_review", "expert_analysis"])
    if any(token in value for token in ("sonnet", "pro", "gpt-5")):
        roles.extend(["planning", "expert_analysis", "long_context"])
    if any(token in value for token in ("flash", "mini", "lite", "extra-low")):
        roles.extend(["simple", "parser", "fast_tools"])
    deduped: list[str] = []
    for role in roles or ["manual_qualification"]:
        if role not in deduped:
            deduped.append(role)
    return deduped


def model_catalog_change(item: Observation, old: Any) -> Finding:
    before = sorted(str(value) for value in (old or []))
    after = sorted(str(value) for value in (item.current or []))
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    configured = set((item.details or {}).get("configured_models") or [])
    active_removed = sorted(set(removed) & configured)
    suggestions = {model: suggested_model_roles(model) for model in added}
    if active_removed:
        verdict = "🚨 активная модель исчезла"
        reason = "configured model disappeared from the subscription catalog; existing runtime fallback must cover it until routing is reviewed"
    elif added:
        verdict = "🧪 доступна новая модель"
        reason = "subscription model lineup changed; qualify the new model before changing production routing"
    else:
        verdict = "🧪 модельный ряд изменился"
        reason = "one or more previously visible models disappeared; confirm entitlement and fallback coverage"
    return Finding(
        item.key,
        item.name,
        "model_catalog",
        before,
        after,
        item.status,
        verdict,
        reason,
        item.source,
        details={
            "added": added,
            "removed": removed,
            "active_removed": active_removed,
            "role_suggestions": suggestions,
            "automatic_routing_change": False,
            "recommended_gate": ["exact_response", "file_tool", "latency", "fallback", "manual_approval"],
        },
    )


def notebooklm_auth() -> Observation:
    profiles = sorted((Path("/home/hermes/.notebooklm-mcp-cli/profiles")).glob("*"))
    profile = profiles[0].name if profiles else ""
    if not profile:
        return Observation(key="notebooklm_auth", name="NotebookLM authentication", kind="health", status="error", error="No profile found")
    code, output = run(["/home/hermes/.local/bin/nlm", "login", "--check", "--profile", profile], timeout=60)
    return Observation(
        key="notebooklm_auth",
        name="NotebookLM authentication",
        kind="health",
        current=profile,
        status="ok" if code == 0 else "error",
        details="valid" if code == 0 else "expired_or_invalid",
        error="" if code == 0 else re.sub(r"\x1b\[[0-9;]*m", "", output)[-800:],
    )


def collect_observations() -> list[Observation]:
    items: list[Observation] = [ubuntu_security()]
    items.append(observe_release(key="hermes", name="Hermes Agent", current=current_hermes(), latest_loader=lambda: github_release("NousResearch/hermes-agent")))
    clip_current = parse_command_version(["/home/hermes/.local/bin/cli-proxy-api"], r"Version:\s*([0-9.]+)", timeout=8)
    items.append(observe_release(key="cliproxy", name="CLIProxyAPI", current=clip_current, latest_loader=lambda: github_release("router-for-me/CLIProxyAPI")))
    cf_current = parse_command_version(["/usr/bin/cloudflared", "--version"], r"version\s+([0-9.]+)")
    items.append(observe_release(key="cloudflared", name="cloudflared", current=cf_current, latest_loader=lambda: github_release("cloudflare/cloudflared")))
    uv_current = parse_command_version(["/home/hermes/.local/bin/uv", "--version"], r"uv\s+([0-9.]+)")
    items.append(observe_release(key="uv", name="uv", current=uv_current, latest_loader=lambda: github_release("astral-sh/uv")))
    items.append(observe_release(key="notebooklm", name="NotebookLM MCP/CLI", current=current_uv_tool("notebooklm-mcp-cli"), latest_loader=lambda: pypi_release("notebooklm-mcp-cli")))
    graph_current = parse_command_version(["/home/hermes/.hermes/tools/graphify-0.9.5/venv/bin/graphify", "--version"], r"graphify\s+([0-9.]+)")
    items.append(observe_release(key="graphify", name="Graphify", current=graph_current, latest_loader=lambda: pypi_release("graphifyy")))
    syncthing_current = parse_command_version(["/usr/bin/syncthing", "--version"], r"syncthing\s+v?([0-9.]+)")
    items.append(observe_release(key="syncthing", name="Syncthing", current=syncthing_current, latest_loader=lambda: github_release("syncthing/syncthing")))
    restic_binary = shutil.which("restic") or "/usr/local/bin/restic"
    restic_current = parse_command_version([restic_binary, "version"], r"restic\s+([0-9.]+)")
    items.append(observe_release(key="restic", name="Restic", current=restic_current, latest_loader=lambda: github_release("restic/restic")))
    tailscale_current = parse_command_version(["/usr/bin/tailscale", "version"], r"^([0-9.]+)", timeout=15)
    items.append(observe_release(key="tailscale", name="Tailscale", current=tailscale_current, latest_loader=lambda: github_release("tailscale/tailscale")))
    chrome_current = parse_command_version(["/usr/bin/google-chrome", "--version"], r"([0-9]+(?:\.[0-9]+){3})")
    chrome_latest = apt_upstream_version("google-chrome-stable")
    items.append(Observation(key="chrome", name="Google Chrome", kind="release", current=chrome_current, latest=chrome_latest, source="Google apt repository"))
    xray_container, xray_current = active_xray_version()
    if xray_container:
        xray = observe_release(key="xray", name="Xray-core", current=xray_current, latest_loader=lambda: github_release("XTLS/Xray-core"))
        xray.details = {"container": xray_container}
        items.append(xray)
    items.extend([notebooklm_auth(), antigravity_model_catalog(), codex_model_catalog(), configured_mcp(), docker_inventory()])
    return items


def release_verdict(observation: Observation) -> tuple[str, str]:
    notes = (observation.release_notes or "").lower()
    if any(phrase in notes for phrase in ("cve-", "security fix", "security advisory", "vulnerability")):
        return "🚨 срочно", "official notes explicitly mention a security fix or vulnerability"
    if observation.key in {"hermes", "graphify"}:
        return "🧪 сначала пилот", "core behavior or knowledge-graph output requires regression testing"
    if any(word in notes for word in ("breaking", "migration", "deprecated", "removed", "incompatible")):
        return "🧪 сначала пилот", "official notes mention compatibility or migration risk"
    return "✅ можно обновлять", "newer official release detected; controlled update and smoke test required"


def evaluate(observations: list[Observation], previous: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    previous_observed = previous.get("observed") or {}
    for item in observations:
        if item.kind == "release" and is_newer(item.latest, item.current):
            verdict, reason = release_verdict(item)
            findings.append(Finding(item.key, item.name, "release", item.current, item.latest, item.status, verdict, reason, item.source, item.published_at, item.release_notes, item.details))
        elif item.kind == "security" and item.latest:
            findings.append(Finding(item.key, item.name, "security", None, item.latest, item.status, "🚨 срочно", f"{len(item.latest)} security package(s) pending", item.source, details=item.details))
        elif item.kind == "health" and item.status not in {"ok", "unknown"}:
            previous_status = (previous_observed.get(item.key) or {}).get("status")
            # Baseline known failures silently; notify only on a new regression
            # or when a previously different health state changes to error.
            if previous_status is not None and previous_status != item.status:
                findings.append(Finding(item.key, item.name, "health", item.current, item.details, item.status, "🧪 требуется действие", item.error or "health check failed", item.source, details=item.details))
        elif item.kind == "inventory":
            old = (previous_observed.get(item.key) or {}).get("current")
            if old is not None and old != item.current:
                if item.key in {"antigravity_model_catalog", "codex_model_catalog"}:
                    findings.append(model_catalog_change(item, old))
                else:
                    findings.append(Finding(item.key, item.name, "inventory", old, item.current, item.status, "🧪 проверить", "runtime inventory changed", item.source, details=item.details))
    return findings


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def finding_fingerprint(finding: Finding) -> str:
    payload = {k: v for k, v in asdict(finding).items() if k not in {"release_notes"}}
    return hashlib.sha256(canonical(payload).encode()).hexdigest()


def load_state(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"schema": 3, "observed": {}, "notified": {}}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    os.replace(temp, path)


def deterministic_report(findings: list[Finding]) -> str:
    lines = ["🛰 Update Radar", ""]
    for finding in findings:
        if finding.finding_type == "model_catalog":
            details = finding.details or {}
            lines.extend([f"{finding.verdict} {finding.name}"])
            if details.get("added"):
                lines.append("Добавлены: " + ", ".join(details["added"]))
            if details.get("removed"):
                lines.append("Исчезли: " + ", ".join(details["removed"]))
            if details.get("active_removed"):
                lines.append("Затронуты активные маршруты: " + ", ".join(details["active_removed"]))
            for model, roles in (details.get("role_suggestions") or {}).items():
                lines.append(f"Предложение для {model}: сначала проверить роли {', '.join(roles)}")
            lines.append("Production-маршрутизация автоматически не менялась. После smoke-теста Hermes предложит точечную замену.")
            if finding.source:
                lines.append(f"Источник: {finding.source}")
            lines.append("")
            continue
        lines.extend([
            f"{finding.verdict} {finding.name}",
            f"Сейчас: {finding.current if finding.current not in (None, '') else '—'}",
            f"Найдено: {finding.latest if finding.latest not in (None, '') else finding.status}",
            f"Почему: {finding.reason}",
        ])
        if finding.source:
            lines.append(f"Источник: {finding.source}")
        lines.append("")
    lines.append("Автообновление не выполнялось.")
    return "\n".join(lines).strip()


def agent_review(findings: list[Finding]) -> str:
    payload = [asdict(item) for item in findings]
    for item in payload:
        item["release_notes"] = str(item.get("release_notes") or "")[:5000]
    prompt = (
        "Ты reviewer Update Radar для production Hermes VPS. JSON ниже уже собран напрямую из официальных "
        "GitHub Release API, PyPI API или локальных read-only проверок. Не используй внешние инструменты и не "
        "утверждай, что открывал страницы. Ничего не обновляй. Анализируй только явно переданные факты. "
        "Не приписывай компоненту зависимости или влияние на Memory, Calendar, Graphify и другие сервисы, если "
        "этого нет в JSON. Верни компактный русский Telegram-отчёт. Для каждой находки: один вердикт "
        "🚨 срочно / ✅ можно обновлять / 🧪 сначала пилот / ⏸ оставить; подтверждённые breaking changes; "
        "совместимость с Ubuntu 24.04 и 1 GB RAM только когда она следует из данных; подтверждённые регрессии; "
        "точный безопасный следующий шаг. Если данных недостаточно, прямо напиши 'не подтверждено'. "
        "В конце обязательно напиши: Автообновление не выполнялось.\n\nJSON:\n" + json.dumps(payload, ensure_ascii=False)
    )
    code, output = run([
        str(PYTHON), "-m", "hermes_cli.main", "-z", prompt,
        "-m", ANALYSIS_MODEL, "--provider", "custom", "-t", "no_mcp",
    ], timeout=720)
    clean = output.strip()
    low = clean.lower()
    invalid_markers = ("firecrawl", "веб-инструмент", "web-инструмент", "не удалось открыть", "не могу открыть")
    if (
        code == 0
        and clean
        and clean not in {"(empty)", "[SILENT]"}
        and "Автообновление не выполнялось." in clean
        and not any(marker in low for marker in invalid_markers)
    ):
        return clean[:3800]
    return ""


def observations_from_fixture(path: Path) -> list[Observation]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Observation(**item) for item in data.get("observations", data)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, default=STATE_FILE)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--baseline", action="store_true", help="Save current observations without notification")
    parser.add_argument("--dry-run", action="store_true", help="Do not persist state")
    parser.add_argument("--no-agent", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--force", action="store_true", help="Ignore notified fingerprints")
    parser.add_argument(
        "--telegram-card",
        action="store_true",
        help="Print a compact Telegram card with interactive action marker",
    )
    args = parser.parse_args()

    state = load_state(args.state)
    observations = observations_from_fixture(args.fixture) if args.fixture else collect_observations()
    findings = evaluate(observations, state)
    observation_map = {item.key: asdict(item) for item in observations}

    new_findings: list[Finding] = []
    for finding in findings:
        fingerprint = finding_fingerprint(finding)
        if args.force or (state.get("notified") or {}).get(finding.key) != fingerprint:
            new_findings.append(finding)

    if args.baseline:
        new_findings = []
    elif new_findings:
        from hermes_cli.update_radar_actions import filter_snoozed

        new_findings = filter_snoozed(new_findings)

    report = ""
    if new_findings:
        report = "" if args.no_agent else agent_review(new_findings)
        if not report:
            report = deterministic_report(new_findings)

    delivery_report = report
    action_ref = ""
    if args.telegram_card and new_findings:
        from hermes_cli.update_radar_actions import create_action, marker_for, render_home

        action = create_action(new_findings)
        action_ref = action["token"]
        delivery_report = render_home(action) + "\n\n" + marker_for(action)

    next_state = {
        "schema": 3,
        "last_run_at": utc_now(),
        "observed": observation_map,
        "notified": dict(state.get("notified") or {}),
    }
    if not args.baseline:
        for finding in new_findings:
            next_state["notified"][finding.key] = finding_fingerprint(finding)

    if not args.dry_run:
        save_state(args.state, next_state)
        if report:
            REPORT_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            (REPORT_DIR / f"update-radar-{stamp}.txt").write_text(report + "\n")

    if args.json:
        print(json.dumps({
            "observations": observation_map,
            "findings": [asdict(item) for item in findings],
            "new_findings": [asdict(item) for item in new_findings],
            "report": report,
            "delivery_report": delivery_report,
            "action_ref": action_ref,
        }, ensure_ascii=False, indent=2))
    elif delivery_report:
        print(delivery_report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
