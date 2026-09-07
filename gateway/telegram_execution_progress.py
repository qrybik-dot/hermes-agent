"""Truthful Telegram projection of native todo, tool, and commentary events."""

import json
import time


class ExecutionProgress:
    """Small presentation-only state machine for one editable progress bubble."""

    _TEXT_LIMIT = 220

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.started = clock()
        self.plan = None
        self.current = "Обрабатываю задачу"
        self.finding = None
        self.finding_label = "Найдено"
        self.next = None
        self.seen = False
        self._last_tool_event = None

    @classmethod
    def _short_text(cls, value):
        text = " ".join(str(value or "").split()).strip(" -*•\t")
        if len(text) > cls._TEXT_LIMIT:
            text = text[: cls._TEXT_LIMIT - 1].rstrip() + "…"
        return text

    @classmethod
    def _scope_from_text(cls, value):
        """Classify technical identifiers into a small user-facing domain."""
        text = cls._short_text(value).casefold()
        if not text:
            return ""
        if any(marker in text for marker in (
            "hermes-call-processing", "call_processing", "call-processing",
            "call-record", "call_record", "transcript",
        )):
            return "calls"
        if any(marker in text for marker in (
            "/srv/hermes-memory/vault", "knowledge", "knowledge_mcp",
        )):
            return "knowledge"
        if any(marker in text for marker in (
            "kanban", "production-lease", "release-transition",
        )):
            return "kanban"
        if any(marker in text for marker in (
            "hermes-gateway", "systemctl", "journalctl",
        )):
            return "service"
        if text.startswith("git ") or "/.git" in text or " git " in text:
            return "code"
        return ""

    @classmethod
    def _tool_stage(cls, name, preview=None, args=None):
        """Return a human status; never expose raw commands/paths as progress."""
        args = args if isinstance(args, dict) else {}

        def _arg(*keys):
            for key in keys:
                value = args.get(key)
                if isinstance(value, str) and value.strip():
                    return cls._short_text(value)
            return ""

        def _check_scope(scope):
            return {
                "calls": "Проверяю сохранённые звонки",
                "knowledge": "Проверяю базу знаний",
                "kanban": "Проверяю фоновые задачи",
                "service": "Проверяю состояние Hermes",
                "code": "Проверяю изменения в коде",
            }.get(scope)

        if name == "terminal":
            command = _arg("command", "cmd") or cls._short_text(preview)
            scoped = _check_scope(cls._scope_from_text(command))
            if scoped:
                return scoped
            lowered = command.casefold().lstrip()
            if lowered.startswith(("find ", "ls ", "grep ", "rg ", "sqlite3 ", "python ", "python3 ")):
                return "Проверяю нужные данные"
            if lowered.startswith(("curl ", "wget ")) or "http://" in lowered or "https://" in lowered:
                return "Проверяю внешний сервис"
            return "Проверяю текущий этап"

        if name == "web_search":
            value = _arg("query", "q")
            return f"Ищу информацию: {value}" if value else "Ищу нужную информацию"
        if name == "web_extract":
            return "Проверяю найденный источник"
        if name == "read_file":
            scope = cls._scope_from_text(_arg("path", "file_path"))
            return _check_scope(scope) or "Проверяю нужные данные"
        if name in {"write_file", "patch", "edit_file"}:
            scope = cls._scope_from_text(_arg("path", "file_path"))
            return {
                "calls": "Обновляю данные сохранённых звонков",
                "knowledge": "Обновляю базу знаний",
                "kanban": "Обновляю фоновую задачу",
                "code": "Сохраняю изменения в коде",
            }.get(scope, "Сохраняю изменения")
        if name == "delegate_task":
            return "Выполняю ограниченную подзадачу"
        if name == "kanban_create":
            return "Запускаю фоновую работу"
        if name == "todo":
            return "Уточняю план"

        lowered_name = str(name or "").casefold()
        if "knowledge" in lowered_name:
            return "Проверяю базу знаний"
        if "call" in lowered_name or "transcript" in lowered_name:
            return "Проверяю сохранённые звонки"
        if "kanban" in lowered_name:
            return {
                "kanban_show": "Проверяю фоновую задачу",
                "kanban_list": "Проверяю фоновые задачи",
                "kanban_cancel": "Останавливаю фоновую задачу",
            }.get(name, "Работаю с фоновой задачей")
        if "search" in lowered_name or "find" in lowered_name:
            return "Ищу нужные данные"
        if "status" in lowered_name or "health" in lowered_name:
            return "Проверяю состояние сервиса"
        return "Проверяю текущий этап"

    def update(self, event, name, *, result=None, is_error=False, preview=None, args=None):
        if name in {None, "clarify", "_thinking"} or event not in {
            "tool.started", "tool.completed",
        }:
            return None

        self.seen = True
        self._last_tool_event = event
        if event == "tool.started":
            fallback = self._tool_stage(name, preview=preview, args=args)
            active = self.plan[2] if self.plan else ""
            self.current = active or fallback
            return self.render()

        if is_error:
            # A single tool failure is an implementation detail when the agent
            # can recover and continue. Do not alarm the user with a transient
            # "action failed" line. Only a failed todo update invalidates the
            # authoritative progress fraction; terminal turn outcome is rendered
            # later by the finalizer if the overall task really failed/partial.
            if name == "todo":
                self.plan = None
                self.next = None
            return None

        if name != "todo":
            # Successful tool completion is not a semantic state transition.
            # Keep the useful current step until todo or commentary advances it.
            return None

        self.plan = self._plan(result)
        if self.plan:
            _, _, active, next_step = self.plan
            self.current = active or "Завершаю ответ"
            self.next = next_step or None
        else:
            self.next = None
        return self.render()

    def update_commentary(self, text):
        """Project completed user-facing commentary, never raw reasoning."""
        labels = (
            ("сейчас:", "current", None),
            ("найдено:", "finding", "Найдено"),
            ("результат:", "finding", "Результат"),
            ("дальше:", "next", None),
            ("следом:", "next", None),
        )
        labelled = False
        for raw_line in str(text or "").splitlines():
            value = self._short_text(raw_line)
            lowered = value.casefold()
            for prefix, field, label in labels:
                if lowered.startswith(prefix):
                    payload = self._short_text(value[len(prefix):])
                    if payload:
                        # The provider can emit ``Сейчас: пункт N завершён`` a
                        # moment before the authoritative todo update advances
                        # completed/total.  Keeping that sentence beside the
                        # old percentage produces a briefly contradictory
                        # snapshot.  Until todo confirms it, retain the
                        # plan-derived current step and accept the finding/next
                        # lines only.
                        if not (
                            field == "current"
                            and self.plan
                            and self._is_completion_claim(payload)
                        ):
                            setattr(self, field, payload)
                        if label:
                            self.finding_label = label
                        labelled = True
                    break
        if labelled:
            self.seen = True
            return self.render()

        value = self._short_text(text)
        if not value:
            return None
        self.seen = True
        lowered = value.casefold()
        finding_prefixes = (
            "нашёл ", "нашел ", "нашла ", "обнаружил ", "обнаружила ",
            "выяснил ", "выяснила ", "готово ", "результат ",
        )
        if lowered.startswith(finding_prefixes):
            self.finding_label = "Найдено"
            self.finding = value
        else:
            self.current = value
        return self.render()

    @staticmethod
    def _is_completion_claim(value):
        lowered = str(value or "").casefold().lstrip()
        return lowered.startswith((
            "завершён", "завершен", "завершена", "завершено", "завершены",
            "выполнен", "выполнена", "выполнено", "выполнены", "готово",
            "completed", "finished", "done",
        ))

    @staticmethod
    def _plan(result):
        try:
            value = json.loads(result) if isinstance(result, str) else result
            if not isinstance(value, dict) or value.get("error") or value.get("success") is False:
                return None
            todos, summary = value.get("todos"), value.get("summary")
            if not isinstance(todos, list) or not todos or len(todos) > 100 or not isinstance(summary, dict):
                return None
            total, done = summary.get("total"), summary.get("completed")
            if type(total) is not int or type(done) is not int or total != len(todos):
                return None
            if any(not isinstance(t, dict) or t.get("status") not in {
                "pending", "in_progress", "completed", "cancelled"
            } or not isinstance(t.get("content"), str) for t in todos):
                return None
            if done != sum(t["status"] == "completed" for t in todos):
                return None
            if any(t["status"] == "cancelled" for t in todos):
                return None
            if sum(t["status"] == "in_progress" for t in todos) > 1:
                return None
            active_index = next(
                (i for i, item in enumerate(todos) if item["status"] == "in_progress"),
                None,
            )
            active = todos[active_index]["content"] if active_index is not None else ""
            pending = [
                item["content"] for i, item in enumerate(todos)
                if item["status"] == "pending" and (active_index is None or i > active_index)
            ]
            if not pending:
                pending = [item["content"] for item in todos if item["status"] == "pending"]
            if active_index is None and pending:
                current, following = pending[0], pending[1:]
            else:
                current, following = active, pending
            clean_current = ExecutionProgress._short_text(current)
            clean_next = ExecutionProgress._short_text(following[0]) if following else ""
            return done, total, clean_current, clean_next
        except (ValueError, TypeError):
            return None

    def _elapsed(self):
        seconds = max(0, int(self.clock() - self.started))
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"

    def render(self, *, final=False, outcome=None):
        if not self.seen:
            return None

        lines = []
        if self.plan:
            done, total, _, _ = self.plan
            percent = done * 100 // total
            if final and outcome == "success" and done == total:
                lines.append("✅ Готово · 100%")
            elif final and outcome == "success":
                lines.append(f"✅ Ответ готов · {percent}% по плану")
            elif final and outcome == "partial":
                lines.append(f"⚠️ Частично · {percent}% по плану")
            elif final and outcome == "failed":
                lines.append(f"⚠️ Не завершено · {percent}% по плану")
            elif final and outcome == "interrupted":
                lines.append(f"⏹ Остановлено · {percent}% по плану")
            elif final:
                lines.append(f"⏹ Статус завершения неизвестен · {percent}% по плану")
            else:
                lines.append(f"🧭 Работаю · {percent}%")
            filled = percent * 10 // 100
            lines.append(f"{'█' * filled}{'░' * (10 - filled)} {done}/{total}")
        else:
            if not final:
                lines.append("🧭 Работаю")
            elif outcome == "success":
                lines.append("✅ Ответ готов")
            elif outcome == "partial":
                lines.append("⚠️ Частично")
            elif outcome == "failed":
                lines.append("⚠️ Не завершено")
            elif outcome == "interrupted":
                lines.append("⏹ Остановлено")
            else:
                lines.append("⏹ Статус завершения неизвестен")

        semantic = []
        if self.current and not final:
            semantic.append(f"Сейчас: {self.current}")
        if self.finding:
            semantic.append(f"{self.finding_label}: {self.finding}")
        if self.next and not final:
            semantic.append(f"Дальше: {self.next}")
        if semantic:
            lines.extend(["", *semantic])
        lines.extend(["", f"⏱ {self._elapsed()}"])
        return "\n".join(lines)
