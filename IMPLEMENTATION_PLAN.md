# Implementation Plan: Extensible AI Quota Monitor

## Goal
Build a quota monitor similar to the shown UI, with fast onboarding of new providers (OpenAI, Windsurf, Amp, etc.) without changing core orchestration logic.

## Product Requirements (from the target UI)
1. Display multiple providers in stacked cards.
2. Show provider plan badge (`Pro`, `Free`, etc.).
3. Support multiple quota rows per provider.
4. Each row shows:
   - label (e.g., `Gemini Pro`, `Prompt credits`)
   - progress bar
   - left metric (`100% left`, `17.5 credits left`, `$10 left`)
   - right metric (`Resets in ...`)
5. Global footer with:
   - app version
   - next update countdown.

## Architecture Overview
1. Provider plugin layer: each provider is an isolated adapter.
2. Normalized domain model: all providers return the same structure.
3. Collector/orchestrator: runs providers, handles retries/timeouts/errors.
4. Cache/storage layer: persists latest normalized snapshots.
5. Presentation layer: terminal UI now, widget/web UI later.

## Phase 1: Core Refactor for Extensibility

### 1.1 Define normalized models
Create shared models (dataclasses or TypedDict):
- `ProviderSnapshot`
  - `id: str`
  - `name: str`
  - `plan: str | None`
  - `items: list[QuotaItem]`
  - `last_update: datetime`
  - `next_update: datetime | None`
  - `ok: bool`
  - `error: str | None`
- `QuotaItem`
  - `id: str`
  - `label: str`
  - `unit: "percent" | "credits" | "usd" | "tokens"`
  - `remaining_value: float | None`
  - `remaining_fraction: float | None`
  - `limit_value: float | None`
  - `reset_at: datetime | None`
  - `meta: dict[str, Any]`

### 1.2 Introduce provider interface
Implement a strict base contract:
- `provider_id` (string key)
- `collect(context) -> ProviderSnapshot`

Any provider must return normalized data and never crash the orchestrator.

### 1.3 Provider registry
Add `providers/registry.py` with:
- explicit registration map (`id -> class`) or auto-discovery
- helper methods:
  - `list_providers()`
  - `get_provider(id)`

`main.py` should iterate providers from the registry instead of hardcoded `if provider == ...`.

### 1.4 Migrate existing providers
Migrate current `antigravity` and `gemini_cli` logic into new provider adapters:
- `providers/antigravity.py`
- `providers/gemini.py`

Keep the existing parsing logic, but adapt output to normalized models.

## Phase 2: Collection, Resilience, and Storage

### 2.1 Orchestrator
Create `collector.py`:
- parallel provider execution (thread pool is enough)
- per-provider timeout
- retry policy (e.g., 1 retry for transient errors)
- error isolation (one provider failure does not affect others)

### 2.2 Snapshot cache
Add local cache (`cache/latest.json` or sqlite):
- write normalized snapshots with timestamp
- read last known good data for fallback display

### 2.3 Refresh cadence
Add configurable refresh interval (e.g., 240s):
- compute and store `next_update`
- expose countdown to UI/footer

## Phase 3: UI Layer (matching target layout)

### 3.1 UI components
Build reusable render blocks:
1. Provider header (`name + plan badge`)
2. Quota row (`label + progress bar + left/right metrics`)
3. Footer (`version + next update in ...`)

### 3.2 Unit-aware formatting
Implement formatter utilities:
- percent: `97% left`
- credits: `17.5 credits left`
- usd: `$10 left`
- tokens: `120k tokens left`

### 3.3 Health and stale states
Show distinct states:
- live data
- stale cached data
- provider error with reason

## Phase 4: Fast Provider Onboarding Workflow

### 4.1 Provider template
Add `providers/_template.py` with:
- auth input placeholders
- parser skeleton
- normalized output example

### 4.2 Provider checklist (for contributors)
For each new provider:
1. Implement adapter.
2. Add environment variables to config/docs.
3. Add parser fixtures (sample raw payloads).
4. Add unit tests for parser + contract test.
5. Register provider in registry.

### 4.3 Documentation
Update README:
- how to add provider in <10 minutes
- required env vars
- debug mode and troubleshooting.

## Phase 5: macOS Widget Path

### 5.1 Data export contract
Add `--format json` CLI output and `status.json` snapshot.

### 5.2 Background updater
Use `launchd` to refresh snapshots periodically.

### 5.3 Widget implementation
Build native WidgetKit extension (SwiftUI):
- reads `status.json`
- shows key provider/row summary
- uses timeline updates from snapshot timestamps.

## Suggested File Structure
```text
agent_usage_monitor/
  main.py
  collector.py
  models.py
  providers/
    __init__.py
    registry.py
    _template.py
    antigravity.py
    gemini.py
    openai.py
    windsurf.py
    amp.py
  ui/
    __init__.py
    render.py
    formatters.py
  cache/
    latest.json
  tests/
    test_provider_contract.py
    test_antigravity_parser.py
    test_gemini_parser.py
```

## Milestones
1. `M1`: Registry + normalized models + migrated current providers.
2. `M2`: Orchestrator + cache + robust error handling.
3. `M3`: UI parity with target layout in terminal.
4. `M4`: Add Windsurf + Amp adapters.
5. `M5`: JSON export + macOS widget integration.

## Risks and Mitigations
1. Provider APIs may be unofficial or unstable.
   - Mitigation: parser fixtures + fallback parsing + graceful degradation.
2. Auth/token sources differ across providers.
   - Mitigation: provider-specific auth modules and strict error messages.
3. Rate limits or endpoint changes.
   - Mitigation: retries, cache fallback, contract tests.

## Definition of Done
1. New provider can be added by creating one file + registry entry.
2. Main flow does not require changes for each new provider.
3. UI correctly renders mixed quota units.
4. Failures are isolated and surfaced without crashing app.
5. JSON snapshot is stable and ready for WidgetKit consumption.
