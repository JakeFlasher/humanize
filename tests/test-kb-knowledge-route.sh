#!/usr/bin/env bash
#
# Tests for hooks/kb-knowledge-route.sh — the CACG knowledge auto-routing
# UserPromptSubmit hook.
#
# Strategy: build a fixture exported-KB layout + a mock `kb` binary, then drive
# the hook with a synthetic UserPromptSubmit stdin event and assert the
# additionalContext contract AND the prompt->search plumbing (via an argv log
# the mock writes). Isolation mirrors test-bitlesson-select-routing.sh: a strict
# SAFE_BASE_PATH (no user-local binaries), CLAUDE_PROJECT_DIR pinned to the
# fixture project, HUMANIZE_CONFIG pinned to the fixture config, and
# XDG_CONFIG_HOME pointed at an empty dir so a developer's ~/.config/humanize
# never leaks in.
#
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/test-helpers.sh"

HOOK="$PROJECT_ROOT/hooks/kb-knowledge-route.sh"
LIB="$PROJECT_ROOT/scripts/lib/kb-route-lib.sh"
SAFE_BASE_PATH="/run/current-system/sw/bin:/usr/bin:/bin:/usr/sbin:/sbin"

echo "=========================================="
echo "KB Knowledge Auto-Routing Hook Tests"
echo "=========================================="
echo ""

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
EMPTY_XDG="$WORK/xdg-empty"; mkdir -p "$EMPTY_XDG"
ARGV_LOG="$WORK/argv.log"
# Isolation assumption: the fixture tree must not be inside a git repo/worktree,
# else a stray git toplevel could shadow the pinned CLAUDE_PROJECT_DIR.
if git -C "$WORK" rev-parse --show-toplevel >/dev/null 2>&1; then
    fail "fixture root $WORK is inside a git repo/worktree; isolation broken"; print_test_summary "KB"; exit 1
fi

# argv_after <flag> <logfile> -> prints the argv element that followed <flag>
argv_after() { awk -v k="$1" '$0==k{getline; print; exit}' "$2"; }

# ----------------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------------

# A minimal exported-KB layout under <proj>/.claude/knowledge for deck cfa.
make_fixture_kb() {
    local root="$1/.claude/knowledge"
    mkdir -p "$root/out/cfa" "$root/cards/cfa/01_quantitative_methods"
    cat > "$root/out/cfa/source_matrix.json" <<'J'
{"schema_version":"cacg.v0","allowed":{"01_quantitative_methods":["qm_src"]}}
J
    cat > "$root/out/cfa/summaries.json" <<'J'
{"schema_version":"cacg.v0","summaries":[{"id":"qm-anova-table","path":"cards/cfa/01_quantitative_methods/qm-anova-table.md","reading_id":"01_quantitative_methods","card_hash":"deadbeef","title":"ANOVA Table","summary":"F-test partition.","source_ids":["qm_src"],"tags":["anova"],"schema_version":"cacg.v0"}]}
J
    printf '# card\n' > "$root/cards/cfa/01_quantitative_methods/qm-anova-table.md"
    printf '#!/usr/bin/env bash\necho stub\n' > "$root/kb-query.sh"; chmod +x "$root/kb-query.sh"
}

# A mock `kb` that (a) logs its argv to $KB_ARGV_LOG and (b) is QUERY-SENSITIVE:
# it only returns a hit when the search query contains 'anova', so a hook that
# mis-wires the prompt into the query is detectable (it would get []).
make_mock_kb() {
    local bindir="$1"; mkdir -p "$bindir"
    cat > "$bindir/kb" <<'S'
#!/usr/bin/env bash
[ -n "${KB_ARGV_LOG:-}" ] && printf '%s\n' "$@" > "$KB_ARGV_LOG"
if [ "${1:-}" = "search" ]; then
  q="${*: -1}"   # the QUERY is the last argv element (passed after `--`)
  case "$q" in
    *anova*) printf '%s' '[{"card_hash":"deadbeef","card_id":"qm-anova-table","path":"cards/cfa/01_quantitative_methods/qm-anova-table.md","reading_id":"01_quantitative_methods","score":12.5,"summary":"F-test partition of regression variance.","tags":["anova"],"title":"ANOVA Table"}]' ;;
    *) printf '[]' ;;
  esac
  exit 0
fi
exit 0
S
    chmod +x "$bindir/kb"
}

write_config() {
    local proj="$1" enabled="$2"
    mkdir -p "$proj/.humanize"
    cat > "$proj/.humanize/config.json" <<J
{"kb_enabled": $enabled, "kb_deck": "cfa", "kb_top_k": 5, "kb_search_bin": "kb"}
J
}

# Run the hook in an isolated env. Args: <proj> <extra_path_prefix|""> <prompt>
run_hook() {
    local proj="$1" extra_path="$2" prompt="$3" path_val
    if [ -n "$extra_path" ]; then path_val="$extra_path:$SAFE_BASE_PATH"; else path_val="$SAFE_BASE_PATH"; fi
    (
        cd "$proj" || exit 1
        printf '{"session_id":"t","prompt":%s}' "$(printf '%s' "$prompt" | jq -Rs '.')" \
        | env -i HOME="$WORK/home" XDG_CONFIG_HOME="$EMPTY_XDG" \
              CLAUDE_PROJECT_DIR="$proj" HUMANIZE_CONFIG="$proj/.humanize/config.json" \
              KB_ARGV_LOG="$ARGV_LOG" PATH="$path_val" \
              bash "$HOOK"
    )
}

# Run the hook with RAW (un-wrapped) stdin bytes — to exercise malformed input.
run_hook_raw() {
    local proj="$1" raw="$2"
    (
        cd "$proj" || exit 1
        printf '%s' "$raw" \
        | env -i HOME="$WORK/home" XDG_CONFIG_HOME="$EMPTY_XDG" \
              CLAUDE_PROJECT_DIR="$proj" HUMANIZE_CONFIG="$proj/.humanize/config.json" \
              KB_ARGV_LOG="$ARGV_LOG" PATH="$WORK/mockbin:$SAFE_BASE_PATH" \
              bash "$HOOK"
    )
}

# ----------------------------------------------------------------------------
# 0. Static checks
# ----------------------------------------------------------------------------
if bash -n "$HOOK" 2>/dev/null; then pass "bash -n on kb-knowledge-route.sh"; else fail "bash -n failed on hook"; fi
if bash -n "$LIB" 2>/dev/null; then pass "bash -n on kb-route-lib.sh"; else fail "bash -n failed on lib"; fi

if jq -e '.hooks.UserPromptSubmit | map(.hooks[].command) | any(test("kb-knowledge-route"))' \
     "$PROJECT_ROOT/hooks/hooks.json" >/dev/null 2>&1; then
    pass "hooks.json registers kb-knowledge-route.sh under UserPromptSubmit"
else
    fail "hooks.json does not register kb-knowledge-route.sh"
fi
# Registration is more than a substring: the referenced file must exist and be executable.
if [ -x "$PROJECT_ROOT/hooks/kb-knowledge-route.sh" ]; then pass "registered hook file exists and is executable"; else fail "registered hook file missing/non-exec"; fi

if jq -e 'has("kb_enabled") and has("kb_root") and has("kb_deck") and has("kb_top_k") and has("kb_search_bin")' \
     "$PROJECT_ROOT/config/default_config.json" >/dev/null 2>&1; then
    pass "default_config.json defines all kb_* keys"
else
    fail "default_config.json missing one or more kb_* keys"
fi

if jq -e '.kb_enabled == false' "$PROJECT_ROOT/config/default_config.json" >/dev/null 2>&1; then
    pass "kb_enabled defaults to false (opt-in)"
else
    fail "kb_enabled must default to false"
fi

# ----------------------------------------------------------------------------
# 1. Enabled + KB present + binary present + matching prompt -> injects context
#    AND the prompt->search plumbing is wired correctly.
# ----------------------------------------------------------------------------
P1="$WORK/p1"; mkdir -p "$P1"; make_fixture_kb "$P1"; make_mock_kb "$WORK/mockbin"; write_config "$P1" true
rm -f "$ARGV_LOG"
OUT1="$(run_hook "$P1" "$WORK/mockbin" "How do I read an anova table?")"

if printf '%s' "$OUT1" | jq -e '.hookSpecificOutput.hookEventName == "UserPromptSubmit"' >/dev/null 2>&1; then
    pass "enabled: output is valid JSON with hookEventName UserPromptSubmit"
else
    fail "enabled: output not the expected UserPromptSubmit JSON" "valid JSON" "$OUT1"
fi

if printf '%s' "$OUT1" | jq -re '.hookSpecificOutput.additionalContext' 2>/dev/null | grep -q 'qm-anova-table'; then
    pass "enabled: additionalContext lists the matching card id"
else
    fail "enabled: additionalContext missing matching card id" "qm-anova-table" "$OUT1"
fi

if printf '%s' "$OUT1" | jq -re '.hookSpecificOutput.additionalContext' 2>/dev/null | grep -q 'cards/cfa/'; then
    pass "enabled: block resolves the concrete deck path cards/cfa/"
else
    fail "enabled: block does not contain a concrete cards/cfa/ path"
fi

if printf '%s' "$OUT1" | jq -re '.hookSpecificOutput.additionalContext' 2>/dev/null | grep -qF '<deck>'; then
    fail "enabled: block leaked literal <deck> placeholder"
else
    pass "enabled: block never emits the literal <deck> placeholder"
fi

if printf '%s' "$OUT1" | jq -re '.hookSpecificOutput.additionalContext' 2>/dev/null | grep -qi 'suggestion'; then
    pass "enabled: block is framed as search suggestions (not provenance)"
else
    fail "enabled: block is not framed as suggestions-only"
fi

# PLUMBING: the mock recorded the exact argv the hook passed to `kb search`.
if [ "$(head -n1 "$ARGV_LOG" 2>/dev/null)" = "search" ]; then pass "plumbing: invoked the 'search' subcommand"; else fail "plumbing: first argv not 'search'" "search" "$(head -n1 "$ARGV_LOG" 2>/dev/null)"; fi
if [ "$(tail -n1 "$ARGV_LOG" 2>/dev/null)" = "How do I read an anova table?" ]; then pass "plumbing: prompt passed verbatim as the QUERY (after --)"; else fail "plumbing: query not wired to prompt" "the prompt" "$(tail -n1 "$ARGV_LOG" 2>/dev/null)"; fi
if [ "$(argv_after --source-matrix "$ARGV_LOG")" = "$P1/.claude/knowledge/out/cfa/source_matrix.json" ]; then pass "plumbing: --source-matrix wired to the export"; else fail "plumbing: --source-matrix mis-wired" "$P1/.claude/knowledge/out/cfa/source_matrix.json" "$(argv_after --source-matrix "$ARGV_LOG")"; fi
if [ "$(argv_after --summaries "$ARGV_LOG")" = "$P1/.claude/knowledge/out/cfa/summaries.json" ]; then pass "plumbing: --summaries wired to the export"; else fail "plumbing: --summaries mis-wired"; fi
if [ "$(argv_after --top-k "$ARGV_LOG")" = "5" ]; then pass "plumbing: --top-k wired to config (5)"; else fail "plumbing: --top-k mis-wired" "5" "$(argv_after --top-k "$ARGV_LOG")"; fi

# ----------------------------------------------------------------------------
# 1b. Query-sensitivity: a prompt with no KB match -> hook no-ops (empty stdout).
# ----------------------------------------------------------------------------
OUT1B="$(run_hook "$P1" "$WORK/mockbin" "unrelated topic with no card match")"
if [ -z "$OUT1B" ]; then pass "no search hits: hook is a no-op"; else fail "no hits: expected empty stdout" "" "$OUT1B"; fi

# ----------------------------------------------------------------------------
# 2. Disabled (kb_enabled false) -> no-op (empty stdout)
# ----------------------------------------------------------------------------
P2="$WORK/p2"; mkdir -p "$P2"; make_fixture_kb "$P2"; write_config "$P2" false
OUT2="$(run_hook "$P2" "$WORK/mockbin" "anova table")"
if [ -z "$OUT2" ]; then pass "disabled: hook is a no-op (empty stdout)"; else fail "disabled: expected empty stdout" "" "$OUT2"; fi

# ----------------------------------------------------------------------------
# 3. Enabled but no exported KB present -> no-op
# ----------------------------------------------------------------------------
P3="$WORK/p3"; mkdir -p "$P3"; write_config "$P3" true   # no .claude/knowledge
OUT3="$(run_hook "$P3" "$WORK/mockbin" "anova table")"
if [ -z "$OUT3" ]; then pass "no KB present: hook is a no-op"; else fail "no KB present: expected empty stdout" "" "$OUT3"; fi

# ----------------------------------------------------------------------------
# 4. Enabled + KB present but kb binary absent from PATH -> no-op
# ----------------------------------------------------------------------------
P4="$WORK/p4"; mkdir -p "$P4"; make_fixture_kb "$P4"; write_config "$P4" true
OUT4="$(run_hook "$P4" "" "anova table")"   # no mockbin on PATH
if [ -z "$OUT4" ]; then pass "no kb binary: hook is a no-op"; else fail "no kb binary: expected empty stdout" "" "$OUT4"; fi

# ----------------------------------------------------------------------------
# 5. Empty / whitespace prompt -> no-op
# ----------------------------------------------------------------------------
P5="$WORK/p5"; mkdir -p "$P5"; make_fixture_kb "$P5"; write_config "$P5" true
OUT5="$(run_hook "$P5" "$WORK/mockbin" "    ")"
if [ -z "$OUT5" ]; then pass "blank prompt: hook is a no-op"; else fail "blank prompt: expected empty stdout" "" "$OUT5"; fi

# ----------------------------------------------------------------------------
# 6. kb_top_k sanitization: invalid value (0) must not crash; hook still emits;
#    and the sanitized value (5) must reach the search argv.
# ----------------------------------------------------------------------------
P6="$WORK/p6"; mkdir -p "$P6"; make_fixture_kb "$P6"
mkdir -p "$P6/.humanize"
cat > "$P6/.humanize/config.json" <<'J'
{"kb_enabled": true, "kb_deck": "cfa", "kb_top_k": 0, "kb_search_bin": "kb"}
J
rm -f "$ARGV_LOG"
OUT6="$(run_hook "$P6" "$WORK/mockbin" "anova table")"
if printf '%s' "$OUT6" | jq -e '.hookSpecificOutput.additionalContext | length > 0' >/dev/null 2>&1; then
    pass "kb_top_k=0 is sanitized; hook still emits context"
else
    fail "kb_top_k=0 sanitization failed" "non-empty additionalContext" "$OUT6"
fi
if [ "$(argv_after --top-k "$ARGV_LOG")" = "5" ]; then pass "kb_top_k=0 sanitized to 5 in the search argv"; else fail "kb_top_k=0 not sanitized in argv" "5" "$(argv_after --top-k "$ARGV_LOG")"; fi

# ----------------------------------------------------------------------------
# 7. Malformed (non-JSON) stdin -> clean no-op: empty stdout AND exit 0.
# ----------------------------------------------------------------------------
OUT7="$(run_hook_raw "$P1" 'this is not json {{{')"; RC7=$?
if [ -z "$OUT7" ] && [ "$RC7" -eq 0 ]; then pass "malformed stdin: empty stdout + exit 0"; else fail "malformed stdin: expected empty stdout + exit 0" "'' / 0" "'$OUT7' / $RC7"; fi

# ----------------------------------------------------------------------------
# 8. Very large prompt (>64KB) must NOT SIGPIPE-abort: exit 0, and (since it
#    starts with 'anova', within the 2000-char cap) still injects context.
# ----------------------------------------------------------------------------
BIG="anova $(head -c 70000 /dev/zero | tr '\0' 'x')"
OUT8="$(run_hook "$P1" "$WORK/mockbin" "$BIG")"; RC8=$?
if [ "$RC8" -eq 0 ]; then pass "huge prompt: hook exits 0 (no SIGPIPE abort)"; else fail "huge prompt: expected exit 0" "0" "$RC8"; fi
if printf '%s' "$OUT8" | jq -e '.hookSpecificOutput.additionalContext | length > 0' >/dev/null 2>&1; then
    pass "huge prompt: still injects context (cap did not abort the search)"
else
    fail "huge prompt: expected non-empty context" "context" "$OUT8"
fi

# ----------------------------------------------------------------------------
# 9. Library unit checks (sourced directly).
# ----------------------------------------------------------------------------
(
    # shellcheck disable=SC1090
    source "$LIB"
    [ "$(kb_sanitize_top_k 7)" = "7" ]    || { echo "kb_sanitize_top_k 7"; exit 1; }
    [ "$(kb_sanitize_top_k 0)" = "5" ]    || { echo "kb_sanitize_top_k 0"; exit 1; }
    [ "$(kb_sanitize_top_k -3)" = "5" ]   || { echo "kb_sanitize_top_k -3"; exit 1; }
    [ "$(kb_sanitize_top_k abc)" = "5" ]  || { echo "kb_sanitize_top_k abc"; exit 1; }
    [ "$(kb_sanitize_top_k 9999)" = "25" ] || { echo "kb_sanitize_top_k clamp"; exit 1; }
    kb_deck_manifests_present "$P1/.claude/knowledge" cfa || { echo "manifests_present true"; exit 1; }
    kb_deck_manifests_present "$P3" cfa && { echo "manifests_present should be false"; exit 1; }
    # get_config_value is needed by kb_discover_root/kb_resolve_binary; source the loader too.
    source "$PROJECT_ROOT/scripts/lib/config-loader.sh"
    [ "$(kb_discover_root "{\"kb_root\":\"$P1/.claude/knowledge\"}" "$P1" cfa)" = "$P1/.claude/knowledge" ] || { echo "discover abs kb_root"; exit 1; }
    [ "$(kb_discover_root '{"kb_root":".claude/knowledge"}' "$P1" cfa)" = "$P1/.claude/knowledge" ] || { echo "discover rel kb_root"; exit 1; }
    [ "$(kb_discover_root '{}' "$P1" cfa)" = "$P1/.claude/knowledge" ] || { echo "discover fallback"; exit 1; }
    [ "$(kb_resolve_binary '{"kb_search_bin":"jq"}')" = "jq" ] || { echo "resolve_binary override"; exit 1; }
    exit 0
) && pass "kb-route-lib helpers behave correctly (sanitize/clamp, discover abs+rel+fallback, resolve override)" || fail "kb-route-lib helpers misbehaved"

print_test_summary "KB Knowledge Auto-Routing Hook Tests"
