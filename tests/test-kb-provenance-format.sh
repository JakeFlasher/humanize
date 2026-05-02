#!/usr/bin/env bash
#
# Test script for the KB-provenance template format.
#
# Locks the contract introduced by the my-KB-mod branch:
#  1. The `## Knowledge Consulted` heading is present in every file
#     that emits or describes a round-summary block.
#  2. The legacy bracket-placeholder stub `[List concrete reference
#     files...]` is gone from all touched files (a project Stop hook
#     scanning the rendered block would false-positive on path-like
#     example tokens inside the placeholder).
#  3. The `TODO-KB-PROVENANCE` sentinel is present in heredoc-emitting
#     files (loop-codex-stop-hook.sh x2, setup-rlcr-loop.sh x1) so that
#     a round summary materialised from those heredocs carries an
#     unfilled-stub indicator humans + project Stop hooks can key on.
#  4. The auto-routing-aware conditional language ("auto-routing
#     knowledge-base hook") is present in next-round-prompt.md and the
#     setup-rlcr-loop.sh round-0 plan stub, replacing the legacy
#     unconditional "open INDEX.md" instruction.
#  5. The exact `N/A -- task not KB-relevant this round` literal (which
#     Codex's heading-level provenance check greps for) is present in
#     every prompt-template file that requires a Knowledge Consulted
#     section.
#  6. `bash -n` passes on heredoc-emitting shell scripts (defends
#     against accidental syntax breakage when the stub format changes).
#
# Each assertion is a single grep / shell command — fast, deterministic,
# no temp-dir or external-tool dependency.
#

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/test-helpers.sh"

echo "========================================"
echo "Testing KB-provenance template format"
echo "========================================"
echo ""

cd "$PROJECT_ROOT"

# 1. `## Knowledge Consulted` heading present in 5 expected files.
KB_FILES=(
    hooks/loop-codex-stop-hook.sh
    scripts/setup-rlcr-loop.sh
    prompt-template/claude/finalize-phase-prompt.md
    prompt-template/claude/finalize-phase-skipped-prompt.md
    prompt-template/claude/review-phase-prompt.md
)
for f in "${KB_FILES[@]}"; do
    if grep -q "## Knowledge Consulted" "$f" 2>/dev/null; then
        pass "## Knowledge Consulted heading present in $f"
    else
        fail "## Knowledge Consulted heading missing in $f"
    fi
done

# 2. Legacy bracket-placeholder absent from all touched files. Includes
#    the prompt-template files in case anybody copy-paste-pollutes them.
LEGACY_SCAN_FILES=(
    "${KB_FILES[@]}"
    prompt-template/codex/full-alignment-review.md
    prompt-template/codex/regular-review.md
)
for f in "${LEGACY_SCAN_FILES[@]}"; do
    if grep -qF '[List concrete reference files' "$f" 2>/dev/null; then
        fail "Legacy bracket-placeholder still present in $f" \
             "absent" "found '[List concrete reference files' literal"
    else
        pass "Legacy bracket-placeholder absent from $f"
    fi
done

# 3. TODO-KB-PROVENANCE sentinel present with expected occurrence count.
hook_count=$(grep -cF 'TODO-KB-PROVENANCE' \
    hooks/loop-codex-stop-hook.sh 2>/dev/null || echo 0)
if [[ "$hook_count" -eq 2 ]]; then
    pass "TODO-KB-PROVENANCE sentinel x2 in loop-codex-stop-hook.sh"
else
    fail "TODO-KB-PROVENANCE sentinel count in loop-codex-stop-hook.sh" \
         "2" "$hook_count"
fi
setup_count=$(grep -cF 'TODO-KB-PROVENANCE' \
    scripts/setup-rlcr-loop.sh 2>/dev/null || echo 0)
if [[ "$setup_count" -eq 1 ]]; then
    pass "TODO-KB-PROVENANCE sentinel x1 in setup-rlcr-loop.sh"
else
    fail "TODO-KB-PROVENANCE sentinel count in setup-rlcr-loop.sh" \
         "1" "$setup_count"
fi

# 4. Auto-routing-aware conditional language in the two prompts that
#    instruct the agent on knowledge-base discovery.
AR_FILES=(
    prompt-template/claude/next-round-prompt.md
    scripts/setup-rlcr-loop.sh
)
for f in "${AR_FILES[@]}"; do
    if grep -q "auto-routing knowledge-base hook" "$f" 2>/dev/null; then
        pass "auto-routing-aware conditional in $f"
    else
        fail "auto-routing-aware conditional missing from $f"
    fi
done

# 5. The exact N/A literal is present in every prompt-template file that
#    asks the agent to write a Knowledge Consulted section. (Codex's
#    heading-level provenance check greps for this literal in the round
#    summary; the prompt instruction must teach the agent the exact
#    spelling.)
NA_LITERAL='N/A -- task not KB-relevant this round'
NA_FILES=(
    prompt-template/claude/finalize-phase-prompt.md
    prompt-template/claude/finalize-phase-skipped-prompt.md
    prompt-template/claude/review-phase-prompt.md
    prompt-template/codex/full-alignment-review.md
    prompt-template/codex/regular-review.md
)
for f in "${NA_FILES[@]}"; do
    if grep -qF "$NA_LITERAL" "$f" 2>/dev/null; then
        pass "exact N/A literal present in $f"
    else
        fail "exact N/A literal missing from $f"
    fi
done

# 6. bash -n on heredoc-emitting shell scripts.
SHELL_FILES=(
    hooks/loop-codex-stop-hook.sh
    scripts/setup-rlcr-loop.sh
    rebase-kb-mod.sh
)
for f in "${SHELL_FILES[@]}"; do
    if bash -n "$f" 2>/dev/null; then
        pass "bash -n OK on $f"
    else
        fail "bash -n failed on $f"
    fi
done

# 7. The TODO-KB-PROVENANCE sentinel inside heredoc-emitted stubs must
#    not contain a path-like token (otherwise an unfilled stub would
#    false-positive a project Stop hook's path detector). Extract every
#    line containing TODO-KB-PROVENANCE and grep for path tokens.
PATH_TOKEN_RE='[A-Za-z0-9_./-]+\.(md|pdf|cu|cuh|h|cpp|hpp|py|sh|toml|json|txt|rs)\b'
sentinel_lines=$(grep -hF 'TODO-KB-PROVENANCE' \
    hooks/loop-codex-stop-hook.sh scripts/setup-rlcr-loop.sh 2>/dev/null \
    || true)
if [[ -z "$sentinel_lines" ]]; then
    fail "no TODO-KB-PROVENANCE lines found for path-token check"
elif echo "$sentinel_lines" | grep -qE "$PATH_TOKEN_RE"; then
    fail "TODO-KB-PROVENANCE line contains a path-like token" \
         "no path tokens in sentinel" \
         "$(echo "$sentinel_lines" | grep -oE "$PATH_TOKEN_RE" | head -3 \
            | tr '\n' ' ')"
else
    pass "TODO-KB-PROVENANCE lines contain no path-like tokens"
fi

print_test_summary "KB-provenance format Tests"
