"""Deterministic intent guard for the Telegram Avito seller workflow."""

from __future__ import annotations

from dataclasses import dataclass
import re


AVITO_INTENT = "avito_sale"
AVITO_MODE_LABEL = "Продажа на Avito"
AVITO_TOOLSETS = ("avito", "clarify", "image_gen", "no_mcp", "vision")

_ATTACHMENT_LINE_RE = re.compile(
    r"^\s*\[(?:The user sent|User sent|Replying to:|System continuation:).*$",
    re.I,
)
_IMAGE_RE = re.compile(
    r"\[The user sent an image|\[User sent an image|vision_analyze|image_url:|"
    r"media_urls?=|image attachment|photo attachment",
    re.I,
)
_CANONICAL_MODE_RE = re.compile(r"(?<![\w@])@(?:avito|авито)\b", re.I)
_AVITO_RE = re.compile(
    r"(?<!\w)(?:avito|авит[оo]?|авитто|авитоo|а\s+вито)(?!\w)|"
    r"(?<!\w)авитo(?!\w)",
    re.I,
)
_SELL_RE = re.compile(
    r"\b(?:прод(?:ать|аем|аём|аю|ажа|ажу)|выстав(?:ить|и|ляем)|размест(?:ить|и)|"
    r"объявлени|текст\s+(?:для\s+)?продаж|продающ\w*\s+текст|"
    r"цен[ау]\s+(?:для\s+)?продаж|за\s+сколько\s+продать|торг)\w*\b",
    re.I,
)
_DIRECT_SELL_RE = re.compile(
    r"\b(?:прод(?:ать|аем|аём|аю|ажа|ажу)|выстав(?:ить|и|ляем)|размест(?:ить|и))\w*\b",
    re.I,
)
_DRAFT_RE = re.compile(
    r"\b(?:сделай|подготовь|напиши|составь|оформи)\w*\b.{0,70}"
    r"\b(?:объявлени|описани|текст\s+продаж|карточк\w*\s+товар)\w*\b",
    re.I | re.S,
)
_PRICE_ONLY_RE = re.compile(
    r"^\s*(?:найди|проверь|посмотри|оцени)\w*\s+(?:цен[уы]|стоимость)|"
    r"^\s*(?:сколько\s+стоит|нормальная\s+цена|оцени\s+(?:это|его|ее|её))",
    re.I,
)
_OTHER_PLATFORM_RE = re.compile(
    r"\b(?:юл[аеуы]|vk|вконтакте|telegram|телеграм|wildberries|озон|ozon|"
    r"маркетплейс|сайт\w*\s+компани)\b",
    re.I,
)
_NEGATIVE_RE = re.compile(
    r"\b(?:не\s+(?:для|на)\s+авито|не\s+прода(?:ем|ём|ю|вать)|"
    r"объявлени\w*\s+(?:пока\s+)?не\s+(?:делай|готовь|нужно))\b",
    re.I,
)
_BUY_RE = re.compile(
    r"\b(?:хочу|нужно|надо|планирую)\s+купить\b|\b(?:купить|покупк|для\s+покупки)\w*\b",
    re.I,
)
_KNOWLEDGE_RE = re.compile(
    r"\b(?:добавь|сохрани|запиши)\w*\b.{0,60}\b(?:баз[уы]|knowledge|памят[ьи])\b",
    re.I | re.S,
)
_LISTING_ANALYSIS_RE = re.compile(
    r"\b(?:посмотри|разбери|проанализируй|оцени|проверь)\w*\b.{0,80}"
    r"\b(?:чуж\w*\s+)?объявлени\w*\b|https?://(?:www\.)?avito\.ru/",
    re.I | re.S,
)
_OBJECT_EVIDENCE_RE = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*(?:л|литр|мл|кг|г|см|мм|м|шт|штук)|"
    r"\b(?:нов\w*|б/у|бу\b|использован\w*|состояни\w*|комплект\w*|"
    r"бутыл\w*|товар\w*|вещ\w*)\b",
    re.I,
)
_AVITO_FOLLOWUP_RE = re.compile(
    r"^\s*(?:ещ[её]\s+(?:фото|фотографи)|добавь\s+(?:торг|фото)|"
    r"измени\s+цен|цен[ау]\s*[:=-]?\s*\d+|переделай\s+(?:описани|обложк)|"
    r"покажи\s+(?:ещ[её]|друг)\w*\s+аналог|сделай\s+(?:друг|втор)\w*\s+обложк)",
    re.I,
)
_TOPIC_SWITCH_RE = re.compile(
    r"\b(?:погод|календар|напомни|маршрут|поездк|ресторан|врач|емиаc|емиас|"
    r"добавь\s+в\s+(?:баз|памят)|хочу\s+купить)\w*\b",
    re.I,
)


@dataclass(frozen=True)
class AvitoIntentDecision:
    kind: str
    reason: str
    has_image: bool = False


def _visible_text(text: str) -> str:
    lines = [line for line in str(text or "").splitlines() if not _ATTACHMENT_LINE_RE.match(line)]
    return "\n".join(lines).strip()


def detect_avito_intent(text: str, *, context_text: str = "") -> AvitoIntentDecision:
    """Return ``avito``, ``clarify`` or ``other`` before any tool execution."""
    raw = "\n".join(part for part in (str(text or ""), str(context_text or "")) if part)
    visible = _visible_text(text)
    evidence = "\n".join(part for part in (visible, _visible_text(context_text)) if part)
    has_image = bool(_IMAGE_RE.search(raw))

    if _NEGATIVE_RE.search(evidence):
        return AvitoIntentDecision("other", "explicit Avito/sale negation", has_image)
    if _OTHER_PLATFORM_RE.search(evidence):
        return AvitoIntentDecision("other", "another sales platform named", has_image)
    if _BUY_RE.search(evidence):
        return AvitoIntentDecision("other", "buy intent", has_image)
    if _KNOWLEDGE_RE.search(evidence):
        return AvitoIntentDecision("other", "knowledge-save intent", has_image)
    if (
        _LISTING_ANALYSIS_RE.search(evidence)
        and not _DIRECT_SELL_RE.search(evidence)
        and not _DRAFT_RE.search(evidence)
    ):
        return AvitoIntentDecision("other", "existing-listing analysis", has_image)

    canonical_mode = bool(_CANONICAL_MODE_RE.search(visible))
    avito_named = bool(_AVITO_RE.search(visible))
    sale_intent = bool(_SELL_RE.search(visible) or _DRAFT_RE.search(visible))
    object_evidence = bool(has_image or _OBJECT_EVIDENCE_RE.search(evidence))

    if canonical_mode:
        return AvitoIntentDecision("avito", "explicit @avito mode", has_image)
    if avito_named and (sale_intent or object_evidence or len(visible.split()) <= 3):
        return AvitoIntentDecision("avito", "explicit Avito sale context", has_image)
    if sale_intent and object_evidence:
        return AvitoIntentDecision("avito", "item plus sale intent", has_image)
    if has_image and not visible:
        return AvitoIntentDecision("clarify", "image without action", True)
    if _PRICE_ONLY_RE.search(visible) or _DRAFT_RE.search(visible) or sale_intent:
        return AvitoIntentDecision("clarify", "sale or price request without enough routing context", has_image)
    return AvitoIntentDecision("other", "no Avito seller signal", has_image)


def avito_clarification_question(decision: AvitoIntentDecision) -> str:
    if decision.has_image:
        return "Что сделать с фотографиями: подготовить объявление для Avito, только оценить цену или другое?"
    return "Это нужно для продажи на Avito, только для оценки цены или для другой задачи?"


def is_avito_task_metadata(metadata: dict | None) -> bool:
    return isinstance(metadata, dict) and metadata.get("intent") == AVITO_INTENT


def is_avito_followup(text: str, *, context_text: str = "") -> bool:
    raw = "\n".join(part for part in (str(text or ""), str(context_text or "")) if part)
    visible = _visible_text(text)
    if _TOPIC_SWITCH_RE.search(visible) or _NEGATIVE_RE.search(visible):
        return False
    decision = detect_avito_intent(text, context_text=context_text)
    if decision.kind == "avito":
        return True
    if decision.has_image and not visible:
        return True
    return bool(_AVITO_FOLLOWUP_RE.search(visible) or (_IMAGE_RE.search(raw) and len(visible) < 80))
