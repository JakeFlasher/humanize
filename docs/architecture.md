# Codex-native RLCR architecture

## Components

```text
Explicit plan, review-only, or full-loop skill
    |
    v
CLI and Stop-hook adapter
    |
    +-- configuration + domain + contract + consensus
    +-- storage + migrations + evidence + process registry
    +-- hardened Git artifact and isolated reviewer runner
    +-- history + reporting + trace export
    |
    v
Stop hook: allow, continue with correction packet, or report terminal state
```

The `Stop` hook is a thin adapter around the same controller used by the manual
`step` command. Nested reviewer runs disable hooks and set a private child guard
to prevent recursion.

## Layering

The controller follows a directed dependency structure learned from Humanize2's
named-layer discipline:

```text
domain <- config <- migrations <- storage <- processes
   ^          ^                      ^           ^
   |          +-- contract           |           |
   +-- consensus                     +-- reporting
   |                                             |
   +---------------- review <---- evidence ------+
                              |
                             CLI
```

`domain` contains typed values but no orchestration. `config` constructs the
immutable policy. `contract` and `consensus` own deterministic acceptance
rules. `review` owns Git artifacts and reviewer execution. `storage` owns
atomic persistence and recovery. The CLI joins these layers and is the only
user-facing state reducer.

## State machine

```text
active -> reviewing -> correcting -> reviewing -> ... -> accepted
   |          |             |
   |          |             +-> exhausted / blocked
   |          +-> canceled (registered processes terminated)
   +-> canceled
```

Terminal states are explicit:

- `accepted`: both required lanes accepted the same committed artifact.
- `blocked`: model, schema, Git-safety, state-integrity, or infrastructure
  requirements failed repeatedly or cannot be trusted.
- `exhausted`: round, call, artifact-cycle, timeout, or wall-time bounds ended.
- `canceled`: explicit user cancellation.

State is keyed by canonical project path and stored outside the model-writable
workspace by default. Each v2 transition first writes an immutable numbered
event containing the recoverable next state, links it to the previous event
digest, appends a compact JSONL summary, then atomically replaces `state.json`.
If a crash lands the event but not the snapshot, the next read verifies the
chain and restores the attested state. An exclusive lock, nonces, and leases
prevent concurrent or stale Stop events from duplicating consensus.

Session binding is deliberate. The first valid Stop hook claims an unbound run;
another session is a no-op. `adopt` clears that binding only while no live review
is running, and the event records a digest of the prior session identifier.

## Review artifact

Every review boundary requires a clean Git worktree. The controller records:

- immutable start and candidate commit IDs;
- verified start-commit ancestry;
- immutable plan, optional contract, and run-configuration snapshots/digests;
- a cumulative raw binary patch and digest;
- changed paths and patch size;
- a same-commit evidence manifest and digest;
- versioned reviewer schema and prompts;
- exact model, effort, sandbox, and command arguments.

The decisive cumulative patch is materialized by the trusted controller.
Reviewers are instructed not to invoke repository Git and receive hardened Git
environment settings in case they do.

Required checks are executed separately as an explicit argv without shell
interpretation. The controller requires a clean worktree before and after,
rejects a changed HEAD, bounds time/output, preserves full-output digests, and
only selects records from the candidate commit. Evidence files and descriptors
are rehashed before artifact construction and again after review.

## Consensus

Each reviewer returns `rlcr.review.v1` JSON. The controller independently checks
the schema and semantics, including:

- exact lane and artifact digest;
- one result for each required lane;
- explicit evidence-bearing checks;
- valid paths and line ranges;
- no accepting verdict with a blocking finding or non-passing check;
- at least one blocking finding for `changes_requested`.

An optional plan contract adds controller-owned required criterion IDs. A
specification-lane result that omits or fails a required criterion produces a
blocking plan-gap finding even if both model verdict strings say `accept`.

Acceptance is computed by the controller and is never inferred from magic prose
such as `COMPLETE` or the absence of a severity marker.

Successful lane results are cached independently by lane plus all trusted
inputs. If one lane returns malformed output or a transient failure, only that
lane consumes a retry. Cached results do not consume token usage again. Codex
JSONL `turn.completed` usage is accumulated and checked against immutable
input, output, and reasoning budgets.

## Operations and observability

- `history` and `show` read historical runs without changing the active pointer.
- `report` emits a portable `rlcr.report.v1` JSON object or Markdown summary.
- `export` renders verified state transitions as Chrome/Perfetto trace events.
- `cancel` records the terminal state before signaling only process groups whose
  attempt ID and kernel PID identity still match their registration. An
  attempt-scoped tombstone is checked before launch and immediately after PID
  registration, closing the cancel-versus-launch race.
- `resume` is limited to infrastructure-blocked runs with time and call budget
  remaining; it cannot bypass semantic or integrity failures.

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

The implementation deliberately does not load executable flows from the
repository, discover alternate providers, silently fall back to another model,
or add remote execution. See
[ADR 0001](adr/0001-humanize2-pattern-adoption.md) for the selective adoption
rationale.
