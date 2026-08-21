# JakeShea Humanize RLCR

A personal, Codex-only fork of Humanize for bounded plan implementation with
independent review loops.

This repository is not the official PolyArch Humanize distribution. Its Codex
identities are deliberately namespaced to avoid collisions with any future
official plugin:

- Plugin: `jakeshea-humanize-rlcr`
- Marketplace: `jakeshea-humanize`
- Skill: `$jakeshea-humanize-rlcr:humanize-rlcr`

## What RLCR does

RLCR means **Ralph Loop with Codex Review**. Codex implements a concrete plan,
then a native `Stop` hook asks two fresh `gpt-5.6-sol:xhigh` contexts to review
the same committed artifact:

- **Specification lane**: plan, acceptance criteria, scope, behavior, and test
  evidence.
- **Correctness lane**: defects, security, regressions, state/error handling,
  portability, and meaningful test gaps.

Both lanes must accept the same immutable artifact digest. Blocking findings
become a continuation prompt; Codex corrects them, commits, and stops again.
The loop is bounded by review rounds, calls, failures, timeouts, and wall time.

## Requirements

- Current Codex CLI with plugins, hooks, structured output, and
  `gpt-5.6-sol:xhigh` access.
- Git.
- Python 3.10 or newer. Windows hooks use the standard `py -3` launcher.

## Install this checkout

```bash
codex plugin marketplace add /absolute/path/to/humanize
codex plugin add jakeshea-humanize-rlcr@jakeshea-humanize
```

Open a new Codex conversation, run `/hooks`, inspect and trust the bundled
hook, then invoke:

```text
$jakeshea-humanize-rlcr:humanize-rlcr implement path/to/plan.md
```

For installation from another machine, update, removal, and troubleshooting,
see [Install for Codex](docs/install-for-codex.md).

## Repository layout

```text
.agents/plugins/marketplace.json       Repo marketplace
plugins/jakeshea-humanize-rlcr/
├── .codex-plugin/plugin.json          Plugin identity and UI metadata
├── hooks/hooks.json                   Native synchronous Stop hook
├── controller/                        State machine, review runner, storage
├── prompts/                           Independent reviewer lane prompts
├── schemas/review-v1.json             Structured reviewer output contract
├── scripts/rlcr.py                    Direct controller entrypoint
└── skills/humanize-rlcr/              Explicit Codex skill and wrapper
tests/test-codex-native-rlcr.py         Protocol, concurrency, and safety tests
```

The plugin is self-contained and uses only the Python standard library plus the
Codex and Git executables. Authoritative run state is stored privately under:

```text
${JAKESHEA_HUMANIZE_RLCR_STATE_HOME:-${XDG_STATE_HOME:-~/.local/state}/jakeshea-humanize-rlcr}
```

## Development

```bash
python3 tests/test-codex-native-rlcr.py
python3 -m json.tool .agents/plugins/marketplace.json >/dev/null
```

Validate with Codex's built-in `skill-creator` and `plugin-creator` validators
before reinstalling. Local installs are cached snapshots, so reinstall and use
a new conversation after every source update:

```bash
codex plugin add jakeshea-humanize-rlcr@jakeshea-humanize
```

## Security boundaries

- Reviewers are ephemeral, read-only, approval-free, strict-configured, and
  have hooks, subagents, and web search disabled.
- Repository Git helpers, filters, text conversion, filesystem monitors,
  config includes, per-worktree config, and lazy fetching are blocked or
  neutralized before trusted Git reads.
- Plan, schema, prompt, manifest, and cumulative patch snapshots are integrity
  checked before and after review.
- Hooks are guardrails, not a complete isolation boundary. The two reviewers
  use independent contexts but the same model family; deterministic tests and
  human judgment remain necessary.
- Strict mode currently blocks repositories containing Git submodules.

See [Architecture](docs/architecture.md) for the state machine and trust model.

## Provenance and license

This personal fork descends from PolyArch Humanize, which itself credits the
GAAC project. The prior Claude Code implementation was removed from this branch
after the Codex-native plugin became self-contained; it remains recoverable in
Git history. See [NOTICE](NOTICE.md).

Licensed under the [MIT License](LICENSE).
