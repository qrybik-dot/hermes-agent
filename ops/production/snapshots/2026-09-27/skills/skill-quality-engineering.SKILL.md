---
name: skill-quality-engineering
description: "Audit, evaluate, and test skills for quality and safety."
version: 1.0.0
author: Anton V, Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [skills, quality, eval, testing, governance, edd]
    related_skills: [hermes-agent-skill-authoring, requesting-code-review]
---

# Skill Quality Engineering

Инженерный стандарт тестирования, валидации и регрессионного контроля навыков Hermes.

## When to Use
- Проектирование нового навыка перед добавлением в библиотеку
- Добавление тестов и валидация траектории инструментов для существующего навыка
- Аудит коллизий триггеров в описаниях навыков
- Расследование сбоя навыка и создание регрессионной фикстуры
- **Don't use for:** форматирования frontmatter (см. `hermes-agent-skill-authoring`) или ревью прикладного кода.

## 1. Decision Matrix
Перед созданием навыка исключите альтернативы:
- Статичные факты/доки → MCP Knowledge.
- Правила проекта → `AGENTS.md`.
- Новое API/интерфейс → Tool/MCP.
- Процедура по шагам → **SKILL.md**.

## 2. Capability Tiers & Regression Policy
| Уровень | Допустимые действия | Траектория | Gate |
|---|---|---|---|
| `READ` | Чтение, поиск, парсинг | `ANY_ORDER` | outcome + off-target regression |
| `DRAFT` | Генерация файлов/шаблонов без применения | `IN_ORDER` | outcome + process + off-target |
| `ACT-REVERSIBLE` | Обратимые локальные правки (файлы, git ветки) | `IN_ORDER` | outcome + process + trust/safety + read-back |
| `ACT-CRITICAL` | Deploy в прод, delete, security/права, транзакции | `EXACT` для мутаций | deterministic zero-tolerance checks + rollback evidence |

Не используйте фиксированный процент как единственный критерий качества. Сравнивайте incumbent/baseline с candidate на одинаковых кейсах; tie/noise сохраняет incumbent.

## 3. Proof Obligation для уровней ACT
Любое действие уровня `ACT` обязано содержать:
1. **Target Identity:** Явное определение изменяемого объекта (путь к файлу, ID сущности, ветка, хэш).
2. **Approval Boundary:** Не требовать повторного подтверждения, если действие явно разрешено исходным запросом и остаётся в его scope. Запрашивать отдельный approval ТОЛЬКО если:
   - действие выходит за пределы исходного scope;
   - возникает новое существенно более рискованное или необратимое действие;
   - действие прямо требует подтверждения по существующим production/policy правилам (уровень `ACT-CRITICAL`).
3. **Read-Back:** Обязательная проверка целевого объекта после записи/мутации (статус, хэш, diff).
4. **Rollback:** План отката изменений (git reset/stash, бэкап) или явное предупреждение о необратимости.

## 4. Evaluation-Driven Development (EDD)
До promotion candidate создайте минимальный реальный eval-set. Для обычного patch — 5–8 кейсов; для critical — 8–12. Обязательные типы:
1. **Historical/positive:** реальная задача или известный failure, который candidate должен исправить.
2. **Boundary/paraphrase:** та же цель другой формулировкой.
3. **Negative/off-target:** похожая задача, которую skill не должен перехватывать или ломать.
4. **Regression:** старый успешный сценарий incumbent.

Проверяйте три слоя:
- **Outcome:** объективный конечный результат.
- **Process:** корректные tools/routing/order, отсутствие бесполезных циклов и лишних ошибок.
- **Trust & Safety:** grounding/provenance/read-back, отсутствие invented IDs и unintended mutations.

Правила:
- deterministic/mechanical oracle первичен; LLM judge — только для субъективных аспектов;
- baseline и candidate получают одинаковый input/model/config;
- disputed/noisy cases повторяются, весь suite без причины не размножается;
- candidate не может менять runner, golden cases, acceptance policy или собственный eval result;
- при FAIL исправляет candidate сам автор/агент, затем eval повторяется.

## 5. Trigger Routing Rule
- Первые 57 символов поля `description` обязаны однозначно называть действие и объект (рантайм обрезает индекс на 57 символе).
- Запрещены пересекающиеся префиксы с соседними навыками в категории.
- Перед созданием нового skill проверьте соседние skills/processes/evals на функциональный дубль; если capability уже существует, расширяйте существующую сущность вместо создания параллельной.

## 6. Controlled Self-Improvement Boundary — Protected Control Plane
Для self-improvement Hermes этот skill задаёт правила качества, но **не является PASS/FAIL runner**. Единственный promotion gate живёт во внешнем Improvement Eval Lab.
- Hermes может создавать/патчить candidate skills и исправлять их после FAIL.
- Hermes не меняет core/runtime, secrets, deployment/rollback infrastructure, Knowledge originals/provenance, eval runner/golden cases/acceptance criteria.
- Production promotion требует staged candidate + eval PASS + независимый review.
- Не создавайте отдельный daemon/cron только ради learning loop; предпочитайте event-driven Kanban.
