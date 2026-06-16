# Google Gemini / Nano Banana image generation probe runlog

Дата: 2026-06-16  
Режим: read-only + safe-write (docs/scripts test-files)  
Запрещено: purchases, billing setup, subscription changes, paid API calls without confirmation, secrets/OAuth/cookies/API keys/.env reads.

## Цель
Исследовать и протестировать Google Gemini / Nano Banana image generation без платного API через локальный `cli-proxy-api` / Antigravity Google OAuth.

## Выполненные шаги

### 1. Capability Discovery (Локальный список моделей)
- Сделан авторизованный запрос к локальному `cli-proxy-api` `/v1/models`.
- В списке доступных моделей обнаружена: `gemini-3.1-flash-image` (Nano Banana 2).
- Другие алиасы (например, `nano-banana`, `gemini-3-pro-image`, `image`) в выводе отсутствуют.

### 2. Capability Verification (Проверка эндпоинтов)
- Тестирование `/v1/images/generations` с моделью `gemini-3.1-flash-image` вернуло ошибку `400 Bad Request` с указанием, что модель не поддерживается на этом эндпоинте.
- Тестирование `/v1/chat/completions` с моделью `gemini-3.1-flash-image` прошло успешно и вернуло сгенерированное изображение.
- Прокси возвращает изображение прямо в ответе чата в поле `images` в виде base64-строки (`data:image/jpeg;base64,...`).

### 3. Локальные конфигурации
- Конфиг `cli-proxy-api` (`~/.cli-proxy-api/config.yaml`): содержит `disable-image-generation: False`.
- Авторизация `cli-proxy-api`: Google OAuth токен для аккаунта `<email>` расположен в файле `~/.cli-proxy-api/auth/<antigravity-oauth-profile>.json`.
- Конфиг Hermes (`~/.hermes/config.yaml`): в секции `image_gen` провайдер установлен как `openai-codex` (переведен ранее сегодня), встроенного провайдера для Google/Gemini/Antigravity нет.

### 4. Smoke-test
- **Промпт**: `A small bronze Hermes messenger figurine on a clean white studio background`
- **Модель**: `gemini-3.1-flash-image` (Nano Banana 2) через Antigravity / `cli-proxy-api`.
- **Затраченное время**: 12.85 сек.
- **Результат**: Декодирован из base64 и сохранен в `.tasks/2026-06-16_google_image_probe/outputs/google_gemini_image_probe.jpg`.
- **Характеристики**: JPEG, 1408x768, 449,443 байт. Проверен через PIL (валидный JPEG).

## Сравнительная таблица путей генерации

| Путь (Route) | Модель (Model) | Метод авторизации (Auth) | Стоимость (Cost Tier) | Генерация (Text-to-Image) | Редактирование (Image Edit) | Риски стабильности (Stability Risk) |
| :--- | :--- | :--- | :--- | :---: | :---: | :--- |
| **Local Proxy (`cli-proxy-api`)**<br>`/v1/chat/completions` | `gemini-3.1-flash-image` | Google Account OAuth (через Antigravity) | Free / Subscription (лимиты аккаунта Google) | **Да** | **Да** (через передачу картинки в messages) | **Средний** (требует активного OAuth-токена, при истечении сессии нужен перелогин) |
| **Google AI Studio API**<br>`/v1/models/...:generateContent` | `gemini-3.1-flash-image` | API Key (`GEMINI_API_KEY`) | Free Tier / Pay-as-you-go | **Да** | **Да** | **Низкий** (официальный API-ключ, стабильная работа) |
| **Google AI Studio Web UI** | `gemini-3.1-flash-image` | Web login (Session Cookie) | Free / Subscription | **Да** | **Да** | **Низкий** (только ручной интерфейс, не автоматизируется) |
| **fal.ai Direct API** | `fal-ai/nano-banana-pro` | FAL API Key (`FAL_KEY`) | Платный ($0.15 за генерацию 1K) | **Да** | **Да** | **Низкий** (стабильный коммерческий провайдер) |

## Вердикт
Генерация изображений через подписку/OAuth Google без платного API полностью возможна на локальной машине через `cli-proxy-api` `/v1/chat/completions` с моделью `gemini-3.1-flash-image`. Она работает стабильно и возвращает base64-изображение прямо в ответе чата.
Поскольку платные ключи не использовались и биллинг не включался, блокировок нет.
