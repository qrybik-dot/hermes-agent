# Hermes architecture map

Updated: 2026-08-14

```text
Telegram / approved webhook / cron
                 |
                 v
      hermes-gateway.service
      native upstream AIAgent
      - owns goal and conversation
      - selects native tools
      - verifies effects and final
          |                 |
          | model call      | bounded native tool call
          v                 v
 isolated CLIProxyAPI    Tool Search / terminal / Knowledge / plugins
 one Antigravity OAuth      |              |
          |                 |              +-- optional adapters/services
          v                 |                  independently pausable
       Gemini               +-- native delegate_task, depth=1,
                                concurrency=1 when the task justifies it

 pre-effect provider failure only
                 |
                 v
 native Codex fallback (Terra)

 control plane --> lease / immutable current+previous / rollback / manifest
```

| Component | Owns | Must not own |
|---|---|---|
| Native `AIAgent` | user goal, context, plan, tool loop, verified final | host deployment or secret storage |
| Native tools and plugins | bounded capability and typed result | general orchestration or finalization |
| Knowledge | indexed data and typed read/write receipts | agent planning or provider routing |
| CLIProxyAPI | one authenticated model transport | conversation, tools, fallback truth or user final |
| Optional domain services | their own narrow domain job | universal task runtime |
| Change control | lease, release transition, rollback and manifest | user intent or semantic task decisions |

No edge in this map permits a tool, proxy or domain service to become a second
Hermes. New capabilities attach at the native tool boundary and must remain
removable without rewriting the core.

