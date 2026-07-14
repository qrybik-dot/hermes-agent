# Telegram contract eval

This suite grades sanitized observations from an isolated or shadow Hermes
model run. It does not call production tools and does not contain copied chats,
personal memory, tokens, IDs, or raw tool output.

Critical assertions are deterministic: public verdicts, technical leakage,
response mode, call budgets, status-message count, and duplicate delivery.
Model/provider names and prompt hashes are observation metadata, not hard-coded
recommendations.

`--self-test` validates only the grader. A release gate requires a JSONL file
captured from the candidate model/gateway route with side effects replaced by
fake adapters.
