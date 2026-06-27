"""Deterministic gateway task and tool routing.

Classification is intentionally LLM-free. It runs before AIAgent construction,
selects a configured model role, and exposes only the toolsets needed for the
current turn while respecting the platform allowlist.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

ROLE_ORDER = (
    "no_llm",
    "simple",
    "parser",
    "research",
    "planning",
    "coding",
    "long_context",
    "server_debug",
)

_NO_LLM_COMMANDS = {
    "memory_search",
    "status",
    "health",
    "provider_status",
    "models_status",
    "latest_backup",
    "usage_today",
    "tasks",
    "today",
    "week",
    "kanban",
    "add",
    "done",
    "move",
    "stuck",
    "help",
    "familytasks",
    "ftasks",
}

_SERVER_DEBUG_RE = re.compile(
    r"\b(vps|systemd|journalctl|hermes-gateway|gateway|ram|swap|disk|telemt|"
    r"cloudflared|syncthing)\b|сервис(?:а|ы|ов)?|логи?\b|диагностик|"
    r"производительност(?:ь|и)\s+(?:vps|сервера|hermes)|"
    r"(?:аудит|проверк)\w*\s+git|git[- ]?(?:синхронизац|статус|аудит)",
    re.I,
)
_CODING_ACTION_RE = re.compile(
    r"\b(?:исправь|почини|реализуй|отрефакторь|закоммить|добавь\s+тест|"
    r"внеси\s+изменения|измени\s+(?:код|файл|скрипт|модуль)|сделай\s+commit)\b|"
    r"\b(?:fix|patch|implement|refactor|commit)\b",
    re.I,
)
_CODING_OBJECT_RE = re.compile(
    r"\b(?:код|python|javascript|typescript|репозитор|файл|скрипт|модуль|pytest|"
    r"git|функци|класс|тест)\w*\b",
    re.I,
)
_RESEARCH_RE = re.compile(
    r"найди\s+(?:свеж|актуаль)|поиск\s+в\s+интернете|проверь\s+по\s+источникам|"
    r"сравни\s+источники|последние\s+новости|актуальные\s+(?:цены|правила|данные)|"
    r"\b(?:latest|web research)\b",
    re.I,
)
_PLANNING_RE = re.compile(
    r"архитектурн\w*\s+план|спроектируй|стратеги\w*|roadmap|blueprint|"
    r"декомпозируй\s+(?:проект|задачу)|план\s+внедрения|"
    r"больш\w*\s+задач\w*|разлож\w*\s+на\s+этапы|через\s+планировани|"
    r"понятн\w*\s+шаг\w*|составь\s+план\s+работ",
    re.I,
)
_PARSER_RE = re.compile(
    r"распарс|преобразуй\s+(?:json|csv|yaml|markdown)|извлеки\s+поля|"
    r"структурируй\s+(?:json|csv|таблиц)|конвертируй\s+(?:json|csv|yaml)",
    re.I,
)
_LONG_CONTEXT_RE = re.compile(
    r"проанализируй\s+(?:весь|большой|длинный)\s+(?:документ|файл|транскрипт)|"
    r"большой\s+документ|полный\s+транскрипт|длинный\s+отч[её]т",
    re.I,
)
_REPORT_RE = re.compile(
    r"(?:сделай|подготовь|обнови|покажи|пришли)\s+(?:нормальный\s+|подробный\s+)?"
    r"отч[её]т|отч[её]т\s+об\s+изменениях|объясни,?\s+что\s+(?:было\s+)?изменено|"
    r"html[- ]?отч[её]т",
    re.I,
)
_EMAIL_RE = re.compile(
    r"\b(?:gmail|email|e-mail)\b|почт(?:а|е|у|ой)|письм(?:о|а|е|у|ом|ами)|"
    r"входящ(?:ие|их)|экспорт\s+приш[её]л",
    re.I,
)
_CALENDAR_RE = re.compile(
    r"google\s+calendar|календар(?:ь|я|е|ю)|событи(?:е|я|й)\s+(?:сегодня|завтра)|"
    r"что\s+у\s+меня\s+запланировано|расписание\s+на\s+(?:сегодня|завтра)|"
    r"встречи\s+(?:сегодня|завтра|в\s+календаре)",
    re.I,
)
_GRANOLA_RE = re.compile(
    r"(?:в|из|через)\s+granola|заметк\w*\s+granola|транскрипт\w*\s+granola|"
    r"встреч\w*,?\s+(?:сохран[её]нн\w*|записанн\w*)\s+в\s+granola|"
    r"поиск\s+по\s+(?:заметкам|встречам)\s+granola",
    re.I,
)
_MEMORY_RE = re.compile(
    r"\b(?:помнишь|помни|обсуждали|делали|раньше|вчера|ранее|сохранили|"
    r"в\s+прошлый\s+раз|обо\s+мне|о\s+мо[её]м|мои?|моя|наше?|семья|карьер|"
    r"поиск\s+работы|предпочтени|решени|проект)\w*\b",
    re.I,
)
_TUTU_RE = re.compile(r"\b(?:tutu|туту|ржд|ж/д|поезд|железнодорожн\w*\s+билет)\b", re.I)
_CONTEXT7_RE = re.compile(
    r"\bcontext7\b|официальн\w*\s+документац|документац\w*\s+(?:api|sdk|библиотек)|"
    r"\b(?:next\.js|react|supabase)\b",
    re.I,
)


@dataclass(frozen=True)
class TaskRoute:
    role: str
    reason: str
    toolsets: list[str]
    max_iterations: int
    skip_context_files: bool = False
    operational_context: str = ""


def explicit_role_override(text: str) -> str | None:
    hay = text or ""
    match = re.search(r"(?:task_role|role|роль)\s*[:=]\s*([a-z_]+)", hay, re.I)
    if match and match.group(1) in ROLE_ORDER:
        return match.group(1)
    match = re.search(r"/(?:role|task_role)\s+([a-z_]+)", hay, re.I)
    if match and match.group(1) in ROLE_ORDER:
        return match.group(1)
    return None


def _intent_flags(text: str) -> dict[str, bool]:
    value = text or ""
    email = bool(_EMAIL_RE.search(value))
    calendar = bool(_CALENDAR_RE.search(value))
    # A mention of Granola inside an email request describes the sender/subject,
    # not a request to open the Granola MCP server.
    granola = bool(_GRANOLA_RE.search(value)) and not email and not calendar
    return {
        "email": email,
        "calendar": calendar,
        "granola": granola,
        "memory": bool(_MEMORY_RE.search(value)),
        "report": bool(_REPORT_RE.search(value)),
        "tutu": bool(_TUTU_RE.search(value)),
        "context7": bool(_CONTEXT7_RE.search(value)),
    }


def classify_task(text: str, *, command: str | None = None) -> tuple[str, str]:
    override = explicit_role_override(text)
    if override:
        return override, "explicit override"

    cmd = (command or "").strip().lstrip("/").lower()
    if cmd in _NO_LLM_COMMANDS:
        return "no_llm", f"deterministic command /{cmd}"

    value = text or ""
    if len(value) > 50000:
        return "long_context", "message length"
    if _SERVER_DEBUG_RE.search(value):
        return "server_debug", "server/debug intent"
    if _CODING_ACTION_RE.search(value) and _CODING_OBJECT_RE.search(value):
        return "coding", "explicit code change intent"
    if _RESEARCH_RE.search(value):
        return "research", "external research intent"
    if _PLANNING_RE.search(value):
        return "planning", "architecture/planning intent"
    if _PARSER_RE.search(value):
        return "parser", "structured parsing intent"
    if _LONG_CONTEXT_RE.search(value):
        return "long_context", "long-context intent"
    if _REPORT_RE.search(value):
        return "simple", "report/continuation intent"
    if len(value) < 1200 and not re.search(r"https?://|```|\n.{80,}\n", value):
        return "simple", "short ordinary message"
    return "simple", "default"


def _apply_platform_policy(requested: set[str], platform_toolsets: list[str] | None) -> list[str]:
    if platform_toolsets is None:
        return sorted(requested)
    allowed = set(platform_toolsets)
    # no_mcp is a routing sentinel rather than a model-facing capability.
    result = {name for name in requested if name == "no_mcp" or name in allowed}
    return sorted(result)


def select_toolsets(
    role: str,
    text: str,
    platform_toolsets: list[str] | None = None,
) -> list[str]:
    flags = _intent_flags(text)

    if role == "no_llm":
        return ["no_mcp"]
    if role == "simple":
        requested = {"no_mcp"}
    elif role == "parser":
        requested = {"file", "code_execution", "clarify", "no_mcp"}
    elif role == "research":
        requested = {"web", "file", "memory", "skills", "clarify", "no_mcp"}
    elif role == "coding":
        requested = {"terminal", "file", "code_execution", "skills", "todo", "memory", "clarify", "no_mcp"}
    elif role == "server_debug":
        requested = {"terminal", "file", "skills", "memory", "clarify", "no_mcp"}
    elif role == "planning":
        requested = {"file", "memory", "skills", "todo", "clarify", "no_mcp"}
    elif role == "long_context":
        requested = {"file", "memory", "skills", "session_search", "clarify", "no_mcp"}
    else:
        requested = {"clarify", "no_mcp"}

    if flags["memory"]:
        requested.update({"memory", "session_search"})
    if flags["report"]:
        requested.update({"file", "memory", "session_search"})
    if flags["email"] or flags["calendar"]:
        # Google Workspace is currently exposed through its skill and CLI.
        requested.update({"skills", "terminal", "file"})
    if flags["granola"]:
        requested.discard("no_mcp")
        requested.add("granola")
    if flags["tutu"]:
        requested.discard("no_mcp")
        requested.add("tutu")
    if flags["context7"] and role in {"coding", "research", "planning"}:
        requested.discard("no_mcp")
        requested.add("context7")

    return _apply_platform_policy(requested, platform_toolsets)


def max_turns_for_role(role: str, cfg: Mapping[str, Any] | None = None) -> int:
    role_cfg: Mapping[str, Any] = {}
    if isinstance(cfg, Mapping):
        raw = cfg.get("role_max_turns") or cfg.get("max_turns_by_role") or {}
        if isinstance(raw, Mapping):
            role_cfg = raw
    if role in role_cfg:
        try:
            return max(1, int(role_cfg[role]))
        except (TypeError, ValueError):
            pass
    return 24 if role in {"coding", "planning", "server_debug", "long_context"} else 12


def compact_operational_context(platform_key: str, role: str) -> str:
    if platform_key != "telegram":
        return ""

    common = (
        "Контекст Telegram Hermes. Отвечай по-русски, если пользователь явно не попросил иначе. "
        "Лучший вывод давай первым, затем краткие основания. Пиши простым человеческим языком, "
        "без длинного тире и без шаблонных AI-фраз. Не соглашайся автоматически: проверяй риски, "
        "слабые места и лучшие альтернативы. Учитывай сохранённую память пользователя, но не выдумывай "
        "факты; для личных, семейных, карьерных и проектных вопросов сначала используй точечный поиск по памяти. "
        "Live-статусом сложной задачи управляет gateway по фактическим событиям инструментов. Не печатай собственный прогресс-бар и не возвращай progress-only вместо результата. Если нужного инструмента нет, сразу верни точный BLOCKED; если работа не выполнена, верни INCOMPLETE. "
        "После технической работы дай нормальный отчёт на русском: Итог; Сделано; Проверено; Файлы; Риски; Следующий шаг, скрывая пустые разделы. Отчёт должен опираться только на фактические git diff/status, логи systemd, результаты тестов и реальные команды. Не выдумывай изменённые файлы, перезапуски, сетевые ошибки, commits или push; непроверенные факты помечай как «не проверено». Не раскрывай токены, ключи, cookies, содержимое личной почты и полные "
        "транскрипты без явного запроса. После двух одинаковых ошибок смени стратегию; после третьей остановись и "
        "сообщи точный блокер. Не удаляй файлы, логи, backups, сессии, кэши, репозитории, память, Google/Granola "
        "данные или контейнеры без явного подтверждения."
    )
    if role in {"coding", "planning", "server_debug", "long_context"}:
        common += (
            " Для изменений кода и VPS действуй backup-first. Не трогай чужой dirty diff, не используй git reset, "
            "git clean, git add . или rm -rf. Добавляй в git только точные файлы. Сначала тесты и проверка конфигурации, "
            "затем максимум один контролируемый restart. Всегда сохраняй понятный rollback."
        )
    return common


def route_turn(
    text: str,
    *,
    command: str | None,
    platform_key: str,
    user_config: Mapping[str, Any] | None,
    platform_toolsets: list[str] | None = None,
) -> TaskRoute:
    role, reason = classify_task(text, command=command)
    flags = _intent_flags(text)
    intent_names = [name for name, enabled in flags.items() if enabled]
    if intent_names:
        reason = f"{reason}; intents={','.join(intent_names)}"
    agent_cfg = (user_config or {}).get("agent") if isinstance(user_config, Mapping) else {}
    if not isinstance(agent_cfg, Mapping):
        agent_cfg = {}
    return TaskRoute(
        role=role,
        reason=reason,
        toolsets=select_toolsets(role, text, platform_toolsets),
        max_iterations=max_turns_for_role(role, agent_cfg),
        # Telegram intentionally skips the large repository AGENTS.md/SOUL.md;
        # compact personality and role safety rules are supplied above instead.
        skip_context_files=(platform_key == "telegram"),
        operational_context=compact_operational_context(platform_key, role),
    )
