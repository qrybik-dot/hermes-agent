# Runlog - graphics full test

Дата: 2026-06-16  
Режим: safe-write  
Рабочая директория: `/home/hermes/.hermes/hermes-agent`  
Запрещено пользователем: runtime Hermes, gateway, cli-proxy-api, systemd, env, model routing, restarts, installs, secrets/OAuth/private keys.

## Цель

Провести A/B тест графических инструментов и маршрутизации Hermes: GPT Image/OpenAI branch, Gemini/Nano Banana branch, NotebookLM as brief layer, Mermaid/SVG/HTML as non-image fallback.

## Roadmap фактически

1. Capability audit - выполнено.
2. Test matrix - выполнено.
3. Прогон image tests - не выполнялся: пользователь выбрал вариант 3, без платных генераций.
4. Non-image fallback outputs - выполнено.
5. Documentation + decision table - выполнено.

## Read-only checks

### Git status

Команда:

```bash
git status --short --branch
```

Результат:

```text
## main...origin/prod/hermes-vps
?? .tasks/
?? docs/GRAPHIC_GENERATION_ROUTER.md
?? docs/TASK_INTAKE_ROUTER.md
?? scripts/dry_run_router.py
```

### Image model catalog audit

Команда была локальной Python-инспекцией `tools/image_generation_tool.py`, без чтения секретов.

Найдено:

```text
active_model=fal-ai/flux-2/klein/9b
active_display=FLUX 2 Klein 9B
schema_params=prompt,aspect_ratio
gpt_models=fal-ai/gpt-image-1.5,fal-ai/gpt-image-2
gemini_models=fal-ai/nano-banana-pro
```

Ключевой вывод: agent-facing `image_generate` принимает только `prompt` и `aspect_ratio`; edit/reference inputs отсутствуют.

### NotebookLM check

Команда:

```bash
nlm login --check --profile hermes-vps
```

Секреты и email замаскированы. Результат: `profile hermes-vps authorized`.

## User decision on paid generation

Агент запросил подтверждение перед платными image calls:

- full 22 image outputs
- minimal 5 image outputs
- no paid generations

Пользователь выбрал: `3` - только docs + SVG/Mermaid fallback.

## Created artifacts

- `docs/GRAPHIC_TEST_MATRIX.md`
- `docs/GRAPHIC_TOOL_DECISION_TABLE.md`
- `.tasks/2026-06-16_graphics_full_test/runlog.md`
- `.tasks/2026-06-16_graphics_full_test/outputs/B1_html.svg`
- `.tasks/2026-06-16_graphics_full_test/outputs/B2_html.svg`
- `.tasks/2026-06-16_graphics_full_test/outputs/F1_mermaid.md`
- `.tasks/2026-06-16_graphics_full_test/outputs/F1_svg.svg`

## Scoring summary

Image outputs were not scored as real results because they were not generated. Non-image fallback outputs were scored in `docs/GRAPHIC_TEST_MATRIX.md`.

## What was not touched

- No config changes.
- No systemd changes.
- No runtime Hermes/gateway/cli-proxy-api changes.
- No restarts.
- No installs.
- No commits.
- No secrets/OAuth/private key files read.
- No model routing changed.

## Final conclusion

Current Hermes can route text-to-image through the FAL image catalog, including GPT Image and Nano Banana entries, but the exposed tool schema is text-only. For exact text and diagrams, SVG/Mermaid/HTML should win by policy. For edit/reference/consistency, the router should not pretend current `image_generate` is enough; it should mark the task as requiring edit-capable provider integration.
