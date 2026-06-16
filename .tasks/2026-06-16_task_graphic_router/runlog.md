# Runlog: Task & Graphic Router Simulation (2026-06-16)

## Статус
- **Дата проведения**: 2026-06-16
- **Версия**: 2.0 (После dry-run аудита и доработок)
- **Режим**: Dry-run (Safe-write, без изменения продакшн runtime-поведения)
- **Цель**: Проверка обновленных правил классификации 15 тестовых задач на маршрутизаторе задач и 9 графических задач на обновленном маршрутизаторе.

---

## 1. Результаты классификации задач (Task Intake Router v2)

| ID | Описание | Размер (`task_size`) | Домен (`task_domain`) | Риск (`risk`) | Политика подтверждения (`ask_policy`) | Режим исполнения (`execution_mode`) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **T1** | Статус докер контейнеров | Simple | system | low | `do_not_ask` | Direct |
| **T2** | Добавить новую переменную окружения DB_PASSWORD в защищенный файл .env и перезапустить службу hermes | Risky | system | high | `ask_before_risky_step` | Safe-Write & Rollback |
| **T3** | Оптимизировать производительность ядра run_agent.py, сократив потребление токенов при длинных сессиях | Large | code | medium | `ask_before_write` | Plan-First |
| **T4** | Исправить опечатку в файле README.md | Simple | content | low | `do_not_ask` | Direct |
| **T5** | SMS-провайдер с установкой twilio | Normal | code | medium | `ask_before_write` | Step-by-Step (needs_web & needs_context7) |
| **T6** | Еженедельный дайджест из Kanban | Normal | content | low | `ask_once` | Step-by-Step |
| **T7** | Удаление старых логов SQLite сессий | Risky | system | high | `ask_before_risky_step` | Safe-Write & Rollback |
| **T8** | Поиск pydantic-v2 документации и апдейт | Normal | code | low | `ask_before_write` | Step-by-Step (needs_web & needs_context7) |
| **T9** | Скрипт бэкапа ~/.hermes в крон | Risky | system | high | `ask_before_risky_step` | Safe-Write & Rollback |
| **T10**| Рефакторинг модуля gateway/ на микросервисы | Large | code | medium | `ask_before_write` | Plan-First |
| **T11**| Проверить порт 8317 на localhost | Simple | system | low | `do_not_ask` | Direct |
| **T12**| Переустановка python-окружения в venv | Risky | system | high | `ask_before_risky_step` | Safe-Write & Rollback |
| **T13**| Обнови requirements.txt и поставь пакет | Normal | code | medium | `ask_before_write` | Step-by-Step (Dependency change) |
| **T14**| Сделай красиво | Large | code | medium | `ask_before_write` | Plan-First (Ambiguity: high) |
| **T15**| Проверить systemd статус без изменений | Simple | system | low | `do_not_ask` | Direct |

---

## 2. Результаты классификации графических задач (Graphic Generation Router v2)

| ID | Описание | Инструмент (`target_tool`) | Обоснование |
| :--- | :--- | :--- | :--- |
| **G1** | Логотип Telegram-бота киберпанк | `gpt_image` | Художественное изображение по текстовому описанию |
| **G2** | Схема взаимодействия компонентов | `mermaid` | Диаграмма связей / логическая схема (точный текст/схема) |
| **G3** | Подготовить презентацию со слайдами о результатах работы бота за неделю | `presentation_chain` | Цепочка создания презентаций: research brief -> outline -> HTML/PPTX -> visual elements |
| **G4** | Робот-помощник с книгой | `gpt_image` | Художественное изображение по текстовому описанию |
| **G5** | Создать интерактивный дашборд со статистикой вызовов API | `html_svg` | Интерактивный UI-компонент / дашборд на HTML/SVG (точный текст/данные) |
| **G6** | Абстрактный темный фон для слайда презентации | `gemini_image` | Абстрактное изображение / иконка / текстура |
| **G7** | Сделай баннер с русским текстом | `html_svg` | Баннер с точным текстом: Mermaid/SVG/HTML предпочтительнее для точного текста |
| **G8** | Отредактируй существующую картинку по референсу | `gemini_image` | Использование Nano Banana / Gemini Image для редактирования (editing), reference-based generation или style series |
| **G9** | Сделай серию из 5 картинок с одним персонажем | `gemini_image` | Использование Nano Banana / Gemini Image для редактирования (editing), reference-based generation или style series |

---

## 3. Выводы по результатам симуляции v2
1. **Разделение полей**: Параметры `needs_web` и `needs_context7` теперь являются независимыми boolean-полями, что упрощает их обработку парсерами.
2. **Безопасность зависимостей**: Любое изменение в зависимостях (например, T13: `requirements.txt`) теперь корректно детектируется как риск `medium` с необходимостью подтверждения записи (`ask_before_write`).
3. **Безопасность VPS**: Любые задачи, связанные с systemd (даже read-only проверка статуса T15), успешно анализируются на предмет рисков. T15 классифицирован как `Simple` с низким риском, разрешающим автономию, в то время как модификации (T2, T7, T9, T12) классифицируются как `Risky` и жестко требуют политики `ask_before_risky_step` с обязательным подтверждением каждого действия.
4. **Текст на картинках**: Задача G7 (русский текст на баннере) успешно маршрутизирована на HTML/SVG, исключая искажения текста диффузионными моделями.
5. **Последовательность в изображениях**: Задачи G8 и G9 (редактирование, сохранение персонажа в серии) направлены на Nano Banana / Gemini Image как на более гибкий инструмент для работы с референсами.
