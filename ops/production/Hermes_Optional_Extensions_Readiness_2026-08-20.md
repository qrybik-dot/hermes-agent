# Hermes optional extensions — readiness и остаток исходного плана

Дата: 2026-08-20  
Core baseline: `5aa2dc4d940bb595fbaf18f26edfc24c48b6952f`  
Статус core: **FINAL_CANDIDATE; расширения отдельно**

## Короткий ответ про FTS publication

FTS publication — фоновая цепочка, которая продвигает подтверждённые Knowledge
записи в каноническое хранилище и перестраивает полнотекстовый индекс; при
разрешении дальше обновляется Graphify. Прямые Knowledge read/write работают
без неё. Это «быстрый поиск по всей базе», а не сама база знаний.

## Readiness необязательных контуров

| Контур | Сейчас | Готовность «включить по команде» | Решение |
|---|---|---:|---|
| Granola | Dedicated optional profile и прежний рабочий OAuth восстановлены; last-30-days reconciled; timer off | READY через change-control; self-service PARTIAL | One-shot технически готов; узкой команды Hermes для старта ещё нет |
| NotebookLM | Бинарник есть, auth-check не проходит; cold start тяжёлый | NO-GO | Оставить off; отдельный re-auth/RAM smoke позже |
| Calls | Units/scripts есть, но старый env, нет credential/E2E proof; лимит памяти слишком велик для VPS | NO-GO | Оставить off; квалифицировать provider и снизить ресурсный риск |
| FTS | Versioned extension опубликован; 185/185, rollback и resource gate PASS; естественных кейсов 5/25 | CANDIDATE | Текущий индекс рабочий; timer off до natural recall gate |
| Knowledge publication | Последовательно вызывает promoter → FTS → Graphify | PARTIAL / OFF | Отделить Graphify; включать только promoter → FTS → read-back после 25 natural cases |
| Oracle retry | Unit существует и disabled | PAUSED пользователем | Не трогать |
| Money Agent | Units off; коммерческий цикл не доказан | NO-GO | Отдельная T3-задача, последним |

## Почему нельзя просто дать Hermes команду «включи»

Файл навыка или allowlist не делает сломанный сервис готовым. Для честного
on-demand управления нужны одновременно:

- отдельный extension manifest: owner, dependency, config, resource budget;
- typed `status / prepare / enable / disable / smoke / rollback`;
- проверка auth и preconditions до старта;
- read-back пользовательского результата, а не только `systemd active`;
- безопасное продолжение после human gate;
- один понятный ответ Hermes без ложного READY.

Универсальный control-skill имеет смысл делать только после готовности каждого
extension. Иначе он маскирует неисправности красивой командой.

После удаления ambient `sudo` native Hermes не может сам запускать systemd-unit.
Существующий Admin MCP не разрешает Granola/FTS и предоставляет только
подтверждённый restart allowlisted unit. Это правильная security-граница, но
означает, что формулировка «включи Granola» пока требует change-control, а не
является готовой one-phrase self-service функцией. Возвращать общий root нельзя.
После core gate нужен один узкий typed action только для Granola one-shot с
preflight, status, result read-back и timeout; FTS получает аналогичный action
только после natural recall gate. Для NotebookLM, calls, Oracle и Money Agent
такую кнопку сейчас добавлять нельзя: она скрыла бы их реальные блокеры.

## Что исходный план недооценил

1. **Миграцию пользовательских обязательств.** Core был отделён правильно, но
   scheduler/task registry не получил отдельного reconciliation gate.
2. **Auth lifecycle коннекторов.** Наличие конфигурации было ошибочно близко к
   понятию readiness; Granola доказала, что profile и token owner должны быть
   частью одного контракта.
3. **Capability readiness.** «Файлы и unit существуют» недостаточно: calls,
   NotebookLM и FTS имеют реальные блокеры.
4. **Версионирование расширений.** Core получил immutable release, а часть
   дополнений осталась mutable/legacy и без `current/previous`.
5. **Продуктовый результат Money Agent.** Циклы и bid считались прогрессом без
   acceptance/payout state machine.

## Оценка внедрения

Архитектурное решение теперь правильное: native Hermes — самостоятельный core,
дополнения — независимые и паузируемые инструменты. Главный успех — прекращено
смешение core, моделей, памяти и тяжёлых фоновых процессов.

Внедрение ещё не полностью закрыто на пользовательском уровне, потому что
необязательные контуры и старые обязательства не прошли тот же строгий
release/read-back/rollback contract. Это не повод менять core снова; это очередь
узких extension-задач.

## Каноническая последовательность

1. Завершить одноразовый Granola gap import и оставить timer off.
2. Перенести только подтверждённые будущие напоминания.
3. Довести FTS natural recall с 5 до 25 кейсов; затем включить только
   promoter → FTS → read-back без Graphify.
4. Квалифицировать calls; NotebookLM включать только on-demand при RAM gate.
5. Oracle оставить paused.
6. Money Agent — последним по T3 launch packet.

Каждый шаг меняет один контур, имеет snapshot, falsifier, smoke, read-back и
rollback. Возврата к общему «включить всё» не требуется.
