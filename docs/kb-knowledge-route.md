# CACG Knowledge Auto-Routing

Humanize can consume a [CACG](https://github.com/) "Content-Addressable Card
Graph" knowledge base — a deck of hash-pinned Markdown knowledge cards exported
from a CACG repo — and surface the relevant cards to the RLCR loop automatically.

This complements the existing **Knowledge Provenance** contract (the
`## Knowledge Consulted` section in every round summary). Auto-routing is the
*input* side (here are cards that may help); provenance is the *output* side
(here are the cards I actually used).

## Exporting a knowledge base into your project

In the CACG repo, run the tiered exporter against your project directory:

```bash
# in the CACG knowledge-base repo
scripts/export-knowledge.sh /path/to/your/project              # tier=query (~14 MB): browse + search
scripts/export-knowledge.sh /path/to/your/project --tier verify  # + chunks_manifest (enables kb verify)
```

This writes a self-contained export to `<project>/.claude/knowledge/`:

```
.claude/knowledge/
  INDEX.md                 card index / primer
  cards/<deck>/<reading_id>/<card_id>.md (+ .history.jsonl)
  out/<deck>/source_matrix.json, summaries.json, cards_manifest.json, INDEX.md, ...
  kb-query.sh              wrapper: ./kb-query.sh search "<topic>" | show <card_id> | verify <file>
  EXPORT_MANIFEST.json     receipt (sizes, sha256, source commit, kb version)
```

`.claude/knowledge/` is the default location the RLCR prompts look for a primer.

## Configuration

The auto-routing hook reads these keys from the merged config hierarchy
(`config/default_config.json` → `~/.config/humanize/config.json` →
`.humanize/config.json`):

| Key | Default | Meaning |
|-----|---------|---------|
| `kb_enabled` | `false` | Master opt-in. When `false`, the hook is a complete no-op. |
| `kb_root` | `""` | KB root override. If empty, discovery tries `<project>/.claude/knowledge` then `<project>/.humanize/kb`. Relative paths resolve against the project root. |
| `kb_deck` | `"cfa"` | Deck name (the `<deck>` segment under `cards/` and `out/`). |
| `kb_top_k` | `5` | Number of cards to inject. Values `<1` or non-numeric fall back to 5. |
| `kb_search_bin` | `"kb"` | The CACG `kb` binary (name on `PATH` or absolute path). |

Enable it per project with `.humanize/config.json`:

```json
{ "kb_enabled": true, "kb_deck": "cfa", "kb_top_k": 5 }
```

## How it works

`hooks/kb-knowledge-route.sh` is registered as a `UserPromptSubmit` hook. On each
prompt, when `kb_enabled` is true and a KB + `kb` binary are present, it runs a
**deterministic** `kb search` over the prompt:

```bash
kb search "<prompt>" --json \
  --source-matrix .claude/knowledge/out/<deck>/source_matrix.json \
  --summaries     .claude/knowledge/out/<deck>/summaries.json \
  --top-k <kb_top_k>
```

and injects the top matches as `hookSpecificOutput.additionalContext`. There is
no LLM call and no provider routing — it is a fast, side-effect-free retrieval.

The injected block is framed as **search suggestions only**. It is *not* a
provenance section: the implementer should open a card (`kb show <card_id>`) and
cite it in `## Knowledge Consulted` only if it was actually used.

Every failure path — disabled, no KB, no binary, no `jq`, malformed config,
search error — is a clean no-op (empty stdout, exit 0). The hook never blocks a
prompt.

## Content-verifying round summaries

When a `verify`-tier export is present (so `chunks_manifest.json` is available),
the CACG repo's `kb verify --round-summary` can content-check the cards cited in
a round summary — on top of humanize's existing "section exists / lists real
paths" check:

```bash
kb verify --round-summary <round-summary.md> \
  --source-matrix   .claude/knowledge/out/<deck>/source_matrix.json \
  --chunks-manifest .claude/knowledge/out/<deck>/chunks_manifest.json
```

Exit `0` = the `## Knowledge Consulted` section is the exact N/A sentinel or
every cited card verifies; `1` = a cited card is stale/missing; `2` = the
section is absent but the summary references `cards/` or `.claude/knowledge/`.
The Codex review prompts point reviewers at this command.

## Testing

`tests/test-kb-knowledge-route.sh` drives the hook against a fixture KB and a
mock `kb` binary, asserting: context injection on a match, the
`UserPromptSubmit`/`additionalContext` JSON shape, no-op on
disabled/no-KB/no-binary/blank-prompt, `kb_top_k` sanitization, and that the
emitted block never leaks a literal `<deck>` placeholder into session context.
