# Structured plan contract

An `rlcr.plan.v1` file makes required criteria and deterministic checks
controller-enforced instead of relying only on reviewer prose:

```json
{
  "schema_version": "rlcr.plan.v1",
  "goal": "Describe the intended outcome.",
  "criteria": [
    {
      "id": "AC-1",
      "description": "State one observable requirement.",
      "required": true,
      "required_checks": ["unit-tests"]
    }
  ]
}
```

Criterion IDs must be unique. Check names use letters, digits, dots, dashes,
and underscores. Validate before starting:

```bash
python3 "<skill>/scripts/rlcr.py" contract validate --contract <path>
```
