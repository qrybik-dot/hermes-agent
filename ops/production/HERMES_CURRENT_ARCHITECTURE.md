# Hermes production architecture

Updated: 2026-08-14  
Scope: native autonomous core candidate

## Governing invariant

Hermes has one semantic owner: the upstream native `AIAgent` running inside
`hermes-gateway.service`. It owns the user goal, conversation, planning, native
tool loop, bounded recovery and the single final answer. No custom general
router, workflow engine, evaluator, domain service or provider proxy may take
over that role.

## Runtime boundaries

- `hermes-gateway.service` runs source only from the immutable `current`
  release. `previous` is the typed rollback target.
- `upstream` is the official NousResearch vendor repository. `origin` is the
  private backup/history for our narrow production delta; its canonical branch
  is `prod/hermes-autonomous-core`. The VPS legacy checkout is only a clean
  staging mirror for the release manager and is never production truth.
- The active profile supplies configuration and credentials; secrets are not
  stored in the release.
- Knowledge, Calendar, Granola, Avito, calls, NotebookLM and Money Agent are
  tools or independently pausable services. They do not own general planning,
  routing, session state or completion.
- `cli-proxy-api.service` is a transport adapter only. Its active auth directory
  contains one Antigravity OAuth credential, concurrency is bounded by the
  gateway, and it has no xAI, AI Studio or OpenAI-compatible route.
- Direct `agy` is not in the production request path. It may be used manually
  as a bounded diagnostic or read-only research leaf after a resource gate.

## Model policy

- Ordinary interactive primary: isolated Antigravity transport, configured
  alias `gemini-3.6-flash-high`. The transport currently reports backend model
  identity `gemini-3.6-flash`; this alias resolution is recorded explicitly.
- Pre-effect provider fallback: native `openai-codex/gpt-5.6-terra`, low
  reasoning. It is attempted only for a qualifying provider failure before an
  external effect.
- Bounded routine worker: Luna/Terra according to the frozen task contract.
- Sol-high is an engineering planner and acceptance judge outside the
  production agent loop, never an always-on runtime component.

After an effect or an ambiguous effect timeout, Hermes does not switch models
and repeat the action. It reconciles through the source of truth and the same
idempotency identity. A fallback answer is never reported as proof that the
primary route succeeded.

## Narrow candidate deltas

Relative to the accepted native core lineage, the final candidate adds only:

1. profile-scoped Codex credential support in the existing native fallback
   resolver;
2. immediate reuse of the existing scoped MCP child reaper after idle recycle;
3. systemd timeout inspection against the manager that owns the actual cgroup,
   preventing a stale same-named user unit from producing a false warning;
4. recognition of release-manager-owned systemd units, so the standalone CLI
   cannot label them stale or overwrite their shared venv, immutable cwd and
   resource controls during `gateway restart`.

These changes add no dependency, daemon, router, memory layer or second agent.

## Operational policy

- One production writer and one control-plane lease.
- Deploy and rollback only through the managed immutable release manager.
- Runtime truth is `current` plus `previous`; Git backup truth is
  `origin/prod/hermes-autonomous-core`; vendor truth is `upstream`.
- Real effects require source-of-truth read-back; false success and duplicate
  effects are release blockers.
- Host floor: `MemAvailable >= 262144 KiB`; OOM, PID/invocation drift,
  sustained PSI or swap-I/O pressure fail closed.
- Optional services remain paused during the observation window and return one
  at a time after their own smoke and resource check.
