# Architecture Decision Records

## 2026-07-13: Scope plugin manager state by Hermes home/profile (keyed cache)

Status: Accepted

Context:
Hermes supports multiple profiles via different Hermes home directories.
Homes are switched two ways in a running process: the `HERMES_HOME`
environment variable (single-profile CLI/gateway processes), and the
context-local `set_hermes_home_override()` (`hermes_constants.py`), which
the multiplexed gateway worker (`gateway/run.py`'s `_profile_scope`) and
subagent/embedded callers use to serve several profiles from one
long-lived process. The override is a `ContextVar` and deliberately does
**not** mutate `os.environ`, since that would leak one profile's home
into every other concurrent task in the same process.

The plugin manager was a process-global single-slot singleton
(`_plugin_manager`). User-installed plugins are discovered from
`get_hermes_home() / "plugins"`, and context-engine plugins (e.g.
`hermes-lcm`) capture profile-scoped state — such as the LCM database
path — at registration time. A single-slot cache meant:

1. Switching homes via `set_hermes_home_override()` was invisible to a
   naive "did `HERMES_HOME` change" check, so the singleton silently kept
   serving the first profile's manager to every other profile in the
   process.
2. Even when a fresh `PluginManager` *was* created for a new home, plugin
   modules are imported into `sys.modules` as `hermes_plugins.<slug>` by
   `_load_directory_module`, and only that top-level module was ever
   replaced. A same-slug plugin's *relative* imports
   (`from . import state`) are cached separately under
   `hermes_plugins.<slug>.<submodule>`, and Python's import machinery
   resolves those from `sys.modules` first — so a profile switch could
   silently keep serving a previous profile's already-imported submodule
   code/state instead of re-executing the new profile's plugin.

Decision:
- Replace the single-slot singleton with a cache keyed on the *resolved*
  Hermes home path (`_plugin_managers_by_home: Dict[Path, PluginManager]`).
  `get_plugin_manager()` resolves the current home via `get_hermes_home()`
  (which itself already consults `get_hermes_home_override()` before
  `os.environ`), so both the env-var and context-local override paths are
  covered uniformly.
- `_plugin_manager` (the old single-slot name) is kept as a thin "last
  manager returned" pointer purely for backward compatibility with
  existing test code that does
  `monkeypatch.setattr(plugins_mod, "_plugin_manager", some_manager)`.
  When that name is monkeypatched to a manager the keyed cache doesn't
  know about, `get_plugin_manager()` treats it as an explicit injection
  and adopts it into the cache under the *current* resolved home, rather
  than discarding it.
- Both `PluginManager._load_directory_module` (initial/`force=True`
  reload within the same home) and the shared `_clear_plugin_submodules`
  helper (profile switch / test teardown) evict `sys.modules[module_name]`
  **and every name prefixed with `module_name + "."`** before a plugin
  slug is (re-)imported, so relative-import submodules can never survive
  a reload or a home switch.
- Test isolation (`tests/conftest.py`'s `_hermetic_environment` fixture)
  calls a new `_reset_plugin_managers_for_tests()` helper that drops the
  entire keyed cache and purges every plugin submodule from `sys.modules`
  between tests, instead of only resetting the single-slot pointer.

Consequences:
- Per-profile LCM instances (and any other context-engine plugin) use
  their own `{home}/lcm.db` regardless of whether the profile switch went
  through `HERMES_HOME` or `set_hermes_home_override()`.
- Plugin discovery remains cached within a profile for normal
  performance, and re-entering a previously-seen profile reuses its
  cached manager instead of rebuilding from scratch.
- Sequential *and* interleaved profile switching — in tests, the gateway
  multiplexer worker, or embedded callers using the context-local
  override — no longer leaks context-engine state, plugin module state,
  or stale relative-import submodules across profiles.
- Regression coverage exercises the real production path
  (`set_hermes_home_override()`) rather than only the env-var path, and
  includes a dedicated relative-import leak test.

## 2026-09-01: Universal media download is one native transactional tool

Status: Accepted for candidate qualification

Baseline: `3d344e217cab846fd5c80738aa8e794e97618be3`

Context:
Telegram requests such as `пришли видео` were handled as free-form agent
research. A production incident installed downloaders ad hoc, waited 181.62
seconds on a 429 retry loop, tried unrelated fallbacks, exposed technical
errors, and left a downloaded file on disk. The user contract is one public
URL in and one delivered Telegram video out, with bounded failure and cleanup.

Decision:
- Add one `media_download` tool that owns URL validation, pinned yt-dlp
  execution, timeout/retry policy, truthful delayed progress, current-chat
  Telegram delivery, message-id confirmation, and `finally` cleanup.
- Add one thin `media-download` skill that routes explicit download intent and
  bare Telegram video URLs to that tool and forbids terminal/browser/service
  fallback exploration.
- Keep authentication, cookies, proxies, DRM bypass, a new daemon, and a local
  Bot API server out of this candidate. The official Bot API remains capped at
  50 MB; oversized files fail before send. A separate approved slice may add a
  local Bot API only if real usage demonstrates that need.
- Pin `yt-dlp==2026.8.19` as an opt-in dependency. Do not enable broad runtime
  package installation; production must install the exact pin through the
  managed environment before activation.

Options considered:
1. Do nothing: rejected because the observed retry/fallback/cleanup incident
   remains reproducible.
2. Skill-only instructions: rejected because the model would still own the
   unsafe multi-step transaction and cleanup.
3. Native tool plus thin skill: selected as the smallest deterministic repair.
4. Separate downloader service, proxy pool, or browser worker: rejected as
   unnecessary operational and security scope.

Consequences:
- Supported public yt-dlp sources share one bounded path; site-specific access
  limits become short classified failures rather than open-ended research.
- Download progress uses only downloader bytes; Telegram upload shows a stage
  label because the current adapter does not expose byte progress.
- Temporary files are private and deleted after confirmed send or failure;
  stale tool-owned request directories are reaped on the next invocation.
- Readiness still requires pinned-dependency installation, focused tests,
  managed deploy, actual Telegram smoke, cleanup read-back, and rollback proof.
