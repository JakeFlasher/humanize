# Install JakeShea Humanize RLCR for Codex

The plugin is distributed from `plugins/jakeshea-humanize-rlcr` through the
repo marketplace in `.agents/plugins/marketplace.json`.

Its personal namespace is intentionally distinct from upstream Humanize:

- Plugin ID: `jakeshea-humanize-rlcr`
- Marketplace ID: `jakeshea-humanize`
- Skill selector: `$jakeshea-humanize-rlcr:humanize-rlcr`

## Requirements

- A current Codex CLI with plugins and hooks.
- Account or workspace access to `gpt-5.6-sol` with `xhigh` reasoning.
- Git and Python 3.10 or newer.
- Windows: the standard Python Launcher providing `py -3`.

The controller probes the required `codex exec` flags before creating a run and
never silently substitutes another reviewer model or effort.

## Install from a local checkout

```bash
codex plugin marketplace add /absolute/path/to/humanize
codex plugin add jakeshea-humanize-rlcr@jakeshea-humanize
```

Verify:

```bash
codex plugin marketplace list
codex plugin list --json
```

Open a new Codex conversation, run `/hooks`, inspect and trust the Humanize
`Stop` hook, then start a loop:

```text
$jakeshea-humanize-rlcr:humanize-rlcr implement path/to/plan.md
```

The repository must be clean when the loop starts and at every review boundary.
Codex should run relevant tests and commit intended changes before stopping.

## Install on another machine

The current branch must first be committed and pushed. Then either add the Git
repository directly:

```bash
codex plugin marketplace add JakeFlasher/humanize --ref for-codex-plugin-only
codex plugin add jakeshea-humanize-rlcr@jakeshea-humanize
```

Or clone it and register the local path:

```bash
git clone --branch for-codex-plugin-only https://github.com/JakeFlasher/humanize.git
codex plugin marketplace add /absolute/path/to/humanize
codex plugin add jakeshea-humanize-rlcr@jakeshea-humanize
```

If the fork is pushed under another owner or branch, substitute those values.

## Defaults and bounds

- Reviewer lanes: specification and correctness.
- Reviewer model: `gpt-5.6-sol`.
- Reasoning effort: `xhigh`.
- Successful review rounds: 6 by default, hard maximum 12.
- Reviewer timeout: 15 minutes by default and maximum.
- Total wall time: 2 hours by default, hard maximum 24 hours.
- Infrastructure failures: maximum 3.
- Unchanged rejected artifacts reuse their cached correction packet.

The skill can pass smaller values through `--max-rounds`, `--review-timeout`,
and `--max-minutes`.

## Manual controller commands

From a source checkout:

```bash
python3 plugins/jakeshea-humanize-rlcr/scripts/rlcr.py start --plan docs/plan.md
python3 plugins/jakeshea-humanize-rlcr/scripts/rlcr.py step
python3 plugins/jakeshea-humanize-rlcr/scripts/rlcr.py status
python3 plugins/jakeshea-humanize-rlcr/scripts/rlcr.py cancel --reason "user canceled"
python3 plugins/jakeshea-humanize-rlcr/scripts/rlcr.py resume
```

`step` exits `0` on acceptance, `10` when corrections remain, and `20` for a
controller or infrastructure block.

Private state defaults to:

```text
${JAKESHEA_HUMANIZE_RLCR_STATE_HOME:-${XDG_STATE_HOME:-~/.local/state}/jakeshea-humanize-rlcr}
```

Set `JAKESHEA_HUMANIZE_RLCR_STATE_HOME` to override it. Starting or mutating a run may
require a Codex approval to write outside the project; reviewer children remain
read-only.

## Update

Local and Git marketplace installs are cached snapshots. Refresh the marketplace
when applicable, reinstall, and start a new conversation:

```bash
codex plugin marketplace upgrade jakeshea-humanize
codex plugin add jakeshea-humanize-rlcr@jakeshea-humanize
```

For a local-path marketplace, pull or edit the checkout before reinstalling;
`marketplace upgrade` is only needed for Git-backed sources. If the hook changed,
review and trust its new hash through `/hooks`.

## Remove

```bash
codex plugin remove jakeshea-humanize-rlcr@jakeshea-humanize
codex plugin marketplace remove jakeshea-humanize
```

Removal leaves private run history intact. Delete that history separately only
when you intentionally want to destroy the audit record.

## Limitations

- The reviewers use fresh contexts but the same model family.
- Read-only Codex sandboxes have broader read visibility than sealed snapshots.
- The CLI pins the requested model and effort, but does not expose a portable
  effective-model reroute attestation.
- Strict clean-boundary mode currently rejects repositories with submodules,
  local Git config includes, or per-worktree Git config.
