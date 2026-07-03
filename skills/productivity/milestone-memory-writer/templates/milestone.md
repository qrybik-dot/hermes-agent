---
owner: anton
scope: personal
project: PROJECT_ID
status: completed
date: YYYY-MM-DD
graph: true
tags: [PROJECT_TAG, milestone, TOPIC_TAG]
source_commit: null
source_branch: null
live_acceptance: false
---

# PROJECT milestone — YYYY-MM-DD — TOPIC

## Итог

Одним абзацем: какое новое устойчивое состояние достигнуто.

## Что изменилось

- Было: прежнее ограничение или ненадёжное поведение
- Стало: новое проверенное поведение
- Для пользователя: практический эффект

## Почему важно

Коротко: где это поможет в следующих задачах и какие ошибки предотвращает.

## Evidence

- Git: commit, branch, remote divergence
- Checks: tests, compile, lint, validation
- Runtime: service state после deployment
- Delivery: подтверждение внешней отправки, если применимо
- Live acceptance: реальный пользовательский сценарий или false

## Закреплённые решения

- Только решения и правила, явно принятые пользователем

## Риски или ограничения

- Только актуальный остаточный риск; удалить раздел, если рисков нет

## Связи

- [[PROJECT_PAGE]]
- [[SYSTEM_PAGE]]
- [[DECISIONS_PAGE]]
