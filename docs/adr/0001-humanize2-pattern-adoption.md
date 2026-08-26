# ADR 0001: Selective Humanize2 pattern adoption

- Status: Accepted
- Date: 2026-08-26
- Decision owners: JakeShea Humanize RLCR maintainers

## Context

The prior Humanize repository stopped being the active design center. Its
successor, [humanfia/humanize2](https://github.com/humanfia/humanize2), is a
new Python orchestration product with flows, multiple agent backends, machines,
providers, resumable cycles, a TUI, and trace collection. This plugin is much
narrower: it is a personal Codex-only completion gate driven by one native Stop
hook.

Research and comparison were performed against Humanize2 commit
[`72bfb030d423eaecb2ec7589c000483320cfc95e`](https://github.com/humanfia/humanize2/commit/72bfb030d423eaecb2ec7589c000483320cfc95e),
authored 2026-08-26. The source at that revision is Apache-2.0. The review
covered its normative specifications, layer rules, flow/session model,
concurrency, cycle persistence, resuming, security, reporting, and tracing.

## Decision

Adopt concepts that strengthen a bounded Codex plugin while implementing them
independently in the existing standard-library controller:

| Humanize2 pattern | RLCR v2 application |
| --- | --- |
| Named layers with a directed dependency graph | Domain, configuration, contracts, consensus, evidence, processes, review, storage, reporting, and CLI modules |
| A run captures what it drives before the first turn | Immutable `rlcr.run-config.v1` snapshot and digest |
| Concurrent work means independent sessions | Two fresh reviewer processes, one per named lane, launched concurrently |
| A cycle is an inspectable directory | Private per-run snapshots, rounds, evidence, hash-chained events, history, and reports |
| Resuming is explicit persisted state | Typed phases, crash recovery, infrastructure resume, and explicit session adoption |
| Trace output is portable | Chrome/Perfetto JSON export from controller events |
| Focused flows carry focused skills | Separate explicit plan, review-only, and full RLCR skills |
| Agent capabilities are checked before work | Codex CLI flag preflight and immutable model/effort policy |

Preserve or strengthen the original plugin boundaries:

- reviewers receive a controller-materialized cumulative patch, not a mutable
  flow definition;
- repository content is evidence and never executable orchestration;
- reviewer children are read-only, approval-free, strict-configured, and have
  hooks, plugins, apps, skills, subagents, image generation, browser use, and
  web search disabled;
- deterministic evidence is explicit argv execution at a clean commit and is
  cryptographically bound to that commit;
- acceptance remains controller-owned exact consensus, never model prose.

## Deliberately not adopted

- Executable Python flows or third-party flow repositories. Loading repository
  orchestration code would violate this plugin's untrusted-repository model.
- Multi-provider accounts, dynamic model discovery, or cross-model fallback.
  RLCR intentionally pins `gpt-5.6-sol:xhigh`; silent fallback would change the
  review policy mid-run.
- Remote anchors, containers, a TUI, telemetry, or unattended general-purpose
  agent execution. They are outside a local completion-gate plugin's scope.
- Humanize2 source code or package dependencies. The implementation is a
  clean-room adaptation of public design ideas, remains MIT, supports Python
  3.10+, and uses only the standard library at runtime.

## Consequences

The controller has more modules and persisted metadata, but its trust boundary
is narrower and more auditable. Older run snapshots remain readable through a
one-way migration view; active runs still refuse a changed runtime and must be
restarted. The two lanes reduce single-context failure but do not provide
model-family diversity, so deterministic checks and human judgment remain
release requirements.
