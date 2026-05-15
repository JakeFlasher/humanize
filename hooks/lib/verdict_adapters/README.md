# Verdict Adapters

This directory reserves the extension seam for future verdict-engine adapters.
In v1 the artifact verdict engine
(`hooks/lib/solbench_verdict_engine.py`) ships solbench-specific behaviour
only; the `--adapter` CLI flag is intentionally NOT exposed externally.

## Why The Seam Exists

The engine reads a structured sidecar
(`.humanize/rlcr/<loop>/round-<N>-objectives.json`) plus a project-specific
manifest at `sidecar.manifest_path`. The sidecar schema, the manifest shape,
and the severity partition for hard-block versus ledger-only verdicts are all
adapter-specific. A future adapter (for example, a sibling SOL-style harness
with different counter-free surfaces or a different rule taxonomy) would
plug in here without modifying the engine's gating, JSONL emission, or
exit-code contract.

## Contract Sketch (Reserved For v2)

A future adapter SHOULD provide:

1. A `detect()` predicate that returns `True` when this adapter applies to
   the current loop. The engine resolves adapter detection internally; the
   external CLI surface stays adapter-agnostic.
2. A `validate_sidecar(sidecar_obj)` function that raises a specific
   exception when required fields are missing or malformed. The engine maps
   raised exceptions to documented `block_reason` strings.
3. A `read_manifest(path)` function that returns a list of surface entries,
   each carrying `{name, status, waiver_reason, substatus,
   evidence_label_contribution}` or equivalent normalized shape.
4. A `compute_verdict(sidecar, manifest)` function that returns a tuple of
   (exit_code, block_reason, computed_verdict_label, warned_flag) following
   the contract documented in `docs/solbench-verdict-engine-schema.md`.
5. A `rule_severity_partition` table classifying each rule into one of:
   `always_hard_block_on_violated`, `conditional_hard_block`, or
   `ledger_only_unless_required`.

## v1 Status

In v1 the solbench adapter logic lives inside
`hooks/lib/solbench_verdict_engine.py` and is gated by an internal
`detect_adapter()` function. The seam in this directory is documentation
only; there is no separate adapter module yet.

The plan that produced this seam (artifact-verdict-engine v1) explicitly
defers the full multi-adapter split to a follow-up plan after the v1 engine
has accumulated usage data.

## Do Not

- Do NOT add a `--adapter` CLI flag to `solbench_verdict_engine.py`. The
  AC-12 negative test asserts the flag is absent.
- Do NOT introduce a new top-level adapter module before v2; doing so
  expands the public surface without a tested extension contract.
- Do NOT rename this directory; the test suite (AC-12) greps for
  `hooks/lib/verdict_adapters/README.md`.
