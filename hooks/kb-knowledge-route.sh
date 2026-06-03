#!/usr/bin/env bash
#
# kb-knowledge-route.sh — UserPromptSubmit hook: auto-route CACG knowledge cards.
#
# This is the concrete "auto-routing knowledge-base hook" that the RLCR prompts
# refer to. On every prompt submission, when a project has an exported CACG
# knowledge base AND opts in (kb_enabled: true), it runs a DETERMINISTIC
# `kb search` over the prompt and injects the top matching card summaries into
# the session as additionalContext. No LLM call, no provider routing.
#
# Output contract (Claude Code UserPromptSubmit):
#   {"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"..."}}
# Empty stdout + exit 0 == no-op (the default for every non-KB / misconfigured /
# disabled / error path). This hook NEVER blocks a prompt and never emits a
# {decision:...} object.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Lightweight sourcing only — do NOT pull in loop-common.sh (it resolves the
# project root and validates templates at source time; unnecessary here).
source "$SCRIPT_DIR/lib/project-root.sh"
source "$PLUGIN_ROOT/scripts/lib/config-loader.sh"
source "$PLUGIN_ROOT/scripts/lib/kb-route-lib.sh"

# jq is mandatory for both config parsing and result shaping; without it, no-op.
command -v jq >/dev/null 2>&1 || exit 0

# Consume stdin (UserPromptSubmit always pipes a JSON event).
INPUT="$(cat || true)"

PROJECT_ROOT="$(resolve_project_root)" || exit 0

# Merged config (default -> user -> project). Suppress config-load warnings so a
# malformed project config never adds noise to the default-disabled hot path.
MERGED="$(load_merged_config "$PLUGIN_ROOT" "$PROJECT_ROOT" 2>/dev/null)" || exit 0

# Opt-in gate. get_config_value tostring-coerces booleans, so this is "true"/"false".
KB_ENABLED="$(get_config_value "$MERGED" kb_enabled 2>/dev/null || true)"
[[ "$KB_ENABLED" == "true" ]] || exit 0

DECK="$(get_config_value "$MERGED" kb_deck 2>/dev/null || true)"; DECK="${DECK:-cfa}"
TOP_K="$(kb_sanitize_top_k "$(get_config_value "$MERGED" kb_top_k 2>/dev/null || true)")"

# Resolve the KB root and binary; any miss is a clean no-op.
KB_ROOT="$(kb_discover_root "$MERGED" "$PROJECT_ROOT" "$DECK")"
[[ -n "$KB_ROOT" ]] || exit 0
KB_BIN="$(kb_resolve_binary "$MERGED")" || exit 0

SM="$KB_ROOT/out/$DECK/source_matrix.json"
SUM="$KB_ROOT/out/$DECK/summaries.json"

# Extract the user prompt; cap the query length so a huge paste stays a sane query.
PROMPT="$(printf '%s' "$INPUT" | jq -r '.prompt // empty' 2>/dev/null | head -c 2000)"
[[ -n "${PROMPT//[[:space:]]/}" ]] || exit 0

# Deterministic search. Failure (bad manifest, version drift, etc.) -> no-op.
HITS="$("$KB_BIN" search "$PROMPT" --json --source-matrix "$SM" --summaries "$SUM" --top-k "$TOP_K" 2>/dev/null)" || exit 0
printf '%s' "$HITS" | jq -e 'type == "array" and length > 0' >/dev/null 2>&1 || exit 0

# Render the suggestion block. The framing is load-bearing: these are SUGGESTIONS,
# not provenance. `kb verify --round-summary` treats the `## Knowledge Consulted`
# section as authoritative, so this block must not be copied wholesale into it.
BLOCK="$(printf '%s' "$HITS" | jq -r --arg deck "$DECK" '
  "## CACG Knowledge — auto-routed matches (deck: \($deck))\n"
  + "Search suggestions only. Open a card with `kb show <card_id>` (or read its `.md`) and cite it in your round summary'"'"'s `## Knowledge Consulted` section ONLY if you actually opened and used it.\n\n"
  + ( [ .[]
        | "- `\(.path)` (\(.card_id)) — \(.title): "
          + ((.summary // "") | gsub("[\r\n]+"; " ") | .[0:200])
      ] | join("\n") )
')"

# Hard byte cap so a pathological corpus can never bloat every prompt.
MAX_BYTES=4096
if [[ "$(printf '%s' "$BLOCK" | wc -c)" -gt "$MAX_BYTES" ]]; then
    BLOCK="$(printf '%s' "$BLOCK" | head -c "$MAX_BYTES")"$'\n- … (truncated)'
fi

jq -n --arg ctx "$BLOCK" \
  '{hookSpecificOutput: {hookEventName: "UserPromptSubmit", additionalContext: $ctx}}'
exit 0
