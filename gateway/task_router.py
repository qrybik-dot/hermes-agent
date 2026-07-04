"""Deterministic gateway task and tool routing.

Classification is intentionally LLM-free. It runs before AIAgent construction,
selects a configured model role, and exposes only the toolsets needed for the
current turn while respecting the platform allowlist.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

ROLE_ORDER = (
    "no_llm",
    "simple",
    "parser",
    "research",
    "expert_analysis",
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
    r"\b(?:исправь|почини|реализуй|отрефакторь|закоммить|настрой|установи|"
    r"создай|расширь|подключи|внедри|доработай|обнови|добавь\s+тест|"
    r"внеси\s+изменения|измени\s+(?:код|файл|скрипт|модуль|маршрутизатор)|"
    r"сделай\s+commit)\b|"
    r"\b(?:fix|patch|implement|refactor|commit|configure|install|extend)\b",
    re.I,
)
_CODING_OBJECT_RE = re.compile(
    r"\b(?:код|python|javascript|typescript|репозитор|файл|скрипт|модуль|pytest|"
    r"git|функци|класс|тест|router|маршрутизатор|skill|навык|конфиг|gateway|"
    r"runtime|toolset|инструмент)\w*\b",
    re.I,
)
_RESEARCH_RE = re.compile(
    r"найди\s+(?:свеж|актуаль)|поиск\s+в\s+интернете|проверь\s+по\s+источникам|"
    r"сравни\s+источники|последние\s+новости|актуальные\s+(?:цены|правила|данные)|"
    r"самостоятельно\s+найди|найди\s+(?:источник|источники|материалы|статьи|видео)|"
    r"не\s+менее\s+\d+\s+источник\w*|"
    r"не\s+старше(?:\s*,?\s*чем)?\s+\d+\s+(?:дн(?:я|ей)|недел[ьиь]|месяц(?:а|ев))|"
    r"\b(?:latest|web research)\b",
    re.I,
)
_NOTEBOOKLM_RE = re.compile(r"\bnotebook\s*lm\b|ноутбук\s*лм|ноутбуклм", re.I)
_EXPERT_ANALYSIS_RE = re.compile(
    r"\b(?:экспертн\w*|критическ\w*|глубок\w*)\s+(?:анализ|оценк|прожарк|разбор)|"
    r"\b(?:прожарк|что\s+я\s+упускаю|сильн\w*\s+и\s+слаб\w*\s+сторон|"
    r"риски?\s+и\s+альтернатив|выбери\s+лучш\w*\s+(?:вариант|подход|архитектур))\b",
    re.I,
)
_LEARNING_SIGNAL_RE = re.compile(
    r"\b(?:запомни|сохрани\s+(?:правило|настройк)|в\s+дальнейшем|"
    r"не\s+пиши\s+так|не\s+делай\s+так|делай\s+(?:короче|иначе|всегда)|"
    r"раньше\s+было\s+лучше|этот\s+(?:способ|подход)\s+сработал|"
    r"учти\s+на\s+будущее|моя\s+настройк|мо[её]\s+предпочтени)\w*\b",
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
_EXPLICIT_HTML_REPORT_RE = re.compile(
    r"html[- ]?отч[её]т|отч[её]т\s+(?:в|как)\s+(?:html|файл)|"
    r"(?:пришли|отправь|подготовь|сохрани)\s+(?:мне\s+)?(?:отдельн\w*\s+)?(?:html|файл|документ)\b",
    re.I,
)
_DEEP_ANALYSIS_RE = re.compile(
    r"\b(?:глубок\w*|подробн\w*|полн\w*|комплексн\w*|системн\w*)\s+"
    r"(?:исследован|анализ|аудит|сравнен)|\b(?:исследование|аналитическ\w*\s+отч[её]т)\b|"
    r"не\s+менее\s+\d+\s+источник\w*",
    re.I,
)
_STRUCTURAL_CHANGE_RE = re.compile(
    r"\b(?:архитектур|миграц|рефактор|интеграц|структур|глобальн|многоэтапн|"
    r"нескольк\w*\s+(?:компонент|сервис|модул|файл)|production|prod)\w*",
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
_DRIVE_RE = re.compile(
    r"google\s+drive|гугл\s+диск|google\s+диск|диск\s+google|"
    r"(?:файл|папк|документ)\w*\s+(?:на|в)\s+(?:drive|google\s+drive|гугл\s+диск)",
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
_TRAVEL_RE = re.compile(
    r"\b(?:сколько|как)\s+(?:ехать|идти|добираться|доехать)\b|"
    r"\b(?:маршрут|дорог[аи]|доехать|ехать)\w*\b.{0,100}\b(?:до|из|от)\b|"
    r"\b(?:парковк|припарковаться|кафе|ресторан|поесть|покушать|"
    r"куда\s+сходить|что\s+посмотреть)\w*\b|"
    r"\b(?:парк|усадьб|музе|вднх|достопримечательност)\w*\b.{0,100}"
    r"\b(?:маршрут|парковк|кафе|рядом|около|возле|дет)\w*\b|"
    r"\b(?:яндекс\s*карт|2гис)\b.{0,100}\b(?:маршрут|парковк|кафе|мест)\w*\b",
    re.I | re.S,
)
_TRAVEL_CAFE_CURRENT_RE = re.compile(
    r"\b(?:рейтинг|рейтингами|отзыв|отзывам|открыто|закрыто|час(?:ы|ов)\s+работы|"
    r"актуальн\w*\s+(?:отзыв|час|рейтинг)|сравни\s+кафе)\w*\b",
    re.I,
)
_TRAVEL_SOURCE_CAPTURE_RE = re.compile(
    r"\b(?:сохрани|запиши|добавь)\w*\b.{0,180}"
    r"\b(?:место|локаци|точк|объект)\w*\b.{0,180}"
    r"\b(?:путешеств|поездк|посетить|travel)\w*\b|"
    r"\b(?:вытащи|извлеки|собери)\w*\b.{0,140}"
    r"\b(?:рилс|reels?|instagram|инстаграм|ссылк)\w*\b.{0,240}"
    r"\b(?:место|локаци|цен|расписан|время\s+работы|час(?:ы|ов)?\s+работы)\w*\b",
    re.I | re.S,
)
_TRAVEL_SOURCE_REFERENCE_RE = re.compile(
    r"https?://|\b(?:рилс|reels?|instagram|инстаграм|ссылк|пост)\w*\b",
    re.I,
)
_TRAVEL_PLACE_REFERENCE_RE = re.compile(
    r"https?://(?:www\.)?(?:yandex\.(?:ru|com)/maps(?:/|\?|$)|maps\.yandex\.(?:ru|com)(?:/|\?|$)|2gis\.(?:ru|com)(?:/|$)|maps\.app\.goo\.gl/|goo\.gl/maps/|google\.[a-z.]+/maps(?:/|\?|$)|maps\.google\.[a-z.]+/)",
    re.I,
)
_TRAVEL_LIVE_TRAFFIC_RE = re.compile(
    r"\b(?:пробк|traffic|live\s*traffic|актуальн\w*\s+(?:дорог|трафик|время\s+в\s+пути))\w*\b",
    re.I,
)
_TRAVEL_ROUTE_PARKING_RE = re.compile(
    r"\b(?:сколько|как)\s+(?:ехать|идти|добираться|доехать)\b|"
    r"\b(?:маршрут|дорог[аи]|доехать|ехать|парковк|припарковаться|яндекс\s*карт|2гис)\w*\b",
    re.I,
)
_CONTEXT7_RE = re.compile(
    r"\bcontext7\b|официальн\w*\s+документац|документац\w*\s+(?:api|sdk|библиотек)|"
    r"\b(?:next\.js|react|supabase)\b",
    re.I,
)
_SKILLS_QUERY_RE = re.compile(
    r"(?:какие|какой|список|подборк)\w*\s+(?:ещ[её]\s+)?(?:навык|skill)\w*|"
    r"(?:есть|установлен|подключен|доступен)\w*.*(?:навык|skill)\w*|"
    r"(?:навык|skill)\w*.*(?:есть|установлен|подключен|доступен|полезен|лучше)|"
    r"(?:найди|поищи|проверь|используй)\s+(?:этот\s+|подходящ\w*\s+)?(?:навык|скилл|skill)\w*|"
    r"hermes\s+skills|/creative-ideation|/skill\b",
    re.I,
)
_EXTERNAL_ACTION_RE = re.compile(
    r"\b(?:скачай|загрузи|отправь|перешли|сохрани|добавь|создай|"
    r"открой|проверь|найди|поищи|извлеки|конвертируй|запусти|выполни|"
    r"установи|подключи|обнови|исправь|почини|настрой)\w*\b",
    re.I,
)
_BARE_URL_RE = re.compile(r"^\s*https?://\S+\s*$", re.I)
_AMBIGUOUS_SAVE_RE = re.compile(r"^\s*(?:сохрани|запиши|добавь)\s+(?:это(?:\s+для\s+меня)?|мне|для\s+меня)(?:\s+это)?[.!?]*\s*$", re.I)
_EXTERNAL_PROVIDER_SENSITIVE_RE = re.compile(
    r"\b(?:mosreg|esia|мосрег|есиа|госуслуг|паспорт|снилс|"
    r"токен\w*|парол\w*|cookie|oauth|authorization|api[_ -]?key|секрет\w*)\b|"
    r"\.env\b|authorized_keys|(?:[\w.+-]+@[\w.-]+\.[A-Za-z]{2,})|"
    r"(?<!\d)(?:\+?7|8)[\s()-]*\d{3}[\s()-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}(?!\d)",
    re.I,
)
_EXTERNAL_PROVIDER_CONTINUATION_ONLY_RE = re.compile(
    r"^\s*(?:продолжить|продолжай|возобновить|resume)\s+[0-9a-f]{6,32}\s*[.!?]*$",
    re.I,
)
_EXTERNAL_PROVIDER_SAFE_ROLES = {
    "simple", "parser", "research", "expert_analysis", "planning", "coding", "server_debug"
}


_EXPLICIT_ADAPTIVE_PLAN_RE = re.compile(
    r"\b(?:jtbd|dod|definition\s+of\s+done)\b|"
    r"\b(?:составь|подготовь|сделай|дай|покажи)\s+(?:кратк\w*\s+)?план\b|"
    r"разлож\w*\s+на\s+этапы|критери\w*\s+готовност",
    re.I,
)
_APPROVED_PLAN_RE = re.compile(
    r"(?:утвержд[её]н\w*|согласован\w*)\s+план\w*|"
    r"план\w*\s+(?:уже\s+)?(?:утвержд[её]н\w*|согласован\w*)|"
    r"по\s+(?:уже\s+)?(?:утвержд[её]нн\w*|согласованн\w*)\s+план\w*|"
    r"без\s+повторн\w*\s+планировани",
    re.I,
)
_LARGE_TASK_RE = re.compile(
    r"\b(?:крупн\w*|больш\w*\s+задач\w*|многоэтапн\w*|неоднозначн\w*|"
    r"архитектурн\w*|рефактор\w*|миграц\w*|интеграц\w*|"
    r"нескольк\w*\s+(?:этап|систем|компонент|сервис|файл))\b|"
    r"не\s+менее\s+(?:[2-9]\d|\d{3,})\s+источник\w*",
    re.I,
)
_RISKY_TASK_RE = re.compile(
    r"\b(?:vps|systemd|sudo|ssh|gateway|rollback|backup|бэкап\w*|откат\w*|"
    r"перезапуск\w*|production|prod)\b",
    re.I,
)
_HIGH_RISK_TASK_RE = re.compile(
    r"\b(?:root|авторизац\w*|аутентификац\w*|секрет\w*|токен\w*|"
    r"удален\w*|миграц\w*)\b|\.env\b|права\s+доступа|authorized_keys",
    re.I,
)
_STATUS_ONLY_RE = re.compile(
    r"^\s*(?:покажи|проверь|дай)\s+(?:текущ\w*\s+)?(?:статус|состояние|логи?)\b",
    re.I,
)
_MULTI_STEP_RE = re.compile(
    r"\b(?:затем|после\s+этого|далее|нескольк\w*\s+шаг|этап)\b|"
    r"\bи\s+(?:добавь|проверь|обнови|создай|настрой|запусти)\b",
    re.I,
)
_ACTION_REQUEST_RE = re.compile(
    r"\b(?:исправь|почини|реализуй|настрой|установи|создай|расширь|подключи|"
    r"внедри|доработай|обнови|добавь|выполни|запусти|перезапусти|удали|"
    r"примени|разверни|составь|подготовь)\b",
    re.I,
)


@dataclass(frozen=True)
class AdaptivePlanningPolicy:
    mode: str = "none"
    preload_plan_skill: bool = False
    reviewer_required: bool = False
    reason: str = "not needed"


def adaptive_planning_policy(text: str, role: str) -> AdaptivePlanningPolicy:
    value = text or ""
    stripped = value.strip()
    imperative = bool(_ACTION_REQUEST_RE.search(value))
    if stripped.endswith("?") and len(stripped) < 320 and not imperative:
        return AdaptivePlanningPolicy(reason="short question")
    if _STATUS_ONLY_RE.search(value) and len(stripped) < 320:
        return AdaptivePlanningPolicy(reason="status-only request")
    if _APPROVED_PLAN_RE.search(value):
        return AdaptivePlanningPolicy(reason="approved plan already exists")

    explicit = bool(_EXPLICIT_ADAPTIVE_PLAN_RE.search(value))
    item_count = len(re.findall(r"(?m)^\s*(?:\d+[.)]|[-*])\s+\S", value))
    large = bool(_LARGE_TASK_RE.search(value)) or len(value) > 1600 or item_count >= 6
    high_risk = bool(_HIGH_RISK_TASK_RE.search(value))
    risky = high_risk or bool(_RISKY_TASK_RE.search(value))
    technical_execution = role in {"coding", "server_debug"} or bool(
        _CODING_ACTION_RE.search(value) and _CODING_OBJECT_RE.search(value)
    )
    nonempty_lines = [line for line in value.splitlines() if line.strip()]
    one_clear_command = bool(
        technical_execution
        and len(stripped) < 320
        and len(nonempty_lines) <= 2
        and item_count == 0
        and not explicit
        and not large
        and not high_risk
        and not _MULTI_STEP_RE.search(value)
    )
    if one_clear_command:
        return AdaptivePlanningPolicy(reason="one clear reversible command")
    if large or risky:
        reason = "large task" if large else "risky task"
        if large and risky:
            reason = "large and risky task"
        return AdaptivePlanningPolicy("reviewed", True, True, reason)
    if explicit:
        return AdaptivePlanningPolicy("plan", True, False, "explicit plan/JTBD/DoD request")
    if technical_execution or role == "planning":
        return AdaptivePlanningPolicy("brief", False, False, "medium execution task")
    return AdaptivePlanningPolicy(reason="simple task")


def should_generate_html_report(text: str, role: str, *, requires_execution: bool = False) -> bool:
    """Choose a document only when it adds value beyond the chat answer."""
    value = str(text or "")
    if _EXPLICIT_HTML_REPORT_RE.search(value):
        return True
    if _DEEP_ANALYSIS_RE.search(value):
        return True
    policy = adaptive_planning_policy(value, role)
    if role == "long_context":
        return True
    if role == "research":
        return policy.mode == "reviewed" or bool(_DEEP_ANALYSIS_RE.search(value))
    if role == "coding":
        return requires_execution or policy.mode == "reviewed" or bool(_STRUCTURAL_CHANGE_RE.search(value))
    if role == "server_debug":
        if _STATUS_ONLY_RE.search(value) and len(value.strip()) < 320:
            return False
        return requires_execution or policy.mode == "reviewed" or bool(_STRUCTURAL_CHANGE_RE.search(value))
    if role == "planning":
        return policy.mode == "reviewed" or bool(_STRUCTURAL_CHANGE_RE.search(value))
    return False


@dataclass(frozen=True)
class TaskRoute:
    role: str
    reason: str
    toolsets: list[str]
    max_iterations: int
    skill_names: tuple[str, ...] = field(default_factory=tuple)
    skip_context_files: bool = False
    operational_context: str = ""


def explicit_role_override(text: str) -> str | None:
    """Allow only an explicit slash command at the start of the current turn.

    Role-like strings inside pasted, replied or forwarded text are untrusted
    content and must never change execution permissions or model selection.
    """
    hay = text or ""
    match = re.match(r"^\s*/(?:role|task_role)\s+([a-z_]+)(?:\s|$)", hay, re.I)
    if match and match.group(1) in ROLE_ORDER:
        return match.group(1)
    return None


def _intent_flags(text: str) -> dict[str, bool]:
    value = text or ""
    email = bool(_EMAIL_RE.search(value))
    calendar = bool(_CALENDAR_RE.search(value))
    drive = bool(_DRIVE_RE.search(value))
    # A mention of Granola inside a Google Workspace request describes the sender,
    # subject, or file contents, not a request to open the Granola MCP server.
    granola = bool(_GRANOLA_RE.search(value)) and not email and not calendar and not drive
    return {
        "email": email,
        "calendar": calendar,
        "drive": drive,
        "granola": granola,
        "memory": bool(_MEMORY_RE.search(value)),
        "report": bool(_REPORT_RE.search(value)),
        "tutu": bool(_TUTU_RE.search(value)),
        "travel": bool(
            _TRAVEL_RE.search(value)
            or _TRAVEL_SOURCE_CAPTURE_RE.search(value)
            or _TRAVEL_PLACE_REFERENCE_RE.search(value)
        ),
        "context7": bool(_CONTEXT7_RE.search(value)),
        "skills_query": bool(_SKILLS_QUERY_RE.search(value)),
        "notebooklm": bool(_NOTEBOOKLM_RE.search(value)),
    }


def travel_is_source_capture(text: str) -> bool:
    value = text or ""
    return bool(_TRAVEL_SOURCE_CAPTURE_RE.search(value) and _TRAVEL_SOURCE_REFERENCE_RE.search(value))


def travel_needs_web_or_browser(text: str) -> bool:
    value = text or ""
    return bool(_TRAVEL_PLACE_REFERENCE_RE.search(value) or travel_is_source_capture(value) or _TRAVEL_CAFE_CURRENT_RE.search(value) or _TRAVEL_LIVE_TRAFFIC_RE.search(value))


def travel_is_route_or_parking(text: str) -> bool:
    value = text or ""
    return bool(_TRAVEL_RE.search(value) and _TRAVEL_ROUTE_PARKING_RE.search(value))


def external_provider_fallback_safe(
    text: str,
    role: str,
    *,
    continued: bool = False,
    requires_execution: bool = False,
) -> bool:
    """Return whether a current-turn-only cross-provider fallback is useful.

    The rule is provider-neutral: block actual credentials, identifiers and
    account-backed data, not broad topics such as medicine, family or CVs.
    Coding and server-analysis prompts are allowed only when no execution is
    required; executable tasks stay on providers that retain their tool path.
    """
    value = (text or "").strip()
    if role not in _EXTERNAL_PROVIDER_SAFE_ROLES:
        return False
    if role in {"coding", "server_debug"} and (
        requires_execution or _ACTION_REQUEST_RE.search(value)
    ):
        return False
    if continued and (
        len(value) < 240 or _EXTERNAL_PROVIDER_CONTINUATION_ONLY_RE.fullmatch(value)
    ):
        return False
    flags = _intent_flags(value)
    if any(flags[name] for name in ("email", "calendar", "drive", "granola", "memory")):
        return False
    return not bool(_EXTERNAL_PROVIDER_SENSITIVE_RE.search(value))


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

    # Forwarded posts, emails and long pasted quotes often contain words such
    # as "логи", "сервис" or "разработчики". They describe the source, not
    # the user's requested action. For technical routing, prefer a short first
    # instruction line when the rest is clearly pasted content.
    nonempty_lines = [line.strip() for line in value.splitlines() if line.strip()]
    technical_intent = value
    if len(nonempty_lines) >= 2 and len(nonempty_lines[0]) <= 240:
        tail = "\n".join(nonempty_lines[1:])
        if len(tail) >= 280 or re.search(r"https?://|^>\s|#[A-Za-zА-Яа-я]", tail, re.M):
            technical_intent = nonempty_lines[0]

    if _SERVER_DEBUG_RE.search(technical_intent):
        return "server_debug", "server/debug intent"
    if _CODING_ACTION_RE.search(technical_intent) and _CODING_OBJECT_RE.search(technical_intent):
        return "coding", "explicit code change intent"
    if _NOTEBOOKLM_RE.search(value):
        return "research", "NotebookLM intent"
    if _RESEARCH_RE.search(value):
        return "research", "external research intent"
    if _EXPERT_ANALYSIS_RE.search(value):
        return "expert_analysis", "expert analysis intent"
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
    elif role == "expert_analysis":
        requested = {"file", "memory", "skills", "todo", "clarify", "no_mcp"}
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
    if _LEARNING_SIGNAL_RE.search(text or ""):
        requested.discard("no_mcp")
        requested.update({"memory", "skills"})
    if flags["report"]:
        requested.update({"file", "memory", "session_search"})
    if flags["email"] or flags["calendar"] or flags["drive"]:
        # Google Workspace is currently exposed through its skill and CLI.
        requested.update({"skills", "terminal", "file"})
    if flags["granola"]:
        requested.discard("no_mcp")
        requested.add("granola")
    if flags["tutu"]:
        requested.discard("no_mcp")
        requested.add("tutu")
    if flags["travel"]:
        requested.discard("no_mcp")
        requested.update({"skills", "terminal"})
        if travel_needs_web_or_browser(text):
            requested.add("web")
            if not _TRAVEL_PLACE_REFERENCE_RE.search(text or ""):
                requested.add("browser")
    if flags["context7"] and role in {"coding", "research", "planning"}:
        requested.discard("no_mcp")
        requested.add("context7")
    if flags["skills_query"]:
        requested.discard("no_mcp")
        requested.update({"skills", "terminal", "file"})
    elif role == "simple" and _AMBIGUOUS_SAVE_RE.fullmatch(text or ""):
        requested.discard("no_mcp")
        requested.add("clarify")
    elif role == "simple" and _BARE_URL_RE.fullmatch(text or ""):
        requested.discard("no_mcp")
        requested.add("clarify")
    elif role == "simple" and not any(flags.values()) and _EXTERNAL_ACTION_RE.search(text or ""):
        requested.discard("no_mcp")
        requested.update({"clarify", "skills", "file", "web", "terminal"})
    if flags["notebooklm"]:
        requested.discard("no_mcp")
        requested.update({"notebooklm", "file", "terminal"})
        if role == "research":
            requested.add("web")

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
    return 24 if role in {"expert_analysis", "coding", "planning", "server_debug", "long_context"} else 12


def compact_operational_context(platform_key: str, role: str) -> str:
    if platform_key != "telegram":
        return ""

    common = (
        "Контекст Telegram Hermes. Отвечай по-русски, если пользователь явно не попросил иначе. "
        "Лучший вывод давай первым, затем краткие основания. Пиши простым человеческим языком, "
        "без длинного тире и без шаблонных AI-фраз. Не соглашайся автоматически: проверяй риски, "
        "слабые места и лучшие альтернативы. Учитывай сохранённую память пользователя, но не выдумывай "
        "факты; для личных, семейных, карьерных и проектных вопросов сначала используй точечный поиск по памяти. "
        "Live-статусом сложной задачи управляет gateway по фактическим подзадачам и действиям инструментов. Не печатай собственный прогресс-бар и не возвращай progress-only вместо результата. "
        "Для обычного информационного запроса отправляй один законченный ответ без READY/PARTIAL, технических метрик и повторного отчёта. Не повторяй тот же вывод вторым блоком. "
        "Верстка Telegram: главный вывод в первых строках; короткие абзацы; пустая строка между смысловыми блоками; жирные мини-заголовки и списки только когда они помогают чтению. Используй не более трёх смысловых эмодзи на весь ответ и не ставь эмодзи в каждый пункт. "
        "Если задача неоднозначна или не хватает ссылки, файла, места, даты либо другого объекта, не возвращай внутреннюю ошибку маршрутизации. Задай один конкретный вопрос: что именно нужно прислать или выбрать, чтобы продолжить. "
        "Когда есть 2–3 понятных варианта, используй clarify с короткими вариантами: Telegram покажет кнопки и оставит возможность написать свой ответ. "
        "Сначала используй ближайший reply-контекст, единственную ссылку или файл из последних сообщений; не переспрашивай очевидное. "
        "Если нужного инструмента действительно нет после проверки skills и доступных способов, коротко объясни причину и что пользователь может сделать. Если работа не выполнена, не утверждай обратное. "
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


def skills_facts_operational_context() -> str:
    return (
        "Контракт фактов о навыках Hermes: перед ответом обязательно проверь фактический "
        "список и содержимое навыков через skills/terminal. Не утверждай, что навык установлен, "
        "существует, вызывается по ключевым словам или имеет определённые функции, без результата "
        "инструмента либо прочитанного SKILL.md. Отделяй установленные локальные навыки от upstream. "
        "Не выдумывай команды установки. Если проверка недоступна, верни BLOCKED, а не рекомендацию."
    )


def travel_operational_context() -> str:
    return (
        "Контракт городских поездок: используй предзагруженный skill city-travel-concierge и реальные "
        "инструменты до ответа. Для обычных маршрутов, ETA, ссылки Яндекс Карт и парковок сначала вызови "
        "один агрегирующий helper через terminal: `city_travel_trip.py --start ... --destination ...`. "
        "Не ищи реализацию skill через search_files и не перечитывай SKILL.md, если skill уже предзагружен. "
        "Не читай, не выводи и не проверяй содержимое credential store; helper сам получает credentials. "
        "Access denied на credential store не повторяй: используй helper либо верни точный блокер. "
        "Если helper вернул маршрут, ссылку и хотя бы parking candidates, данных достаточно для ответа: сразу "
        "сформируй короткий пользовательский ответ и не выполняй web_search для уверенности. "
        "Максимум одна альтернативная попытка при падении конкретного provider; после двух одинаковых ошибок остановись. "
        "Сначала определи тип источника по URL текущего сообщения: Яндекс Карты, Google Maps, 2ГИС, Instagram/Reels, TikTok, YouTube или обычная веб-страница. "
        "URL текущего сообщения имеет приоритет над историей. Не добавляй Instagram, рилс, карту или иной источник из прошлых сообщений, если его нет в текущем сообщении или явном reply-контексте и совпадение объекта не проверено. "
        "Ссылка на карту означает карточку указанного объекта, а не подтверждает связь с прошлым рилсом. Для карт используй web и не запускай платный cloud browser. "
        "Для Instagram/TikTok сначала используй web-поиск и видимые метаданные, browser только как резерв после неудачи web. "
        "Сначала классифицируй содержание публикации: физическое место, событие, товар/услуга, совет/подборка или другое. Travel-place сохраняй только для однозначно определённого физического места. "
        "Для ссылки без команды ничего не сохраняй автоматически: дай суть и предложи один уместный вариант действия. "
        "Для текущих отзывов, рейтингов, режима работы и актуальных часов кафе используй web или browser. "
        "Если пользователь прислал ссылку, рилс или пост и просит сохранить место для будущей поездки, не проси "
        "геолокацию до попытки извлечения. Сначала открой исходный материал через web/browser, определи название, "
        "локацию и описание, затем отдельно проверь изменяемые данные: цены, расписание и часы работы. Разделяй "
        "утверждения из публикации и подтверждённые актуальные факты; ничего не додумывай. После сбора через file "
        "запиши UTF-8 JSON в `/home/hermes/.hermes/tmp/travel-place-save.json`, затем выполни только фиксированную "
        "ASCII-команду `/usr/bin/python3 /home/hermes/.hermes/skills/productivity/city-travel-concierge/scripts/"
        "travel_place_save_from_file.py`. Не помещай пользовательский текст прямо в shell-команду."
        "В JSON поле source_url должно быть URL текущего сообщения, а verified_source_url содержит только реально открытые проверочные страницы. "
        "Для price, schedule и hours укажи verified_fields только если значение явно найдено в текущем или проверочном источнике; предположения и пометки «обычно» или «уточнять» не сохраняй. "
        "readback_count означает число совпавших фрагментов проверки, а не количество отдельных записей; не сообщай «создано 3 записи». "
        "Говори «сохранено» только если helper вернул saved=true либо already_exists=true вместе с readback_count>0. При ошибке верни "
        "точные status и message; не переходи к generic memory и не ищи код skill. Если Instagram недоступен, используй "
        "видимую подпись/метаданные и одну альтернативную попытку поиска по названию или ключевым словам. Один точный "
        "уточняющий вопрос допустим только если после этого место всё ещё нельзя определить однозначно. "
        "Не оценивай время в пути, расстояние, парковку или рейтинг по памяти. "
        "Geoapify не учитывает live traffic: всегда помечай это и давай ссылку Яндекс Карт для проверки перед "
        "выездом. Парковку называй бесплатной только при официальном подтверждении или свежем подтверждении "
        "пользователя; OSM fee=no допускает лишь статус likely_free, остальные кандидаты unverified. "
        "Не советуй дворы, частную территорию, access=private/customers/permit или место без проверяемой точки. "
        "Для кафе по отзывам верни не более 3 вариантов: текущий рейтинг, число отзывов, расстояние, почему подходит "
        "для детей и прямую ссылку на источник; места без проверяемых отзывов не ранжируй. Если объект неоднозначен, "
        "сначала разреши адрес/координаты через skill, а не угадывай."
    )


def notebooklm_operational_context() -> str:
    return (
        "Контракт NotebookLM: используй настоящий NotebookLM MCP. Дождись завершения каждого артефакта, "
        "скачай артефакты и проверь существование файлов перед отчётом. После скачивания используй штатный "
        "terminal, предпочтительно stat -c '%n|%s' <path>, чтобы подтвердить точный размер больше нуля. "
        "Не создавай Python, shell или другие helper-скрипты только для проверки размера и не используй "
        "patch/write_file для проверки уже существующего артефакта. Для источников с ограничением свежести "
        "сохраняй название, URL и точную дату публикации; источник без подтверждаемой даты не засчитывай. "
        "READY допустим только после завершения генерации, скачивания, проверки существования и ненулевого "
        "размера файла, успешной отправки и подтверждения нужного числа URL/дат. Иначе верни PARTIAL или "
        "BLOCKED. Итоговый отчёт пользователю пиши на русском."
    )


def adaptive_planning_operational_context(policy: AdaptivePlanningPolicy) -> str:
    if policy.mode == "none":
        return ""
    if policy.mode == "brief":
        return (
            "Адаптивное планирование: это средняя задача. Перед действиями составь короткий внутренний "
            "план из 2–4 шагов. Не вызывай дополнительную модель и не показывай пользователю отдельный "
            "методологический блок. Выполняй задачу сразу и сохраняй текущий формат прогресса и финала."
        )

    common = (
        "Адаптивное планирование: используй предзагруженный skill plan в adaptive execution mode. "
        "Если пользователь запросил только план, не выполняй изменения. Если вместе с планом запросил "
        "реализацию, не останавливайся после плана. Не создавай отдельный plan-файл без явного запроса. "
        "Пользователю показывай только: цель одной строкой, план из 3–7 пунктов, до 3 рисков и DoD из "
        "2–5 критериев. Прогресс: текущий этап, подтверждённый результат и блокер. Финал оставь в текущем "
        "формате READY / PARTIAL / BLOCKED."
    )
    if not policy.reviewer_required:
        return common
    return common + (
        " После чернового плана вызови ровно одного reviewer через delegate_task с role=leaf. Reviewer "
        "не должен менять файлы или выполнять реализацию. Передай ему цель, план, риски, rollback и UX "
        "финального отчёта; попроси максимум 5 замечаний по пропускам, переусложнению и безопасности. "
        "Используй настроенный delegation runtime. Если reviewer наследует ту же модель или провайдер, "
        "называй его reviewer в отдельном контексте, а не независимой моделью. Если delegate_task "
        "недоступен или завершился ошибкой, сделай один явно обозначенный self-review и не называй его "
        "независимым. Не делегируй саму реализацию и не вызывай второго reviewer."
    )


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
    planning_policy = adaptive_planning_policy(text, role)
    if planning_policy.reviewer_required and role in {"simple", "parser"}:
        role = "expert_analysis"
        reason = f"{reason}; promoted_for={planning_policy.reason}"
    intent_names = [name for name, enabled in flags.items() if enabled]
    if intent_names:
        reason = f"{reason}; intents={','.join(intent_names)}"
    if planning_policy.mode != "none":
        reason = f"{reason}; adaptive_plan={planning_policy.mode}:{planning_policy.reason}"
    agent_cfg = (user_config or {}).get("agent") if isinstance(user_config, Mapping) else {}
    if not isinstance(agent_cfg, Mapping):
        agent_cfg = {}

    skill_names_list: list[str] = []
    if flags["email"] or flags["calendar"] or flags["drive"]:
        skill_names_list.append("google-workspace")
    if flags["travel"]:
        skill_names_list.append("city-travel-concierge")
    if planning_policy.preload_plan_skill:
        skill_names_list.append("plan")
    skill_names = tuple(dict.fromkeys(skill_names_list))

    toolsets = select_toolsets(role, text, platform_toolsets)
    if planning_policy.preload_plan_skill:
        toolsets = _apply_platform_policy(set(toolsets) | {"file", "skills"}, platform_toolsets)
    if planning_policy.reviewer_required:
        toolsets = _apply_platform_policy(set(toolsets) | {"delegation"}, platform_toolsets)

    operational_context = compact_operational_context(platform_key, role)
    adaptive_context = adaptive_planning_operational_context(planning_policy)
    if adaptive_context:
        operational_context = (operational_context + "\n\n" + adaptive_context).strip()
    if _LEARNING_SIGNAL_RE.search(text or ""):
        operational_context = (operational_context + "\n\n" +
            "Сигнал обучения: это может быть устойчивое предпочтение, коррекция поведения или успешный способ работы. "
            "Сохраняй только действительно повторно полезное правило; не создавай дубль и после записи сделай read-back.").strip()
    max_iterations = max_turns_for_role(role, agent_cfg)
    if planning_policy.reviewer_required:
        max_iterations = max(max_iterations, 24)
    if flags["skills_query"]:
        operational_context = (operational_context + "\n\n" + skills_facts_operational_context()).strip()
    if flags["travel"]:
        operational_context = (operational_context + "\n\n" + travel_operational_context()).strip()
        if travel_needs_web_or_browser(text):
            max_iterations = max(max_iterations, 20)
        else:
            max_iterations = min(max_iterations, 8)
    if flags["notebooklm"]:
        operational_context = (operational_context + "\n\n" + notebooklm_operational_context()).strip()
        max_iterations = max(max_iterations, 36)
    return TaskRoute(
        role=role,
        reason=reason,
        toolsets=toolsets,
        max_iterations=max_iterations,
        skill_names=skill_names,
        # Telegram intentionally skips the large repository AGENTS.md/SOUL.md;
        # compact personality and role safety rules are supplied above instead.
        skip_context_files=(platform_key == "telegram"),
        operational_context=operational_context,
    )
