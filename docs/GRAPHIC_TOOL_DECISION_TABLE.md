# Hermes Graphic Tool Decision Table

Дата: 2026-06-16  
Статус: decision matrix обновлена после runtime probe. `openai-codex` проверен как рабочий основной канал image generation через ChatGPT/Codex OAuth, без fal.ai direct, без OpenAI API key и без Gemini API key.

## Executive summary

| Сценарий | Лучший инструмент | Запасной инструмент | Комментарий |
|---|---|---|---|
| Фотореализм / художественная сцена | `openai-codex` (`gpt-image-2-medium`) | Managed `image_generate` только если verified in-session | `openai-codex` реально сгенерировал PNG через ChatGPT/Codex OAuth; fal.ai/OpenAI API/Gemini API не использовать без отдельного approval |
| Арт / иллюстрация / логотип | `openai-codex` (`gpt-image-2-medium`) | Nano Banana later / SVG for simple logos | Для production logo с точной геометрией лучше дорабатывать SVG |
| Минималистичная иконка | SVG/HTML first, GPT/Nano for exploratory concepts | Recraft/Krea | Если нужна точность и масштабируемость - SVG; если нужен стиль - image model |
| Русский текст в баннере | SVG/HTML | Ideogram/Qwen/GPT Image 2 после QA | Image generation запрещать как default, разрешать только как декоративный фон без критичного текста |
| Английский короткий текст | SVG/HTML для точности, GPT/Nano для постерного арта | Ideogram | Даже если image model умеет текст, нужен visual QA |
| Image editing по существующему изображению | Gemini/Nano Banana через edit-capable integration | OpenAI Images API edit endpoint | Текущий Hermes `image_generate` не имеет параметра input image/mask |
| Day-to-night edit | Gemini/Nano Banana edit | OpenAI edit | Требуется reference/edit API, не текущий text-only tool |
| Style reference generation | Gemini/Nano Banana или Krea refs | GPT Image if API supports refs separately | Текущий Hermes schema блокирует reference input |
| Character/mascot consistency | Gemini/Nano Banana | Character bible + repeated prompts | Нужен реальный reference support и visual QA |
| Серия презентационных визуалов | NotebookLM brief -> outline -> HTML/PPTX -> image backgrounds отдельно | SVG/HTML | Не генерировать слайды целиком картинками |
| Архитектурная схема | Mermaid | SVG/HTML | Image models запрещать: нет точности, трудно редактировать |
| UI / dashboard / table | HTML/CSS/SVG | Mermaid для flow | Image models запрещать для текста и интерактивности |

## Tool availability conclusion

| Tool family | Реально найдено | Runtime подтверждение | Текущая роль |
|---|---|---|---|
| GPT Image / OpenAI branch | `openai-codex` plugin: `gpt-image-2-medium`; также есть paid API/fal catalog | Подтверждено реальной генерацией через ChatGPT/Codex OAuth | Primary text-to-image канал для Hermes, если OAuth доступен |
| Gemini / Nano Banana | `fal-ai/nano-banana-pro` в каталоге | Не подтверждено платным вызовом | Text-to-image через FAL path при выборе модели |
| NotebookLM | `nlm` установлен, `profile hermes-vps authorized` | Подтверждено read-only auth check | Brief/research/outline layer |
| Mermaid/SVG/HTML | Доступны через файлы | Подтверждено созданными outputs | Точные схемы, текст, UI, fallback |

## Router rules for suggest-mode Hermes

1. Если запрос содержит точный текст, русский текст, таблицу, UI, схему, sequence, architecture, flow - сразу предлагать Mermaid/SVG/HTML, не image model.
2. Если запрос - арт, moodboard, иллюстрация, фотореализм, mascot concept - предлагать GPT Image или Nano Banana, с предупреждением о стоимости и visual QA.
3. Если запрос - image edit, reference, consistency, same character - не использовать текущий `image_generate`; маршрутизировать в `needs_edit_capable_provider` до появления input-image support.
4. Если запрос - презентация - маршрут: brief -> outline -> HTML/PPTX -> отдельные visuals. NotebookLM только для brief/research, не для генерации финальных слайдов.
5. Если image model возвращает URL - показывать пользователю через Markdown image. Если создан локальный SVG/HTML - отправлять как файл или inline preview в Telegram.
6. Для платных генераций в safe-write режиме спрашивать подтверждение: количество outputs, примерная стоимость, модель.
7. Для обычной image generation команды сначала проверять `openai-codex`: если `OpenAICodexImageGenProvider.is_available()` true, использовать `image_gen.provider=openai-codex` и `image_gen.model=gpt-image-2-medium`.
8. Если `openai-codex` недоступен из-за OAuth expired/auth/403/quota/network/parser, не уходить в fal.ai/OpenAI API/Gemini API автоматически. Разрешённый fallback: уже verified managed `image_generate` channel только если он явно доступен в текущем runtime; иначе вернуть понятный blocker и запросить approval.

## Suggested internal routing labels

| Label | Trigger | Action |
|---|---|---|
| `graphic.art.generate` | photo, illustration, logo concept, background | Use `openai-codex` first when available; ask approval before any paid API/fal/Gemini branch |
| `graphic.text.exact` | banner text, Russian text, labels, table | SVG/HTML only |
| `graphic.diagram.precise` | architecture, flow, sequence, class diagram | Mermaid first, SVG export optional |
| `graphic.edit.reference` | edit existing image, style reference, same character | Block current `image_generate`; require edit-capable integration |
| `presentation.chain` | deck, slides, presentation | NotebookLM/brief -> outline -> HTML/PPTX -> visuals |

## Risks and limitations

- `openai-codex` runtime availability was proven by actual PNG generation: `.tasks/2026-06-16_image_runtime_probe/outputs/openai_codex_robot_dashboard.png`.
- Current Hermes image tool is text-to-image only at schema level. It cannot pass reference images or masks.
- Model catalog availability does not guarantee Nous/FAL gateway allowlist availability for every model.
- Image URLs may expire or remain remote; for durable docs, download/copy policy is needed, but it was not exercised here.
- Text inside raster images still requires visual QA; for Russian text default should remain SVG/HTML.

## Next safe step

Apply the safe config change after confirmation: set `image_gen.provider=openai-codex` and `image_gen.model=gpt-image-2-medium`; do not restart gateway without separate approval. A code-level fallback wrapper is a separate change if automatic fallback is required.
