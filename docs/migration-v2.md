# Migrating from RLCR v1 to v2

RLCR v2 is an in-place plugin upgrade. It does not move or delete private run
history.

## Before updating

Finish or cancel an active v1 run. The controller pins its runtime digest, so an
active run correctly refuses to continue after plugin code changes. Do not edit
the private state file to bypass this check.

Record the current state if needed:

```bash
python3 plugins/jakeshea-humanize-rlcr/scripts/rlcr.py status --json
python3 plugins/jakeshea-humanize-rlcr/scripts/rlcr.py report --format markdown
```

## Update behavior

- V1 snapshots are read through a one-way, in-memory `rlcr.run.v1` to
  `rlcr.run.v2` migration. The original file is not rewritten merely by being
  inspected.
- New runs capture an immutable run configuration and use hash-chained event
  files as the recovery authority.
- The manifest is now the sole plugin-version source.
- Existing terminal history remains available through `history`, `show`,
  `report`, and `export`.

## Starting a v2 run

Optionally validate a structured contract, then start:

```bash
python3 plugins/jakeshea-humanize-rlcr/scripts/rlcr.py contract validate \
  --contract docs/plan-contract.json
python3 plugins/jakeshea-humanize-rlcr/scripts/rlcr.py start \
  --plan docs/plan.md \
  --contract docs/plan-contract.json \
  --require-check unit-tests
```

After implementation and a clean commit, attach the required check:

```bash
python3 plugins/jakeshea-humanize-rlcr/scripts/rlcr.py evidence run \
  --name unit-tests -- python3 tests/test-codex-native-rlcr.py
```

## New operational commands

- `adopt`: release a non-reviewing run from its bound session so the next Stop
  hook can bind explicitly.
- `history` and `show --run`: inspect repository run history.
- `report`: render portable Markdown or JSON.
- `export`: produce a Chrome/Perfetto event trace.
- `cancel`: now terminates only registered reviewer process groups whose kernel
  identity matches the active attempt, with a tombstone preventing a reviewer
  from launching across a concurrent cancellation.

If a v2 run reports snapshot, event-chain, evidence, ancestry, or configuration
integrity failure, preserve its run directory for diagnosis and start a new run
only after understanding the failure.
