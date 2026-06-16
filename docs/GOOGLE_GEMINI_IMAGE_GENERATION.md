# Google Gemini / Nano Banana Image Generation

## Overview

**Nano Banana 2** (техническое имя модели: `gemini-3.1-flash-image`) — это новейшая модель генерации и редактирования изображений от Google, встроенная в семейство Gemini 3.1. Она оптимизирована для скорости, точного следования промпту и нативной поддержки изменения/редактирования изображений в рамках диалогового контекста (multimodal conversation history).

В отличие от традиционных моделей (например, DALL-E 3), которые работают через выделенные эндпоинты генерации, `gemini-3.1-flash-image` в официальном Google GenAI SDK вызывается через стандартный метод генерации контента `client.models.generate_content`, возвращая сгенерированное изображение прямо в составе ответа в виде байтов (`inline_data`).

---

## Сравнительная таблица путей генерации (Capability Matrix)

| Путь (Route) | Модель (Model) | Метод авторизации (Auth) | Стоимость (Cost Tier) | Генерация (Text-to-Image) | Редактирование (Image Edit) | Риски стабильности (Stability Risk) |
| :--- | :--- | :--- | :--- | :---: | :---: | :--- |
| **Local Proxy (`cli-proxy-api`)**<br>`/v1/chat/completions` | `gemini-3.1-flash-image` | Google Account OAuth (через Antigravity) | Free / Subscription (лимиты аккаунта Google) | **Да** | **Да** (через передачу картинки в messages) | **Средний** (требует активного OAuth-токена, при истечении сессии нужен перелогин) |
| **Google AI Studio API**<br>`/v1/models/...:generateContent` | `gemini-3.1-flash-image` | API Key (`GEMINI_API_KEY`) | Free Tier / Pay-as-you-go | **Да** | **Да** | **Низкий** (официальный API-ключ, стабильная работа) |
| **Google AI Studio Web UI** | `gemini-3.1-flash-image` | Web login (Session Cookie) | Free / Subscription | **Да** | **Да** | **Низкий** (только ручной интерфейс, не автоматизируется) |
| **fal.ai Direct API** | `fal-ai/nano-banana-pro` | FAL API Key (`FAL_KEY`) | Платный ($0.15 за генерацию 1K) | **Да** | **Да** | **Низкий** (стабильный коммерческий провайдер) |

---

## Особенности локального эндпоинта (`cli-proxy-api`)

При обращении к локальному API-прокси `127.0.0.1:8317` обнаружены следующие особенности интеграции:
1. Вызов стандартного эндпоинта `/v1/images/generations` с моделью `gemini-3.1-flash-image` возвращает ошибку `400 Bad Request`:
   > `Model gemini-3.1-flash-image is not supported on /v1/images/generations or /v1/images/edits. Use gpt-image-2, grok-imagine-image, grok-imagine-image-quality, or a configured openai-compatibility image model.`
2. Корректный способ генерации через прокси — отправка POST-запроса на эндпоинт `/v1/chat/completions`. Прокси перехватывает этот запрос, обращается к Gemini API и возвращает изображение в специальном формате:
   - Поле `content` ассистента имеет значение `null`.
   - Ответ содержит массив `images` с объектом `image_url`, содержащим base64-строку изображения (с префиксом `data:image/jpeg;base64,...`).

### Пример структуры ответа `/v1/chat/completions`
```json
{
  "id": "xmsxaqGcJbO1kdUP6KeAoAU",
  "object": "chat.completion",
  "created": 0,
  "model": "gemini-3.1-flash-image",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": null,
        "reasoning_content": null,
        "tool_calls": null,
        "images": [
          {
            "type": "image_url",
            "image_url": {
              "url": "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQE..."
            }
          }
        ]
      }
    }
  ],
  "usage": { ... }
}
```

---

## Тестирование (Smoke Test)

Успешно выполнен Smoke-тест локального пути через `cli-proxy-api` / Antigravity Google OAuth без использования платных API-ключей.

- **Модель**: `gemini-3.1-flash-image` (Nano Banana 2)
- **Промпт**: `A small bronze Hermes messenger figurine on a clean white studio background`
- **Затраченное время (Latency)**: ~12.85 секунд
- **Формат файла**: JPEG (`image/jpeg`)
- **Размер**: 449,443 байт (~439 КБ)
- **Разрешение**: 1408x768
- **Локальный путь**: `.tasks/2026-06-16_google_image_probe/outputs/google_gemini_image_probe.jpg`
- **Статус проверки**: Валидный JPEG, открывается через PIL.
