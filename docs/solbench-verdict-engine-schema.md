# Sidecar Schema For The Artifact Verdict Engine

The artifact verdict engine consumes a per-round JSON sidecar at
`.humanize/rlcr/<loop>/round-<N>-objectives.json` and emits a JSONL row to
`.humanize/rlcr/<loop>/solbench-progress.jsonl`. This document is the
authoritative description of the sidecar contract and the JSONL row shape.

The engine ships solbench-specific behaviour in v1; the adapter seam under
`hooks/lib/verdict_adapters/` reserves an extension point for future adapters
without exposing an external `--adapter` flag.

## Schema Version

`schema_version: "1.0"`. The string is required on every sidecar and on every
emitted JSONL row so a future migration can route mixed corpora through the
correct decoder.

## Required Fields

### Identity Block

These nine fields uniquely identify the sidecar and pin it to the round /
manifest pair it was authored against. The engine validates each field; any
missing field or stale `generated_at` (older than 24 hours) produces a
hard-block.

| Field | Type | Notes |
|-------|------|-------|
| `schema_version` | string | Must equal `"1.0"` in v1. |
| `loop_id` | string | Loop directory basename, e.g. `2026-05-15_22-01-15`. |
| `round` | integer | Must match `state.md` `current_round` at sidecar-write time. |
| `adapter` | string | Adapter identifier; v1 expects `"solbench"`. |
| `objective_id` | string | Short stable token (e.g. `metric_extractor_t7`). |
| `objective_hash` | string | SHA-256 hex of the canonical objective definition. |
| `manifest_path` | string | Repo-relative or absolute path to the v2 manifest. |
| `manifest_hash` | string | SHA-256 hex of the manifest file's bytes. |
| `generated_at` | string | RFC 3339 timestamp at sidecar emission. |

### Correctness Block

```json
"correctness": {"passed": true, "tests_passed": 9, "tests_failed": 0}
```

`passed=false` hard-blocks the gated transition with
`block_reason="correctness_failed"`.

### Latency Block

```json
"latency": {
  "required": false,
  "delta_pct": -2.1,
  "threshold_pct": null,
  "basis": "cupti_activity"
}
```

- `required=true` AND `delta_pct < threshold_pct` -> hard-block with
  `block_reason="required_latency_threshold_breach"`.
- `required=false` OR `delta_pct >= threshold_pct` with non-trivial regression
  -> soft-warn (exit code 2). The wrapper increments the drift counter.

`basis` annotates which counter-free surface produced the measurement (e.g.
`cupti_activity`, `nvbit_replay`); it is forwarded verbatim into the JSONL.

### SOL Score Block

```json
"sol_score": {
  "value": "unknown_t_sol",
  "provenance": {
    "authority": "local_proxy_5060",
    "basis": "solar_stage4_missing",
    "leaderboard_comparable": false
  }
}
```

- `value` is either a JSON number or the literal string `"unknown_t_sol"`. No
  silent default. No `null`. No `0.0` fallback.
- `provenance.authority` enumerates where the score was computed (e.g.
  `local_proxy_5060`, `solar_stage4_registered`).
- `provenance.basis` records the methodology (e.g. `solar_stage4_registered`,
  `solar_stage4_missing`).
- `provenance.leaderboard_comparable` reports whether the score is comparable
  against the public SOL leaderboard.

When the operator opts in via `leaderboard_comparable_required: true` (see
below), a sentinel `"unknown_t_sol"` value hard-blocks with
`block_reason="leaderboard_comparable_required_but_unknown"`.

### Leaderboard Opt-In

```json
"leaderboard_comparable_required": false
```

Boolean opt-in for AC-5e. Most rounds set this to `false`; metric_extractor
rounds that publish a leaderboard-comparable SOL score set it to `true` and
expect the engine to refuse advancement until a real value lands.

### Required Surfaces

```json
"required_surfaces": ["nvbit", "cupti_activity"]
```

Surfaces whose manifest `status == "failed"` hard-block with
`block_reason="required_surface_failed:<name>"`. Surfaces missing from this
list are non-required and never trigger a hard-block from AC-5b, even if their
manifest status is `failed`.

### AC Deltas

```json
"ac_deltas": {"AC-3": {"from": "pending", "to": "passed"}}
```

Free-form dictionary of objective deltas. Forwarded verbatim into the JSONL
row; the engine does not interpret the structure beyond preserving it.

### Rule Compliance

The nine CLAUDE rules each carry a status enum `{verified, violated,
not_evaluated}` plus an optional `evidence` string. The severity partition is:

- Rules 1, 2, 3, 4, 5, 6, 8 -> hard-block on `violated` with
  `block_reason="rule_violation:<rule_id>"`.
- Rule 7 (default-stream discipline) -> hard-block on `violated` IF
  `rule_required_by_objective.rule_7 == true` OR the engine machine-proves a
  violation from the manifest / transcript. Otherwise ledger-only.
- Rule 9 (IISWC non-access) -> hard-block on `violated` IF file/transcript
  evidence is present. Otherwise ledger-only.
- `not_evaluated` for any rule is ledger-only UNLESS
  `rule_required_by_objective.<rule_id> == true`, in which case it
  hard-blocks with `block_reason="rule_not_evaluated:<rule_id>"`.

```json
"rule_compliance": {
  "rule_1_no_ncu": {"status": "verified", "evidence": "manifest.host.tools.ncu==absent"},
  "rule_2_no_clock_lock": {"status": "verified", "evidence": "..."},
  "rule_3_no_privileged_cupti": {"status": "verified", "evidence": "..."},
  "rule_4_no_host_driver_work": {"status": "verified", "evidence": "..."},
  "rule_5_submission_language": {"status": "verified", "evidence": "..."},
  "rule_6_no_evaluator_state_exploit": {"status": "not_evaluated", "evidence": null},
  "rule_7_default_stream": {"status": "not_evaluated", "evidence": null},
  "rule_8_precision_contract": {"status": "verified", "evidence": "..."},
  "rule_9_iiswc_no_access": {"status": "not_evaluated", "evidence": null}
}
```

### Rule Required By Objective

```json
"rule_required_by_objective": {"rule_7": false, "rule_9": false}
```

Per-rule opt-in. When `rule_required_by_objective.rule_N == true`, the
corresponding `not_evaluated` or `violated` (for the conditional rules 7, 9)
status hard-blocks.

## Manifest Block

The engine reads the manifest at `sidecar.manifest_path`. It expects the
solbench v2 manifest with a `surfaces` array whose entries carry:

| Field | Type | Notes |
|-------|------|-------|
| `name` | string | Surface name (e.g. `nvbit`). |
| `status` | string | One of `{ok, unavailable, blocked, failed, skipped}`. |
| `waiver_reason` | string \| null | Operator-supplied waiver context. |
| `substatus` | string \| null | Optional finer-grained sub-state. |
| `evidence_label_contribution` | string \| null | Label fragment forwarded to evidence packets. |

Each surface entry is preserved verbatim in the JSONL row. The engine never
collapses surfaces to a `failed_count` integer (AC-4 negative test).

## JSONL Row Shape

Every fired transition appends exactly one JSONL row to
`.humanize/rlcr/<loop>/solbench-progress.jsonl`. Shape:

```json
{
  "schema_version": "1.0",
  "timestamp": "2026-05-15T23:00:00Z",
  "loop_id": "2026-05-15_22-01-15",
  "round": 1,
  "transition": "next_round",
  "mode": "gated",
  "adapter": "solbench",
  "engine_exit_code": 0,
  "verdict_blocked": false,
  "verdict_warned": false,
  "block_reason": null,
  "codex_verdict": "advanced",
  "computed_verdict": "advanced",
  "verdict_mismatch": false,
  "correctness": {"passed": true, "tests_passed": 9, "tests_failed": 0},
  "latency": {"required": false, "delta_pct": -2.1, "threshold_pct": null, "basis": "cupti_activity"},
  "sol_score": {"value": "unknown_t_sol", "provenance": {"authority": "local_proxy_5060", "basis": "solar_stage4_missing", "leaderboard_comparable": false}},
  "leaderboard_comparable_required": false,
  "required_surfaces": ["nvbit", "cupti_activity"],
  "surfaces": [
    {"name": "nvbit", "status": "ok", "waiver_reason": null, "substatus": null, "evidence_label_contribution": null}
  ],
  "rule_compliance": { "...": "..." },
  "rule_required_by_objective": {"rule_7": false, "rule_9": false},
  "ac_deltas": {"AC-3": {"from": "pending", "to": "passed"}},
  "objective_id": "metric_extractor_t7",
  "objective_hash": "...",
  "manifest_hash": "...",
  "terminal_reason": null
}
```

### Field Notes

- `transition` enumerates the gated/logged-only call site:
  `next_round`, `review_fix`, `enter_finalize`, `finalize_completion`,
  `stop_marker`, `maxiter`, `mainline_drift`, `review_start`,
  `complete_at_maxiter`.
- `mode` is one of `gated`, `logged_only`, `skipped_no_adapter`, `blocked`.
- `engine_exit_code` is the integer the Python engine returned to the bash
  wrapper (`0`, `1`, or `2`).
- `block_reason` is `null` on success and one of the documented enum strings
  on hard-block (e.g. `correctness_failed`, `required_surface_failed:nvbit`,
  `required_latency_threshold_breach`,
  `leaderboard_comparable_required_but_unknown`, `rule_violation:rule_1_no_ncu`,
  `rule_not_evaluated:rule_7_default_stream`, `sidecar_identity_mismatch:round`,
  `sidecar_identity_invalid:missing_<field>`, `sidecar_identity_stale`,
  `adapter_active_sidecar_absent`, `sidecar_malformed`).
- `terminal_reason` is set on logged-only terminal transitions to one of
  `complete`, `stop`, `maxiter`, `complete_at_maxiter`, `mainline_drift`,
  `review_start`; otherwise `null`.

## Exit Code Contract

| Engine exit | Semantic | Wrapper Behaviour |
|-------------|----------|-------------------|
| 0 | continue | Emit JSONL row; proceed with original state mutation. |
| 1 | hard-block | Emit JSONL row; refuse to mutate state.md, refuse to rename / create round artifacts. |
| 2 | soft-warn | Emit JSONL row with `verdict_warned=true`; proceed with state mutation; drift counter increments. |

Logged-only transitions ignore exit codes 1 / 2 (no hard-block) but always
write the JSONL row.

## Adapter Detection

The engine fail-opens (emits a `mode=skipped_no_adapter` row, exit 0) only if
ALL three predicates are negative:

1. `.humanize/adapter-config.json` absent OR does not declare a known adapter.
2. `.claude/knowledge/problems/` absent OR contains zero `.md` files.
3. No `round-<N>-objectives.json` file in the loop directory.

If ANY predicate is positive, the adapter is considered "active" and the
engine fail-closes on absent / malformed sidecars.

## Worked Example

A round where the test suite passes, latency stays advisory, the SOL score is
genuinely unknown (no leaderboard requirement), and all known rules verify.

`.humanize/rlcr/2026-05-15_22-01-15/round-1-objectives.json`:

```json
{
  "schema_version": "1.0",
  "loop_id": "2026-05-15_22-01-15",
  "round": 1,
  "adapter": "solbench",
  "objective_id": "metric_extractor_t7",
  "objective_hash": "deadbeef...",
  "manifest_path": ".humanize/rlcr/2026-05-15_22-01-15/round-1-manifest.json",
  "manifest_hash": "cafef00d...",
  "generated_at": "2026-05-15T22:30:00Z",
  "correctness": {"passed": true, "tests_passed": 9, "tests_failed": 0},
  "latency": {"required": false, "delta_pct": -2.1, "threshold_pct": null, "basis": "cupti_activity"},
  "sol_score": {
    "value": "unknown_t_sol",
    "provenance": {
      "authority": "local_proxy_5060",
      "basis": "solar_stage4_missing",
      "leaderboard_comparable": false
    }
  },
  "leaderboard_comparable_required": false,
  "required_surfaces": ["nvbit", "cupti_activity"],
  "ac_deltas": {"AC-3": {"from": "pending", "to": "passed"}},
  "rule_compliance": {
    "rule_1_no_ncu": {"status": "verified", "evidence": "manifest.host.tools.ncu==absent"},
    "rule_2_no_clock_lock": {"status": "verified", "evidence": "clock-lock helper not invoked"},
    "rule_3_no_privileged_cupti": {"status": "verified", "evidence": "no privileged cupti calls in transcript"},
    "rule_4_no_host_driver_work": {"status": "verified", "evidence": "host code path uses public driver only"},
    "rule_5_submission_language": {"status": "verified", "evidence": "submission is CUDA/C++"},
    "rule_6_no_evaluator_state_exploit": {"status": "not_evaluated", "evidence": null},
    "rule_7_default_stream": {"status": "not_evaluated", "evidence": null},
    "rule_8_precision_contract": {"status": "verified", "evidence": "fp32 reference accepted"},
    "rule_9_iiswc_no_access": {"status": "not_evaluated", "evidence": null}
  },
  "rule_required_by_objective": {"rule_7": false, "rule_9": false}
}
```

Outcome: engine exit 0, JSONL row appended with `mode=gated`,
`verdict_blocked=false`, `engine_exit_code=0`. Wrapper proceeds with the
original `upsert_state_fields` call.
