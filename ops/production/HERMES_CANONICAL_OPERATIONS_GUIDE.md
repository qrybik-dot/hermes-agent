# Hermes: каноническая схема, обновления и материалы

Статус документа: canonical operations index

Актуальность снимка: 2026-08-20

Владелец: пользователь; исполнители: Hermes, GPT и Codex

Этот файл — первая точка входа для любого нового чата или агента. Он объясняет,
где находится production, где хранится наша история, что считается оригинальным
Hermes и как продолжать работу без повторного изобретения архитектуры.

## 1. Три сущности без смешения

### A. Оригинальный Hermes производителя

- Репозиторий: `https://github.com/NousResearch/hermes-agent.git`.
- Имя remote: `upstream`.
- Это vendor truth: новые версии, исправления и native-возможности.
- Мы не добавляем в его CLI собственные команды и не превращаем его core в наш
  workflow engine.
- Перед сохранением любого нашего расширения очередное обновление upstream
  проверяется на native-эквивалент. Если upstream решает задачу достаточно
  хорошо, наше расширение удаляется.

### B. Наша история и интеграция в Git

- Приватный репозиторий: `git@github.com:qrybik-dot/hermes-agent.git`.
- Имя remote: `origin`.
- `origin/prod/hermes-autonomous-core` — точный источник установленного core и
  минимальных принятых production-дельт.
- `origin/prod/hermes-vps` — админ-инструменты и VPS operations-контур.
- В Git разрешены код, обезличенные specs, ADR, manifests и handoff-документы.
- В Git запрещены secrets, OAuth, raw memory, звонки, личное содержимое vault,
  `.env`, cookies и recovery keys.

### C. Production Hermes

- VPS runtime: `/home/hermes/hermes-runtime/current`.
- Быстрый откат: `/home/hermes/hermes-runtime/previous`.
- Истина — immutable release, на который указывают эти ссылки, плюс systemd
  read-back. Legacy checkout не является production.
- Текущий снимок: `current=5aa2dc4d940bb595fbaf18f26edfc24c48b6952f`,
  `previous=17a0262375b4e449f974fc0ba26c72fa247ff4dc`.

## 2. Обновление Hermes

Официальная команда производителя `hermes update` остаётся неизменной. В
обычной установке она делает Git pull, обновляет зависимости и конфигурацию, а
затем перезапускает gateway. Это корректно для обычного checkout, но не для
нашего immutable production.

### Что пользователь говорит Hermes

- **«Проверь обновления Hermes»** — read-only сравнение установленной версии с
  последним стабильным upstream release и краткий список релевантных изменений.
- **«Подготовь обновление Hermes»** — отдельный чистый integration candidate:
  новый upstream tag, перенос только ещё нужных дельт, тесты, smoke и rollback;
  production не переключается.
- **«Обнови production Hermes»** — после готового candidate выполняется штатное
  переключение через managed immutable release manager и read-back.

Это не новые команды внутри Hermes CLI, а operations-workflow вокруг
оригинального продукта. Голые `/update` и `hermes update` в production не
используются, потому что обходят `current/previous`, lease и наши rollback-gates.
В обычной локальной установке производителя они остаются штатными.

### Обязательный sunset-review при каждом обновлении

Для каждого нашего расширения решить одно из трёх:

1. `REPLACE_WITH_NATIVE` — upstream теперь делает это достаточно хорошо;
2. `KEEP_EXTENSION` — функция всё ещё нужна и остаётся паузируемым tool/service;
3. `REMOVE` — функция больше не нужна.

Постоянный core patch допустим только при воспроизводимом upstream-баге,
минимальном diff и явном плане удаления после vendor fix.

### Снимок обновления на 2026-08-20

- Production: Hermes `v0.20.0` плюс 23 узкие custom-коммита; merge-base с
  upstream — `3c27eb6234…`.
- Последний стабильный upstream: `v0.20.4 / v2026.8.18`, tag
  `e624e9fde561e1add9388384012b295fde669ade`.
- Важные изменения: SessionDB contention/event-loop fixes, cron continuity и
  media-send hardening, MCP/Gemini tool-call fixes, remote gateway recovery,
  security advisory scan для skills и честный update при parked branch.
- Решение: обновление полезно, но это diverged integration, не fast-forward.
  Подготовить отдельный candidate на tag `v2026.8.18`; прямой production
  `hermes update` не запускать.

### Текущий gate autonomous core

- Архитектура и граница полномочий приняты: native `AIAgent` остаётся
  владельцем смысла, а gateway больше не имеет `sudo`, Docker или доступа к
  Money Agent.
- Полный статус остаётся `PARTIAL / FINAL_CANDIDATE_ROUTE_NO_GO`: proxy Gemini,
  direct Gemini и Codex сейчас ограничены квотами; proxy Grok не связан с
  provider; direct `agy` слишком тяжёл для VPS 1 GB; NVIDIA не авторизован.
- Production routing не менялся. Следующий gate — один восстановившийся
  primary и максимум один pre-effect fallback, затем полный непрерывный
  `J01-J20` на одном profile. Два чистых окна сами по себе не дают `READY`.
- Текущий CLIProxy использует ровно один isolated Antigravity OAuth; прежняя
  гипотеза о смешении credentials для текущей конфигурации опровергнута.
  Однако его auth-contract обязательно project-bound, а официальный `agy`
  использует отдельную consumer-сессию Google Pro. Официального import/loader
  consumer credential в CLIProxy нет, поэтому ручной token bridge запрещён.
- CLIProxy `7.2.137` новее установленного `7.2.112` и содержит полезные
  Antigravity transport/schema/tool-call исправления, но не закрывает этот
  auth-contract. Обновление допустимо только отдельным sidecar-candidate, не
  прямой заменой production.

## 3. Память и Knowledge

Целевая схема без второго «мозга»:

1. Native `MEMORY.md` / `USER.md` — только короткие критичные факты, всегда в
   контексте.
2. Native `session_search` — штатный SQLite FTS5 по истории диалогов.
3. Canonical Knowledge Markdown — подтверждённая долговременная база знаний.
4. SQLite FTS5 Knowledge — лёгкий производный индекс для быстрого поиска.

Визуализация не требует отдельной graph DB:

- текущий Hermes уже имеет native `journey` / `/journey` для временной карты
  собственных memories и skills;
- Knowledge остаётся Markdown, а связи задаются проверяемыми `[[wikilinks]]` и
  минимальным frontmatter; Obsidian строит локальный offline-граф прямо из них;
- Graphify допустим только как удаляемая on-demand проекция для доказанных
  multi-hop задач, которых не закрывают FTS и Markdown-ссылки.

Knowledge FTS полезен и нужен: текущий `knowledge_search()` читает именно его,
поэтому устаревший индекс означает, что новая сохранённая запись может не
находиться. 2026-08-20 старый сломанный unit заменён versioned extension вне
core: 185/185 документов, inventory/integrity/resource/rollback PASS.

Минимальный repair:

- отдельный versioned extension, не core patch;
- отдельный индекс для `Personal Anton`, `Wife`, `Family Shared`,
  `Hermes System`, `Codex KB`; междоменного fallback нет;
- one-shot rebuild с `concurrency=1`, временной БД, integrity check и атомарной
  заменой;
- `promoter -> FTS -> search read-back`;
- Graphify, embeddings и внешний memory-provider остаются выключенными, пока
  реальные evals не докажут недостаточность FTS.

Не подключать сейчас memU, Graphiti, OpenViking, Hy-Memory, Mem0 или иной
provider в production. Полезные идеи берём без нового runtime: provenance,
`valid_from/valid_to`, `supersedes`, progressive disclosure `summary -> body`.
Внешний provider допускается только в изолированном shadow-пилоте после
измеренного провала FTS/Markdown и не получает право писать canonical Knowledge.

Каноническое решение и критерии:
`reports/Hermes_Memory_Architecture_Decision_2026-08-20.md`.

## 4. Текущие расширения

| Расширение | Статус | Решение |
|---|---|---|
| Granola | `READY_ON_DEMAND` | Старый рабочий transport; разовый sync, timer off |
| Knowledge FTS | `CANDIDATE` | 185/185 и rollback PASS; natural recall пока 5/25, timer off |
| Knowledge publication | `PAUSED / PARTIAL` | После 25 natural cases; Graphify отделить |
| Obsidian/Syncthing | `READY_ON_DEMAND` | VPS send-only sync-window; Mac receive-only/offline; daemon off |
| NotebookLM | `PAUSED / NO-GO` | Нужны auth и RAM qualification |
| Calls | `PAUSED / NO-GO` | Нужны provider и E2E proof |
| Oracle retry | `PAUSED` | Пользователь отложил |
| Money Agent | `SEPARATE T3 / NO-GO` | Новый большой task до commercial loop |

## 4.1 Очистка provider-артефактов

2026-08-20 удалено `1,071,791,375` bytes superseded CLIProxy qualification,
upgrade, inactive auth/config и Antigravity staging artifacts. Сохранены только
current CLIProxy binary/config, один active Antigravity credential, текущий
`agy` и один proxy rollback binary. Сервисы не перезапускались; Gateway,
CLIProxyAPI и Knowledge остались active, failed units — 0. Старые credential
копии намеренно не восстанавливаются.

Receipt:
`reports/Hermes_VPS_Provider_Artifacts_Cleanup_2026-08-20.md`.

## 5. Канонические handoff и отчёты

### На MacBook — полный рабочий набор

Корень: `/Users/skyeng/Documents/oracle`

- Этот индекс: `HERMES_CANONICAL_OPERATIONS_GUIDE.md`.
- Текущий full-DoD handoff:
  `reports/handoff-hermes-autonomous-core-full-dod-20260820.json`.
- Предыдущий narrative core handoff (история кандидата, не текущий verdict):
  `reports/Hermes_Autonomous_Core_Final_Candidate_Handoff_2026-08-20.md`.
- Core report: `reports/Hermes_Autonomous_Core_Final_Report_2026-08-20.html`.
- Extensions handoff:
  `reports/handoff-hermes-extensions-reconciliation-20260820.json`.
- Granola: `reports/Hermes_Granola_Gap_Sync_Result_2026-08-20.md`.
- Legacy obligations: `reports/Hermes_Legacy_Tasks_Reconciliation_2026-08-20.md`.
- Optional readiness:
  `reports/Hermes_Optional_Extensions_Readiness_2026-08-20.md`.
- Memory/FTS candidate result:
  `reports/Hermes_Knowledge_FTS_Candidate_Result_2026-08-20.md`.
- Obsidian offline replica:
  `reports/Hermes_Memory_Obsidian_Replica_Result_2026-08-20.md`.
- Money Agent task packet: `reports/Money_Agent_T3_Launch_Packet_2026-08-20.md`.
- Provider cleanup receipt:
  `reports/Hermes_VPS_Provider_Artifacts_Cleanup_2026-08-20.md`.

GPT с MacBook connector сначала читает этот файл, затем только относящийся к
задаче handoff. Не нужно загружать все старые отчёты в контекст.

### На VPS — operational truth

Корень: `/home/hermes/.hermes/ops/change-control`

- `HERMES_CANONICAL_OPERATIONS_GUIDE.md` — копия этого индекса.
- `handoff-hermes-autonomous-core-full-dod-20260820.json` — текущий core verdict.
- `handoff-hermes-autonomous-core-final-candidate-20260820.json` — предыдущий
  candidate snapshot; не использовать для объявления `READY`.
- `handoff-hermes-extensions-reconciliation-20260820.json` — extensions.
- `production-lease.json`, transition и runtime manifest — динамическое
  change-control состояние; его всегда проверяют заново.

### В Git — долговечная история

- Этот индекс хранится как `ops/production/HERMES_CANONICAL_OPERATIONS_GUIDE.md`
  в operations-ветке `origin/prod/hermes-vps`.
- Ветка `origin/prod/hermes-autonomous-core` не получает документационные
  commits отдельно от release: её HEAD должен оставаться точным source SHA
  production-candidate.
- Git хранит историю решений и кода, но не заменяет live VPS read-back.
- Большие HTML/PNG и персональные данные не дублируются в Git без необходимости.

## 6. Не переносить из старого Hermes автоматически

- legacy checkout, старые DB целиком, просроченные cron и delivery-state;
- вторые routers, workflow engines и общие memory providers;
- временные model/provider patches без воспроизводимого falsifier;
- старые коннекторы, если upstream уже имеет зрелый native-вариант.

Напоминание, ранее ошибочно названное «о номерах», на самом деле было старым
повторяющимся напоминанием **«подкрутить пластину» раз в пять дней** для
ортодонтической пластинки Веры. 2026-08-20 дублирующая legacy-задача удалена;
источник напоминания теперь только существующее событие Google Calendar.

## 7. Продолжение при смене агента или лимите

1. Прочитать этот файл.
2. Проверить live `current`, `previous`, systemd и production lease.
3. Прочитать только один актуальный handoff по нужному контуру.
4. Не считать старые SHA, service state и upstream version актуальными без
   read-back.
5. Делать один extension за раз; core обновлять отдельным candidate.
6. Money Agent выполнять отдельной T3-задачей по его launch packet.
