import unittest

from gateway.save_intent_router import detect_save_intent


class SaveIntentRouterTests(unittest.TestCase):
    def test_typo_save_with_reply_url_goes_to_knowledge(self):
        decision = detect_save_intent(
            "сохрарни в базу инфо",
            "https://yandex.cloud/ru/docs/datalens/dashboard/insights#neuroanalyst-2",
        )
        self.assertTrue(decision.matched)
        self.assertEqual(decision.route, "save_knowledge")
        self.assertFalse(decision.needs_clarification)
        self.assertFalse(decision.terminal_allowed)
        self.assertFalse(decision.llm_allowed)

    def test_save_this_with_reply_text_goes_to_knowledge(self):
        decision = detect_save_intent(
            "сохрани это",
            "Статья про агентный BI в дашбордах и требования к контексту.",
        )
        self.assertEqual(decision.route, "save_knowledge")
        self.assertFalse(decision.needs_clarification)

    def test_bare_remember_asks_with_buttons(self):
        decision = detect_save_intent("запомни")
        self.assertEqual(decision.route, "ask_clarification")
        self.assertTrue(decision.needs_clarification)
        self.assertGreaterEqual(len(decision.buttons), 3)
        self.assertIn("В базу", [button["text"] for button in decision.buttons])

    def test_explicit_reminder_delegates_not_knowledge(self):
        decision = detect_save_intent("запиши напоминание разобраться со штрафами")
        self.assertEqual(decision.route, "delegate_action")
        self.assertFalse(decision.needs_clarification)

    def test_explicit_task_delegates_not_knowledge(self):
        decision = detect_save_intent("добавь задачу купить лекарства")
        self.assertEqual(decision.route, "delegate_action")

    def test_url_in_command_goes_to_knowledge(self):
        decision = detect_save_intent("сохрани ссылку https://example.com/a?b=1")
        self.assertEqual(decision.route, "save_knowledge")

    def test_soft_task_without_destination_asks(self):
        decision = detect_save_intent("запомни купить молоко")
        self.assertEqual(decision.route, "ask_clarification")
        self.assertTrue(decision.needs_clarification)

    def test_do_not_treat_delete_as_save(self):
        decision = detect_save_intent("удали это из базы")
        self.assertFalse(decision.matched)
        self.assertEqual(decision.route, "ignore")

    def test_knowledge_hint_without_save_verb_is_ignored(self):
        decision = detect_save_intent("пришли ссылку на выбранную парковку")
        self.assertFalse(decision.matched)
        self.assertEqual(decision.route, "ignore")


if __name__ == "__main__":
    unittest.main()
