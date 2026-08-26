# RLCR operations

Use the skill-local `scripts/rlcr.py` with Python 3.

## Start options

- `--contract <path>` captures an optional `rlcr.plan.v1` contract.
- `--require-check <name>` requires same-commit evidence; repeat as needed.
- `--base <ref>` expands cumulative review scope from a known ancestor.
- `--max-rounds`, `--review-timeout`, and `--max-minutes` bound work.
- `--max-input-tokens`, `--max-output-tokens`, and
  `--max-reasoning-tokens` bound measured reviewer usage.

## Evidence

Run a check without shell interpretation and attach its result to the current
commit:

```bash
python3 "<skill>/scripts/rlcr.py" evidence run --name unit-tests -- python3 -m unittest
```

Evidence is invalidated by a new commit. A failed or tampered record never
satisfies a required check.

## Recovery and inspection

```bash
python3 "<skill>/scripts/rlcr.py" status
python3 "<skill>/scripts/rlcr.py" history
python3 "<skill>/scripts/rlcr.py" show --run <run-id>
python3 "<skill>/scripts/rlcr.py" adopt
python3 "<skill>/scripts/rlcr.py" resume
python3 "<skill>/scripts/rlcr.py" cancel --reason "<reason>"
python3 "<skill>/scripts/rlcr.py" report --format markdown
python3 "<skill>/scripts/rlcr.py" export --output rlcr-trace.json
```

Use `adopt` only to transfer a non-reviewing run from its bound Codex session.
Use `resume` only after correcting the infrastructure condition named by a
blocked run.
