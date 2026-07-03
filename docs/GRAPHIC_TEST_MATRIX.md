# Hermes Graphic Tools A/B Test Matrix

Дата: 2026-06-16  
Режим: safe-write  
Статус image-прогона: обновлено после safe runtime probe. `openai-codex` реально сгенерировал PNG через ChatGPT/Codex OAuth; fal.ai direct, OpenAI API key и Gemini API key не использовались.

## Capability audit

### Проверенный источник

Аудит выполнен read-only по коду `/home/hermes/.hermes/hermes-agent/tools/image_generation_tool.py` и безопасным локальным командам. Секреты, OAuth-файлы, env и runtime не читались и не менялись.

### Текущее состояние Hermes image tool

| Пункт | Факт |
|---|---|
| Agent-facing tool | `image_generate` |
| Публичные параметры | `prompt`, `aspect_ratio` |
| Возврат | JSON/result с `success` и `image`; после postprocess - URL или локальный путь, показывать через Markdown `![описание](url-or-path)` |
| Активная модель в config | `fal-ai/flux-2/klein/9b` - FLUX 2 Klein 9B |
| Сохранение файлов | Если backend возвращает URL, Hermes показывает URL; локальная копия не гарантирована. При локальном результате возможен `agent_visible_image`. |
| Runtime/config changes | Не выполнялись |

### Проверенный `openai-codex` канал

| Пункт | Факт |
|---|---|
| Provider | `openai-codex` |
| Class | `OpenAICodexImageGenProvider` |
| Path | `plugins/image_gen/openai-codex/__init__.py` |
| Availability | `provider.is_available() == True` under repo venv |
| Auth | ChatGPT/Codex OAuth via Hermes auth helpers, token not logged |
| Endpoint | `/backend-api/codex/responses` |
| Host model | `gpt-5.5` |
| Tool | `image_generation` |
| Image model/tier | `gpt-image-2-medium` |
| Verified output | `.tasks/2026-06-16_image_runtime_probe/outputs/openai_codex_robot_dashboard.png` |
| Output check | PNG, 1536x1024, RGB, magic bytes OK |

Safe config target, not yet applied in this task without explicit confirmation: `image_gen.provider=openai-codex`, `image_gen.model=gpt-image-2-medium`.

### GPT/OpenAI ветка в каталоге FAL

| Model ID | Display | Text-to-image | Image editing | Reference generation | Notes |
|---|---:|---:|---:|---:|---|
| `fal-ai/gpt-image-1.5` | GPT Image 1.5 | Да | Нет через текущий Hermes schema | Нет через текущий Hermes schema | `supports`: prompt, image_size, quality, num_images, output_format, background, sync_mode |
| `fal-ai/gpt-image-2` | GPT Image 2 | Да | Нет через текущий Hermes schema | Нет через текущий Hermes schema | `supports`: prompt, image_size, quality, num_images, output_format, sync_mode |

Вывод: GPT Image есть в кодовом каталоге, но runtime-доступ не подтвержден платным вызовом. Через текущий agent-facing `image_generate` доступен только text-to-image.

### Gemini / Nano Banana ветка

| Model ID | Display | Text-to-image | Image editing | Reference generation | Notes |
|---|---:|---:|---:|---:|---|
| `fal-ai/nano-banana-pro` | Nano Banana Pro (Gemini 3 Pro Image) | Да | Нет через текущий Hermes schema | Нет через текущий Hermes schema | `supports`: prompt, aspect_ratio, num_images, output_format, safety_tolerance, seed, sync_mode, resolution, enable_web_search, limit_generations |

Вывод: Nano Banana Pro есть в кодовом каталоге. В текущей Hermes tool schema нет входов для reference image/edit mask, поэтому edit/reference сценарии нельзя честно прогнать через текущий `image_generate` без отдельной интеграции или обходного API.

### NotebookLM

| Проверка | Факт |
|---|---|
| CLI | `nlm` установлен |
| Auth | `profile hermes-vps authorized` |
| Роль в графическом пайплайне | Research/brief layer, не image generator |
| Безопасный статус | Можно использовать для brief/outline, но в этом прогоне не создавались notebooks/artifacts |

### Non-image fallback

| Инструмент | Статус | Лучшие сценарии |
|---|---|---|
| Mermaid | Доступен как текстовый артефакт | Архитектура, flow, sequence, router boundary |
| SVG | Доступен через safe-write файлы | Точный русский/английский текст, баннеры, простые схемы |
| HTML/CSS | Доступен через safe-write файлы | UI, презентационные layout, отчеты, сложные текстовые визуалы |

## 12-case test matrix

| ID | Группа | Prompt / задача | GPT Image 2 | Nano Banana Pro | NotebookLM | Non-image fallback | Ожидаемый победитель |
|---|---|---|---|---|---|---|---|
| A1 | Pure generation | Робот-помощник читает бумажную книгу у окна, мягкий утренний свет | `openai-codex` verified on equivalent safe prompt | Planned, not run | Не нужен | Не нужен | `openai-codex` first |
| A2 | Pure generation | Киберпанк-логотип для Telegram-бота Антона | `openai-codex` verified on equivalent safe prompt | Planned, not run | Не нужен | SVG fallback возможен для простого логотипа | `openai-codex` first |
| A3 | Pure generation | Плоская иконка семейного ассистента | Planned, not run | Planned, not run | Не нужен | SVG fallback хорош для production icon | SVG или GPT/Nano в зависимости от style |
| B1 | Text in image | Баннер с русским текстом: Еженедельный отчёт Hermes / Стабильность • Контекст • Автоматизация | Planned, not run | Planned, not run | Не нужен | `B1_html.svg` создан | SVG/HTML |
| B2 | Text in image | Постер с коротким английским текстом | Planned, not run | Planned, not run | Не нужен | `B2_html.svg` создан | GPT/Nano для art, SVG для точного текста |
| C1 | Image editing | Добавить чашку кофе на стол, сохранив всё остальное | Blocked by current schema | Blocked by current schema | Не нужен | Неприменимо | Gemini/Nano после edit-capable integration |
| C2 | Image editing | Сделать дневную сцену ночной | Blocked by current schema | Blocked by current schema | Не нужен | Неприменимо | Gemini/Nano после edit-capable integration |
| D1 | Reference | Новое изображение по референсу стиля | Blocked by current schema | Blocked by current schema | Может подготовить style brief | Неприменимо | Gemini/Nano или Krea после reference support |
| D2 | Reference | Новая сцена с сохранением внешности персонажа/маскота | Blocked by current schema | Blocked by current schema | Может подготовить character bible | Неприменимо | Gemini/Nano после reference support |
| E1 | Consistency | 3 сцены с одним персонажем | Planned as multi-prompt, not run | Planned as multi-prompt, not run | Может подготовить character bible | Неприменимо | Gemini/Nano, но нужен real visual QA |
| E2 | Consistency | 3 визуала для одной презентации в едином стиле | Planned as multi-prompt, not run | Planned as multi-prompt, not run | Да, brief/outline | HTML/PPTX + отдельные visuals | NotebookLM brief + HTML/PPTX + image backgrounds |
| F1 | Router boundary | gateway -> cli-proxy-api -> run_agent.py | Not appropriate | Not appropriate | Не нужен | `F1_mermaid.md`, `F1_svg.svg` созданы | Mermaid/SVG |

## Scoring rubric

0 - не выполнено / неприменимо; 1 - плохо; 2 - слабо; 3 - приемлемо; 4 - хорошо; 5 - отлично.

Критерии: `prompt_adherence`, `visual_quality`, `text_fidelity`, `edit_fidelity`, `reference_fidelity`, `consistency`, `speed`, `reliability`, `telegram_readiness`, `tool_appropriateness`, `fallback_quality`.

## Evaluated results in this safe run

| Output | Case | Tool | prompt_adherence | visual_quality | text_fidelity | edit_fidelity | reference_fidelity | consistency | speed | reliability | telegram_readiness | tool_appropriateness | fallback_quality | Verdict |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `B1_html.svg` | B1 | SVG | 5 | 4 | 5 | 0 | 0 | 4 | 5 | 5 | 5 | 5 | 5 | Winner for Russian text |
| `B2_html.svg` | B2 | SVG | 5 | 4 | 5 | 0 | 0 | 4 | 5 | 5 | 5 | 4 | 5 | Best when exact text matters |
| `F1_mermaid.md` | F1 | Mermaid | 5 | 3 | 5 | 0 | 0 | 5 | 5 | 5 | 5 | 5 | 5 | Winner for architecture flow |
| `F1_svg.svg` | F1 | SVG | 5 | 4 | 5 | 0 | 0 | 5 | 5 | 5 | 5 | 5 | 5 | Better visual delivery than raw Mermaid |
| `openai_codex_robot_dashboard.png` | A/probe | `openai-codex` | 5 | 5 | 5 | 0 | 0 | 4 | 4 | 5 | 5 | 5 | 0 | Primary raster image channel verified |

## Not-run image result slots

| Planned file | Status | Reason |
|---|---|---|
| `A1_gpt.png` | not_run | Paid generation not approved |
| `A1_gemini.png` | not_run | Paid generation not approved |
| `A2_gpt.png` | not_run | Paid generation not approved |
| `A2_gemini.png` | not_run | Paid generation not approved |
| `A3_gpt.png` | not_run | Paid generation not approved |
| `A3_gemini.png` | not_run | Paid generation not approved |
| `B1_gpt.png` | not_run | Paid generation not approved; SVG preferred anyway |
| `B1_gemini.png` | not_run | Paid generation not approved; SVG preferred anyway |
| `B2_gpt.png` | not_run | Paid generation not approved |
| `B2_gemini.png` | not_run | Paid generation not approved |
| `C1_gpt.png` | blocked | Current Hermes schema lacks edit/reference image input |
| `C1_gemini.png` | blocked | Current Hermes schema lacks edit/reference image input |
| `C2_gpt.png` | blocked | Current Hermes schema lacks edit/reference image input |
| `C2_gemini.png` | blocked | Current Hermes schema lacks edit/reference image input |
| `D1_gpt.png` | blocked | Current Hermes schema lacks reference input |
| `D1_gemini.png` | blocked | Current Hermes schema lacks reference input |
| `D2_gpt.png` | blocked | Current Hermes schema lacks reference input |
| `D2_gemini.png` | blocked | Current Hermes schema lacks reference input |
| `E1_gpt_1-3.png` | not_run | Paid generation not approved |
| `E1_gemini_1-3.png` | not_run | Paid generation not approved |
| `E2_gpt_1-3.png` | not_run | Paid generation not approved |
| `E2_gemini_1-3.png` | not_run | Paid generation not approved |
