# Plan and contract template

Use this Markdown structure:

```text
# <Outcome>
## Context and current evidence
## Goals and non-goals
## Interfaces and compatibility
## Implementation slices
## Data or state migration
## Validation and evidence
## Risks and rollback
## Acceptance criteria
```

Pair it with strict JSON:

```json
{
  "schema_version": "rlcr.plan.v1",
  "goal": "One observable outcome",
  "criteria": [
    {
      "id": "AC-1",
      "description": "An independently verifiable requirement",
      "required": true,
      "required_checks": ["unit-tests"]
    }
  ]
}
```

Use stable criterion IDs in both files. A required check name is an evidence
label, not a shell command; the later implementation run supplies its explicit
argv through `evidence run`.
