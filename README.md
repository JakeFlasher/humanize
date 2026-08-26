# JakeShea Humanize RLCR

A personal, Codex-only fork of Humanize for bounded plan implementation with
independent review loops. Version 2 selectively adopts Humanize2's layered
run/cycle, resumability, focused-flow, and traceability patterns while keeping a
smaller fail-closed Codex plugin boundary.

This repository is not the official PolyArch Humanize distribution. Its Codex
identities are deliberately namespaced to avoid collisions with any future
official plugin:

- Plugin: `jakeshea-humanize-rlcr`
- Marketplace: `jakeshea-humanize`
- Skill: `$jakeshea-humanize-rlcr:humanize-rlcr`
- Planning skill: `$jakeshea-humanize-rlcr:humanize-plan`
- Review-only skill: `$jakeshea-humanize-rlcr:humanize-review`

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
The loop is bounded by review rounds, calls, failures, timeouts, wall time, and
measured Codex token usage.

RLCR v2 additionally provides:

- optional `rlcr.plan.v1` contracts whose required criteria are checked by the
  controller;
- named deterministic evidence bound to the exact candidate commit;
- independent per-lane caching, so a valid lane is not rerun when its peer
  fails transiently;
- typed failures, safe live-review cancellation, explicit session adoption,
  and one-way v1 history migration;
- recoverable hash-chained events, run history, Markdown/JSON reports, and
  Chrome/Perfetto traces.

## Requirements

- Current Codex CLI with plugins, hooks, structured output, and
  `gpt-5.6-sol:xhigh` access.
- Git.
- Python 3.10 or newer. Windows hooks use the standard `py -3` launcher.

## Install or upgrade the plugin

The plugin is published by this repository's `jakeshea-humanize` marketplace.
The current release has version prefix `0.2.0+codex.`; the suffix is a cache
stamp that changes whenever the local plugin is rebuilt.

### First install from a local checkout

Register the repository marketplace once, then install the plugin:

```bash
codex plugin marketplace add /absolute/path/to/humanize
codex plugin add jakeshea-humanize-rlcr@jakeshea-humanize
```

Verify that Codex reports it as installed and enabled:

```bash
codex plugin list --json
```

### First install directly from GitHub

```bash
codex plugin marketplace add JakeFlasher/humanize --ref for-codex-plugin-only
codex plugin add jakeshea-humanize-rlcr@jakeshea-humanize
```

### Upgrade an existing installation

Finish or cancel an active RLCR run before upgrading because an in-flight run
intentionally refuses changed controller semantics. For a local checkout, pull
or edit the source and reinstall its cache-stamped snapshot:

```bash
git pull --ff-only
codex plugin add jakeshea-humanize-rlcr@jakeshea-humanize
codex plugin list --json
```

Do not add the marketplace again if `codex plugin marketplace list` already
shows `jakeshea-humanize`. For a Git-backed marketplace, refresh it first:

```bash
codex plugin marketplace upgrade jakeshea-humanize
codex plugin add jakeshea-humanize-rlcr@jakeshea-humanize
```

After either installation or upgrade, open a **new Codex conversation** so the
new skills and hook snapshot are loaded. Run `/hooks`, inspect and trust the
bundled Stop hook, then invoke one of the explicit skills:

```text
$jakeshea-humanize-rlcr:humanize-rlcr implement path/to/plan.md
$jakeshea-humanize-rlcr:humanize-plan create a plan and contract for this change
$jakeshea-humanize-rlcr:humanize-review review this committed change against path/to/plan.md
```

For installation from another machine, update, removal, and troubleshooting,
see [Install for Codex](docs/install-for-codex.md).

## Repository layout

```text
.agents/plugins/marketplace.json       Repo marketplace
plugins/jakeshea-humanize-rlcr/
├── .codex-plugin/plugin.json          Plugin identity and UI metadata
├── hooks/hooks.json                   Native synchronous Stop hook
├── controller/                        Layered state, policy, evidence, review, reporting
├── prompts/                           Independent reviewer lane prompts
├── schemas/                           Review and plan-contract schemas
├── scripts/rlcr.py                    Direct controller entrypoint
└── skills/                             Plan, review-only, and full-loop skills
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
ruff check .
ruff format --check .
pyright
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
- Start commit ancestry is enforced, and plan contract, run configuration,
  evidence manifest, and cumulative patch all contribute to artifact identity.
- Evidence commands run as explicit argv without a shell, at a clean commit;
  failed, stale, missing, or tampered records cannot authorize review.
- Live cancellation uses attempt-scoped process registrations and
  PID-reuse-resistant kernel identities before signaling a process group.
- Hooks are guardrails, not a complete isolation boundary. The two reviewers
  use independent contexts but the same model family; deterministic tests and
  human judgment remain necessary.
- Strict mode currently blocks repositories containing Git submodules.

See [Architecture](docs/architecture.md) for the state machine and trust model,
[the Humanize2 adoption ADR](docs/adr/0001-humanize2-pattern-adoption.md) for
the merge decision, [research foundations](docs/research-foundations.md) for
the paper-to-control mapping, and [the v2 migration guide](docs/migration-v2.md)
for upgrades.

## Provenance and license

This personal fork descends from PolyArch Humanize, which itself credits the
GAAC project. Humanize2 was studied at a pinned Apache-2.0 revision, but no
Humanize2 source was copied or linked into this MIT plugin. See
[NOTICE](NOTICE.md).

Licensed under the [MIT License](LICENSE).
