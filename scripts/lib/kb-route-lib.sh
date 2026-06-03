#!/usr/bin/env bash
#
# kb-route-lib.sh — shared helpers for CACG knowledge-base auto-routing.
#
# A "KB root" is a directory exported by the CACG repo's
# scripts/export-knowledge.sh. It contains:
#   <root>/out/<deck>/source_matrix.json   (REQUIRED by kb search)
#   <root>/out/<deck>/summaries.json       (the BM25 corpus)
#   <root>/out/<deck>/cards_manifest.json  (used by kb show)
#   <root>/cards/<deck>/...                (the card bodies)
#
# These helpers are deliberately side-effect-free (no stdout except the
# documented return values) so they are safe to source inside a
# UserPromptSubmit hook on the hot path.

if [[ -n "${_HUMANIZE_KB_ROUTE_LIB_SOURCED:-}" ]]; then
    return 0 2>/dev/null || true
fi
_HUMANIZE_KB_ROUTE_LIB_SOURCED=1

# kb_deck_manifests_present <root> <deck>
#   Returns 0 iff the two files kb search hard-requires exist under <root>.
kb_deck_manifests_present() {
    local root="$1" deck="$2"
    [[ -n "$root" && -n "$deck" ]] || return 1
    [[ -f "$root/out/$deck/source_matrix.json" && -f "$root/out/$deck/summaries.json" ]]
}

# kb_discover_root <merged_config_json> <project_root> <deck>
#   Prints the resolved KB root on stdout (empty if none). Resolution order:
#     1. config kb_root (absolute, or relative to project_root)
#     2. <project_root>/.claude/knowledge   (export-knowledge.sh default dest-root)
#     3. <project_root>/.humanize/kb        (alternate co-located layout)
#   Only returns a candidate that actually has the deck manifests.
kb_discover_root() {
    local cfg="$1" proot="$2" deck="$3"
    local configured candidate
    configured="$(get_config_value "$cfg" kb_root 2>/dev/null || true)"
    if [[ -n "$configured" ]]; then
        case "$configured" in
            /*) candidate="$configured" ;;
            *)  candidate="$proot/$configured" ;;
        esac
        if kb_deck_manifests_present "$candidate" "$deck"; then printf '%s' "$candidate"; return 0; fi
    fi
    for candidate in "$proot/.claude/knowledge" "$proot/.humanize/kb"; do
        if kb_deck_manifests_present "$candidate" "$deck"; then printf '%s' "$candidate"; return 0; fi
    done
    printf ''
    return 0
}

# kb_resolve_binary <merged_config_json>
#   Prints the kb binary to use (config kb_search_bin, default "kb"). Returns
#   0 iff that binary is actually invocable.
kb_resolve_binary() {
    local cfg="$1" bin
    bin="$(get_config_value "$cfg" kb_search_bin 2>/dev/null || true)"
    bin="${bin:-kb}"
    printf '%s' "$bin"
    command -v "$bin" >/dev/null 2>&1
}

# kb_sanitize_top_k <raw>  ->  prints a positive integer (default 5)
#   get_config_value tostring-coerces numbers, so kb_top_k arrives as a
#   string. Reject 0/negative/non-numeric and fall back to 5.
kb_sanitize_top_k() {
    local raw="$1"
    if [[ "$raw" =~ ^[1-9][0-9]*$ ]]; then printf '%s' "$raw"; else printf '5'; fi
}
