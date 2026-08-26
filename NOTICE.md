# Provenance notice

This repository is a personal Codex-only fork maintained by JakeShea. It is not
the official PolyArch Humanize plugin and uses distinct Codex plugin and
marketplace identifiers to avoid implying affiliation or ownership.

The repository descends from:

- `PolyArch/humanize`, an iterative development harness distributed as MIT in
  its upstream plugin metadata.
- GAAC (GitHub-as-a-Context), credited by the upstream Humanize project.

The v2 redesign also studied `humanfia/humanize2` at commit
`72bfb030d423eaecb2ec7589c000483320cfc95e` (Apache-2.0) on 2026-08-26. Its
layering, flow/session, cycle persistence, resuming, reporting, tracing, and
security documentation informed the architecture decision in
`docs/adr/0001-humanize2-pattern-adoption.md`. No Humanize2 source code,
assets, dependency, or executable flow was copied into this plugin; the v2
controller is an independent MIT implementation of selected public design
ideas.

The former Claude Code, Kimi, Gemini, BitLesson, monitoring, and dashboard
implementation was removed from the `for-codex-plugin-only` branch after the
native Codex plugin became self-contained. Original authorship and the complete
removed source remain available in Git history.
