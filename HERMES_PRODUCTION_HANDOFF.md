# Hermes Production Handoff

Этот файл — короткая передача контекста для Codex, GPT и Hermes при работе с production Антона.
Он не заменяет live-проверку VPS: изменчивые факты всегда подтверждать через Hermes VPS Admin v3.

## Простая целевая схема

```text
официальный NousResearch/hermes-agent
        ↓ плановое обновление
наш fork qrybik-dot/hermes-agent + минимум необходимых правок
        ↓ тесты и managed_deploy
immutable production release на VPS
```

- Официальный Hermes остаётся основной кодовой базой.
- Наш fork хранит точный код production и минимальный overlay.
- Production не является mutable Git checkout и не обновляется через `git pull` внутри active release.
- Calls, Knowledge, Granola, backups и другие автономные контуры по возможности остаются вне Hermes core.

## Production truth на момент этой передачи

- Active runtime: `500831638d1e590bf84eef6bd098524b51453baf`
- Previous runtime: `5fa896bf80e8ee2caba885b6608e8f75162fd9d2`
- Declared Hermes version: `0.18.0`
- Transition state: `stable`
- Runtime path: `/home/hermes/hermes-runtime/current`
- Canonical change-control: `/opt/hermes-change-control`
- Canonical production branch: `prod/hermes-vps`

После выполнения этой передачи ветка `prod/hermes-vps` должна указывать на тот же commit, что и active runtime.
При расхождении верить symlink `current`, runtime manifest, systemd и canonical transition state, а не старому checkout или переписке.

## Нужные локальные изменения в текущем production

Текущий overlay поверх линии Hermes 0.18.0 включает, среди прочего:

- `0cc92a74…` — execution recovery вместо шаблонной блокировки;
- `9579323f…` — корректный gateway timeout из systemd;
- `5fa896bf…` — fallback для простых запросов и немедленное уведомление о переключении;
- `50083163…` — обязательный tool evidence для утверждений о файлах, звонках, Knowledge и live-состоянии; пустой финал не получает READY.

Это не означает, что весь старый overlay нужно переносить в следующие версии. При обновлении каждое изменение заново классифицировать:

1. уже есть в upstream — удалить локальный патч;
2. можно вынести в plugin/skill/config/external service — вынести из core;
3. всё ещё нужен core patch — переносить минимально и с regression test.

## Правила планового обновления

1. Определить актуальный стабильный upstream на момент работы.
2. Создать clean candidate от upstream, а не продолжать бесконечно старую production-ветку.
3. Перенести только подтверждённо нужный минимальный overlay.
4. Проверить Telegram, routing/fallback, execution/read-back, calls, Knowledge и Granola.
5. Развернуть только через canonical lease и `managed_deploy`.
6. При обязательном smoke failure выполнить rollback.
7. После успешного deploy обновить `prod/hermes-vps` до точного active runtime SHA и актуализировать этот файл.

Срочный hotfix можно строить от текущего production, но затем его нужно forward-port в следующий upstream candidate либо удалить, если upstream уже исправил проблему.

## GitHub и публикация

- `prod/hermes-vps` — точное состояние работающего production, а не экспериментальная ветка.
- Candidate и локальный commit сами по себе не публикуются.
- Push выполняется только по явному разрешению пользователя.
- Не хранить в репозитории секреты, токены, `.env`, приватные данные звонков или Knowledge.
- Handoff содержит архитектуру и проверяемые технические факты, но не пользовательские данные.

## `/update` и `/version`

- `/version` должен показывать реальную базовую версию Hermes и runtime commit.
- `/update` для нашего VPS не должен делать `git pull` внутри immutable release.
- Целевое поведение `/update`: собрать clean upstream candidate, применить минимальный overlay, протестировать и выполнить managed deploy или оставить production без изменений.
- Пользовательский UX остаётся простым: одна команда `/update`, без отдельной сложной панели управления.

До реализации managed update стандартный `/update` считается неподходящим для production VPS.

## Recovery

- Перед записью: fresh `shared_preflight`, transition check и один writer lease.
- Перед изменениями: backup и clean candidate от текущей production truth или актуального upstream — в зависимости от типа задачи.
- Active immutable runtime не редактировать.
- Deploy/rollback/transition/recovery — только typed change-control tools.
- Lease освобождать только после smoke/read-back и безопасного stable transition.

## Что проверить в начале следующей сессии

```text
1. shared_preflight(actor="codex" или "gpt")
2. lease_status
3. release_transition_status
4. readlink -f /home/hermes/hermes-runtime/current
5. active release version + .hermes-release.json
6. git ls-remote origin refs/heads/prod/hermes-vps
```

Если active runtime и GitHub branch расходятся, не продолжать плановые изменения, пока не установлена причина и каноническая линия.

## Ближайшая продуктовая задача

Собрать актуальный официальный Hermes как чистую основу, перенести только нужные исправления, проверить внешние контуры и заменить текущий production через managed deploy. Цель — основной Hermes с минимальной кастомизацией, а не дальнейшее наращивание старой ветки 0.18.0.
