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
source "$PLUGIN_ROOT/scripts/portable-timeout.sh"   # run_with_timeout (falls back to direct exec if unavailable)

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
# Project-relative path to the exported query wrapper (falls back to absolute if KB is outside the project).
QUERY_REL="${KB_ROOT#"$PROJECT_ROOT"/}/kb-query.sh"

# Extract the user prompt and cap its length. Truncate in-shell (char-based) rather than
# piping to `head -c`: under `set -o pipefail` a >64KB prompt SIGPIPE-kills the pipeline
# (exit 141) and `jq` on malformed stdin exits 5 — both would abort the hook instead of
# no-op'ing. `|| true` + ${var:0:N} keeps every path graceful and avoids mid-UTF8 byte splits.
PROMPT="$(printf '%s' "$INPUT" | jq -r '.prompt // empty' 2>/dev/null || true)"
PROMPT="${PROMPT:0:2000}"
[[ -n "${PROMPT//[[:space:]]/}" ]] || exit 0

# Deterministic search, time-bounded (a wedged/huge corpus must not hang the prompt). `--`
# ends option parsing so a prompt beginning with '-' is taken as the QUERY, not a flag.
HITS="$(run_with_timeout "${KB_SEARCH_TIMEOUT:-10}" "$KB_BIN" search --json --source-matrix "$SM" --summaries "$SUM" --top-k "$TOP_K" -- "$PROMPT" 2>/dev/null)" || exit 0
printf '%s' "$HITS" | jq -e 'type == "array" and length > 0' >/dev/null 2>&1 || exit 0

# Render the suggestion block. The framing is load-bearing: these are SUGGESTIONS,
# not provenance. `kb verify --round-summary` treats the `## Knowledge Consulted`
# section as authoritative, so this block must not be copied wholesale into it.
BLOCK="$(printf '%s' "$HITS" | jq -r --arg deck "$DECK" --arg wrapper "$QUERY_REL" '
  "## CACG Knowledge — auto-routed matches (deck: \($deck))\n"
  + "Search suggestions only. Open a card with `\($wrapper) show <card_id>` (or read its `.md`) and cite it in your round summary'"'"'s `## Knowledge Consulted` section ONLY if you actually opened and used it.\n\n"
  + ( [ .[]
        | "- `\((.path // "") | gsub("[\r\n]+";" "))` (\((.card_id // "") | gsub("[\r\n]+";" "))) — \((.title // "") | gsub("[\r\n]+";" ") | .[0:200]): "
          + ((.summary // "") | gsub("[\r\n]+"; " ") | .[0:200])
      ] | join("\n") )
')"

# Hard byte cap so a pathological corpus can never bloat every prompt.
MAX_BYTES=4096
if [[ "$(printf '%s' "$BLOCK" | wc -c)" -gt "$MAX_BYTES" ]]; then
    BLOCK="$(printf '%s' "$BLOCK" | head -c "$MAX_BYTES" || true)"$'\n- … (truncated)'
fi

jq -n --arg ctx "$BLOCK" \
  '{hookSpecificOutput: {hookEventName: "UserPromptSubmit", additionalContext: $ctx}}'
exit 0
