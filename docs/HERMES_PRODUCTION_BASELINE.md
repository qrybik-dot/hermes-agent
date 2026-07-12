# Hermes Production Baseline

Статус: canonical production reference
Дата фиксации: 2026-06-27
Ветка production: `prod/hermes-vps`

## Цель

Этот документ фиксирует рабочее поведение Hermes после оптимизации скорости, маршрутизации моделей, продолжения задач и Telegram-отчётности. Изменения upstream или локальные патчи не должны ухудшать эти свойства без явного решения и обновления regression-тестов.

## 1. Скорость

Проверенные ориентиры, не жёсткий SLA:

- простой cold-запрос после restart: около 10-15 секунд;
- простой warm-запрос при cache hit: около 2-5 секунд;
- простой запрос: один LLM-вызов, без инструментов;
- deterministic/no_llm-команды не должны вызывать модель;
- `clarify` не должен блокировать turn дольше 15 секунд;
- обычный лимит agent loop: 12 итераций;
- повтор пустого ответа для `simple + no_mcp`: максимум один.

Проверенный результат 2026-06-26:

- cold simple: 11.947 сек;
- warm simple: 2.214 сек;
- ещё один ordinary simple: 4.285 сек;
- routed prompt: около 6.4 КБ вместо 44.1 КБ;
- schema simple toolset: 49 796 -> 1 286 байт.

## 2. Маршрутизация моделей

| Роль | Провайдер | Модель |
|---|---|---|
| simple | custom | gemini-3.5-flash-low |
| parser | custom | gemini-3.5-flash-low |
| research | custom | gemini-3.1-pro-low |
| planning | openai-codex | gpt-5.6-sol |
| coding | openai-codex | gpt-5.6-terra |
| long_context | custom | gemini-3.1-pro-low |
| server_debug | openai-codex | gpt-5.6-sol |
| no_llm | gateway | deterministic |

Правила:

- маршрутизация детерминированная и не требует отдельного LLM-классификатора;
- сложные quality-locked роли не должны молча переходить в бесплатную модель;
- метрики обязаны различать выбранную и фактическую модель;
- fallback фиксируется только при фактическом использовании;
- Git/VPS/systemd аудит получает `server_debug + terminal`.
- `coding` использует `gpt-5.6-terra`; `planning` и `server_debug` используют `gpt-5.6-sol`;
- `gpt-5.5` не участвует в активном роутинге и сохраняется только как ручной rollback-кандидат;
- делегирование остаётся последовательным и плоским: один дочерний агент, без вложенного orchestrator и без автоодобрения;
- в production запрещены неприбитые алиасы `gpt-5.6`, `latest` и `auto-latest`.

## 3. Продолжение задач

- состояние хранится в `state.db`, таблица `gateway_tasks`;
- задача сохраняет исходную цель, роль, разрешённые и обязательные инструменты, статус и Telegram message id;
- состояние переживает session expiry, `/reset` и gateway restart;
- `Готов продолжить`, `Продолжить N`, `Дальше` и аналогичные команды обрабатываются gateway до LLM;
- reply-контекст не должен превращать continuation-команду в новую задачу;
- Telegram reply-контекст передаётся в task runtime структурно: текущий текст, message/update id, chat/user id, replied-to text/caption/message/sender id;
- для календарных reply-команд gateway использует приоритет: текущий текст -> reply text/caption -> pending task того же chat/user;
- при нескольких задачах показывается deterministic-выбор;
- при отсутствии незавершённых задач возвращается `Незавершённых задач нет` без LLM;
- завершённая задача не возвращается в active choice.

## 4. Контракт фактического результата

`SUCCESS` разрешён только при подтверждённом результате.

Обязательные правила:

- execution-задача без tool calls -> `INCOMPLETE`;
- progress-only без результата -> `INCOMPLETE`;
- `max_iterations_reached` -> `INCOMPLETE`, даже если модель сформировала убедительный итог;
- отсутствие обязательного инструмента -> `BLOCKED` до LLM-вызова;
- слова `готово к записи`, `внутренний буфер`, `при следующем цикле` не являются подтверждением выполненного действия;
- календарное событие считается созданным только после подтверждения write-операции или последующей read-only проверки;
- календарные команды из Telegram reply поддерживают русские даты вида `1 июля 2026`, `1 июля`, `завтра` и числовые форматы;
- служебные строки вроде `Записал в формате события` и `Часовой пояс ... не указан` не должны попадать в summary события;
- повтор одного Telegram update/message не должен создавать вторую tracked calendar-задачу;
- технический отчёт опирается только на Git, systemd, логи и фактические результаты тестов.

## 5. Эталонный положительный сценарий

Read-only Git-аудит 2026-06-27:

- continuation восстановил исходную задачу;
- роль: `server_debug`;
- модель: `openai-codex/gpt-5.5`;
- tools: 5 фактических вызовов;
- результат: ветка, dirty state, remotes, ahead/behind, commits и systemd timer;
- изменений не внесено;
- production совпал с `origin/prod/hermes-vps`;
- итог отправлен пользователю на русском языке.

Этот сценарий является golden acceptance для задач диагностики VPS и Git.

## 6. Telegram UX и отчётность

### Live-status

Для задачи с 3+ этапами gateway создаёт одно сообщение и обновляет его через edit:

```text
⏳ В работе: <задача>
[████░░░░░░] 40%
Этап: <этап>
Идёт: <реальное время>
```

Правила:

- модель не печатает собственный progress bar;
- tool trace и внутренние рассуждения не отправляются;
- status update не создаёт поток отдельных сообщений;
- после restart используется сохранённый status message id;
- progress не является финальным результатом.

### Финальный Telegram-отчёт

Короткий plain text:

1. итог;
2. что сделано;
3. что проверено;
4. реальные риски;
5. следующий безопасный шаг в рамках задачи.

### HTML

HTML обязателен для больших production-изменений, аудитов, инцидентов и архитектурных работ. В Telegram отправляется сам `.html` файл и короткая выжимка, но не HTML-код.

## 7. Логи и метрики

INFO содержит одну компактную строку на запрос:

- status;
- role;
- resolved model/provider;
- cache hit/miss;
- LLM/tool call count;
- total/LLM/tool/overhead time;
- fallback только при наличии;
- task_id только для tracked task.

DEBUG содержит routing reason, selected toolsets, prepare/init/post и узкие технические счётчики.

Lifecycle tracked task в INFO: `created`, `paused`, `resumed`, `completed`, `blocked`, `delivery_failed`. Текст личной задачи в lifecycle-лог не записывается.

## 8. Regression gate

Перед production restart обязательны:

- `py_compile` изменённых Python-файлов;
- Ruff `F821,F823`;
- `git diff --check`;
- targeted pytest: router, cache, continuation, Telegram delivery, empty-response recovery, session-search budget;
- config validation;
- один controlled restart;
- `ActiveState=active`, `SubState=running`, `NRestarts=0`;
- clean Git worktree и push в `origin/prod/hermes-vps`.

## 9. Что не является эталоном

- success только по числу tool calls;
- утверждение, что запись создана, если она лишь подготовлена;
- молчаливый fallback на StepFun/GLM;
- 10+ одинаковых session_search;
- длинный внутренний монолог в Telegram;
- несколько progress-сообщений для одной задачи;
- отчёт без фактического diff, логов или тестов.
