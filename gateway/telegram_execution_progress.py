"""Telegram projection of native tool events; no execution or durable state."""
import json
import time


class ExecutionProgress:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.started = clock()
        self.plan = None
        self.stage = "Обрабатываю задачу"
        self.seen = False

    def update(self, event, name, *, result=None, is_error=False):
        if name in {None, "clarify", "_thinking"} or event not in {"tool.started", "tool.completed"}:
            return None
        self.seen = True
        if event == "tool.started":
            self.stage = {
                "terminal": "Выполняю команду", "todo": "Обновляю план",
                "web_search": "Ищу источники", "web_extract": "Читаю источники",
                "read_file": "Читаю файл", "delegate_task": "Выполняю подзадачи",
            }.get(name, "Выполняю действие")
        else:
            self.stage = "Получена ошибка инструмента" if is_error else "Обрабатываю результат"
            if is_error:
                self.plan = None
            if name == "todo":
                self.plan = None if is_error else self._plan(result)
        return self.render()

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
            active = next((t["content"] for t in todos if t["status"] == "in_progress"), "")
            return done, total, " ".join(active.split())[:140]
        except (ValueError, TypeError):
            return None

    def render(self, *, final=False):
        if not self.seen:
            return None
        lines = ["Обработка завершена" if final else self.stage]
        if self.plan:
            done, total, active = self.plan
            lines.append(f"По отметкам плана: {done}/{total} · {done * 100 // total}%")
            if active and not final:
                lines.append(f"Текущий этап: {active}")
        if final:
            lines.append(f"Время обработки: {max(0, self.clock() - self.started):.1f} с")
        return "\n".join(lines)
