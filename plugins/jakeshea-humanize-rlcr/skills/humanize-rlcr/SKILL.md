---
name: humanize-rlcr
description: Run a bounded plan-implementation-correction loop in Codex with two fresh, independent, read-only GPT-5.6 Sol reviewers at xhigh reasoning. Use when the user explicitly asks to implement a concrete plan with Humanize or RLCR, require independent acceptance before completion, continue correcting review findings, inspect a loop, attach test evidence, transfer a loop to a new session, resume a blocked review, or cancel it. Do not invoke implicitly for ordinary coding or one-pass review.
---

# Humanize RLCR

Use the `scripts/rlcr.py` next to this skill by absolute path. Do not rely on
`PLUGIN_ROOT`; it is available to plugin hooks, not ordinary skill commands.

## Run the loop

1. Require a concrete repository plan. When available, prefer an
   `rlcr.plan.v1` contract and named deterministic checks; read
   [plan-contract.md](references/plan-contract.md).
2. Start the bounded run:

   ```bash
   python3 "<skill>/scripts/rlcr.py" start --plan <path> [--contract <path>]
   ```

   Pass explicit user bounds and base refs through unchanged. Read
   [operations.md](references/operations.md) for supported controls.
3. If setup fails, report the exact failure. Never weaken model, effort,
   sandbox, Git, snapshot, schema, evidence, or budget checks.
4. Implement only the captured plan. Run proportionate validation, commit the
   intended changes, and leave the worktree clean.
5. Attach every required check at that commit with `evidence run`.
6. Stop normally. The native Stop hook runs both reviewer lanes concurrently.
   If Codex continues, address every blocking finding, rerun checks, commit, and
   stop again.
7. Finish only when the controller reports `ACCEPTED`. Reviewer prose, silence,
   or a single accepting lane is never sufficient.

When hooks are disabled, invoke `step` manually. It exits `0` on acceptance,
`10` when work remains, and `20` on a controller or infrastructure block.

## Preserve the boundary

- Do not edit private controller state, snapshots, evidence, reviewer outputs,
  packets, or event journals.
- Do not retry an unchanged rejected artifact; the controller reuses its
  artifact and lane caches.
- Do not bypass `BLOCKED`, `EXHAUSTED`, or `CANCELED`. Report the precise state.
- Use `adopt` explicitly before a different Codex session takes ownership.
- Ask the user when a finding conflicts with the plan or needs a material scope
  decision.
