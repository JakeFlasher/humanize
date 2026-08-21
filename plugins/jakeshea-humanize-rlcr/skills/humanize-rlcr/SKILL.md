---
name: humanize-rlcr
description: Run a bounded RLCR plan-implementation loop in Codex with two independent, read-only GPT-5.6 Sol reviewers at xhigh reasoning. Use when the user asks to implement a plan iteratively, run Humanize or RLCR, gate completion on independent review, continue correcting findings until acceptance, inspect loop status, resume a blocked review, or cancel an active loop. Do not invoke implicitly for ordinary one-pass coding or review requests.
---

# Humanize RLCR

Use the bundled controller and native Codex `Stop` hook. Run the
`scripts/rlcr.py` resource next to this `SKILL.md` by its absolute path from
Codex's loaded skill location. Do not rely on `PLUGIN_ROOT`; Codex injects that
variable into plugin hook commands, not ordinary skill shell commands.

## Start an implementation loop

1. Require a concrete plan file. If the user has not supplied one, ask for its
   path or create the plan only when they explicitly requested planning too.
2. Run:

   ```bash
   python3 "<this-skill-directory>/scripts/rlcr.py" start --plan <repo-relative-plan>
   ```

   Add `--base <ref>` only when the user wants the cumulative review scope to
   include changes already present relative to that ref. Pass requested bounds
   through `--max-rounds`, `--review-timeout`, or `--max-minutes`.
3. If setup fails, report the exact failure and stop. Do not weaken the model,
   effort, sandbox, clean-tree, plan, or budget checks.
4. Implement the plan. Keep scope tied to the plan and run proportionate tests.
5. Before ending each correction round, make the repository clean by committing
   the intended changes. Do not push unless the user separately requested it.
6. Stop normally. The bundled hook launches both reviewers in parallel. If it
   continues the turn, read the correction packet, address every blocking
   finding, rerun validation, commit, and stop again.
7. Treat acceptance as valid only when the controller reports `ACCEPTED`. Never
   infer acceptance from reviewer prose or silence.

## Manual and recovery commands

Run the same deterministic controller when hooks are disabled or when the user
asks for status:

```bash
python3 "<this-skill-directory>/scripts/rlcr.py" step
python3 "<this-skill-directory>/scripts/rlcr.py" status
python3 "<this-skill-directory>/scripts/rlcr.py" cancel --reason "<user reason>"
python3 "<this-skill-directory>/scripts/rlcr.py" resume
```

`step` exits `10` when corrections remain, `0` on acceptance, and `20` for a
controller or infrastructure block. Read its printed packet before proceeding.

## Invariants

- Reviewers are always fresh `gpt-5.6-sol` processes with `xhigh` reasoning,
  read-only sandboxing, no subagents, no nested hooks, and JSON-schema output.
- Both the specification lane and correctness lane must accept the same Git
  artifact digest. Any blocking finding rejects the round.
- Do not edit controller state, reviewer results, prompt snapshots, or journals.
- Do not retry an unchanged rejected commit; the controller deliberately reuses
  its cached verdict.
- Do not bypass a `BLOCKED`, `EXHAUSTED`, or `CANCELED` state. Report it to the
  user. Resume only after the underlying infrastructure issue is corrected.
- Reviewer output is advisory evidence, not permission to expand scope. Ask the
  user when a finding conflicts with the plan or requires a material choice.
