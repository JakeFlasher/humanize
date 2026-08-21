# Codex-native RLCR architecture

## Components

```text
Explicit skill
    |
    v
Controller start/status/step/cancel/resume
    |
    +-- private atomic state and append-only journal
    +-- immutable plan/schema/prompt snapshots
    +-- hardened Git artifact builder
    +-- specification reviewer (gpt-5.6-sol:xhigh)
    +-- correctness reviewer  (gpt-5.6-sol:xhigh)
    |
    v
Stop hook: allow, continue with correction packet, or report terminal state
```

The `Stop` hook is a thin adapter around the same controller used by the manual
`step` command. Nested reviewer runs disable hooks and set a private child guard
to prevent recursion.

## State machine

```text
active -> reviewing -> correcting -> reviewing -> ... -> accepted
   |          |             |
   |          |             +-> exhausted
   |          +-> blocked
   +-> canceled
```

Terminal states are explicit:

- `accepted`: both required lanes accepted the same committed artifact.
- `blocked`: model, schema, Git-safety, state-integrity, or infrastructure
  requirements failed repeatedly or cannot be trusted.
- `exhausted`: round, call, artifact-cycle, timeout, or wall-time bounds ended.
- `canceled`: explicit user cancellation.

State is keyed by canonical project path and stored outside the model-writable
workspace by default. Updates use an exclusive lock, temporary file, `fsync`,
and atomic replacement. Reviewer attempts use nonces and leases so concurrent
or stale `Stop` events cannot duplicate consensus.

## Review artifact

Every review boundary requires a clean Git worktree. The controller records:

- immutable start and candidate commit IDs;
- an immutable plan snapshot and digest;
- a cumulative raw binary patch and digest;
- changed paths and patch size;
- versioned reviewer schema and prompts;
- exact model, effort, sandbox, and command arguments.

The decisive cumulative patch is materialized by the trusted controller.
Reviewers are instructed not to invoke repository Git and receive hardened Git
environment settings in case they do.

## Consensus

Each reviewer returns `rlcr.review.v1` JSON. The controller independently checks
the schema and semantics, including:

- exact lane and artifact digest;
- one result for each required lane;
- explicit evidence-bearing checks;
- valid paths and line ranges;
- no accepting verdict with a blocking finding or non-passing check;
- at least one blocking finding for `changes_requested`.

Acceptance is computed by the controller and is never inferred from magic prose
such as `COMPLETE` or the absence of a severity marker.

## Trust boundaries

- Reviewer processes are ephemeral, strict-configured, read-only, approval
  `never`, and have web search, subagents, user config, rules, and hooks disabled.
- Environment inheritance is minimized and secret-like variables are excluded
  from reviewer shell commands.
- Trusted Git calls remove ambient `GIT_*` overrides, ignore global/system
  config, disable lazy fetching and optional locks, neutralize executable
  filters/diff drivers/fsmonitor/hooks, and fail closed on config includes or
  per-worktree config.
- Snapshot and patch integrity is checked before and after each review.
- Repository text remains untrusted evidence. Same-family reviewers can share
  blind spots, so deterministic tests and human review remain part of the
  release decision.
