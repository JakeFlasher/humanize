---
name: humanize-plan
description: Create an implementation-ready repository plan and optional rlcr.plan.v1 contract for a later Humanize RLCR run. Use when the user explicitly asks to plan a substantial change, turn requirements into controller-enforced acceptance criteria, define deterministic evidence checks, or prepare a plan before implementation. Do not start implementation unless the user also asks for it.
---

# Humanize Plan

Produce a plan that another Codex session can implement without guessing.

1. Inspect relevant code, tests, interfaces, repository guidance, and current
   behavior. Resolve important unknowns before committing to architecture.
2. Write a Markdown plan containing outcome, current-state evidence, non-goals,
   ordered implementation slices, affected interfaces/data, migration and
   compatibility notes, risks, validation, rollback, and uniquely named
   acceptance criteria.
3. For a substantial or high-risk change, create a sibling `rlcr.plan.v1` JSON
   contract using [contract-template.md](references/contract-template.md).
   Make each required criterion observable and associate deterministic check
   names where possible.
4. Validate the contract with the skill-local controller:

   ```bash
   python3 "<skill>/scripts/rlcr.py" contract validate --contract <path>
   ```

5. Re-read the plan against the request and repository evidence. Remove vague
   steps, circular criteria, hidden product choices, and checks that cannot be
   run at a clean commit.
6. Return the plan and contract paths, unresolved decisions, and the exact
   checks expected during implementation. Start `$jakeshea-humanize-rlcr:humanize-rlcr`
   only if implementation is also in scope.
