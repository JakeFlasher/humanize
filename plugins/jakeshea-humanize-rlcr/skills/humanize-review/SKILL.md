---
name: humanize-review
description: Run a bounded one-pass independent specification and correctness review of clean committed changes against an existing plan. Use when the user explicitly asks for a Humanize audit, dual-lane review, release gate, or review-only assessment without authorizing fixes. Do not use for implementation loops; use humanize-rlcr when corrections should continue automatically.
---

# Humanize Review

Use the `scripts/rlcr.py` next to this skill by absolute path.

1. Require a concrete plan and a known ancestor base for the intended review
   scope. Use the user's base or an unambiguous branch upstream; do not guess
   across materially different commit ranges.
2. Require a clean committed worktree. If tests are required, run and attach
   them as evidence only when the user authorized running those commands.
3. Start exactly one review round:

   ```bash
   python3 "<skill>/scripts/rlcr.py" start --plan <path> --base <ref> --max-rounds 1 [--contract <path>]
   python3 "<skill>/scripts/rlcr.py" step
   ```

4. On acceptance, report the artifact commit, both lane verdicts, evidence, and
   residual risks. On exit `10`, read the correction packet and report findings
   in severity order with paths and acceptance tests. Do not implement fixes
   unless the user separately asks.
5. After a findings-only review, cancel the still-correcting run with reason
   `one-pass review completed` so its Stop hook cannot capture unrelated work.
6. Never downgrade the pinned reviewer configuration or claim consensus from a
   single lane, malformed output, stale commit, or missing required evidence.

If another RLCR run is active, report it rather than replacing or adopting it.
