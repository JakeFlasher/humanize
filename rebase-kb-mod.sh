#!/usr/bin/env bash
# rebase-kb-mod.sh - Rebase the local my-KB-mod branch onto the latest origin/dev.
#
# Usage:
#   ./rebase-kb-mod.sh              # normal run
#   ./rebase-kb-mod.sh --dry-run    # fetch + overlap check only, no rebase
#
# What it does:
#   1. Verifies we are on the my-KB-mod branch inside the expected repo.
#   2. Fetches origin/dev.
#   3. Computes the fork point (merge-base) and auto-derives the list of
#      locally modified files. Works with any number of local commits.
#   4. Lists new upstream commits (exits early if already up to date).
#   5. Checks for file overlap between local modifications and new upstream.
#      - If overlap: prints conflicting files and STOPS (manual review needed).
#      - If no overlap (or --force): rebases automatically.
#   6. Runs verification: bash -n, test suite, KB smoke grep.
#   7. Prints a summary.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# --- Configuration ---
BRANCH="my-KB-mod"
UPSTREAM="origin/dev"

# --- Helpers ---
die()  { echo "FATAL: $*" >&2; exit 1; }
info() { echo "--- $*"; }
ok()   { echo "[OK] $*"; }
warn() { echo "[WARN] $*" >&2; }

DRY_RUN=false
FORCE=false
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        --force)   FORCE=true ;;
        -h|--help)
            echo "Usage: $0 [--dry-run] [--force]"
            echo "  --dry-run   Fetch and check overlap only, do not rebase"
            echo "  --force     Rebase even if file overlap is detected (conflicts may occur)"
            exit 0
            ;;
        *) die "Unknown argument: $arg" ;;
    esac
done

# --- Preflight checks ---
info "Preflight checks"

git rev-parse --is-inside-work-tree >/dev/null 2>&1 \
    || die "Not inside a git repository"

CURRENT_BRANCH="$(git branch --show-current)"
if [[ "$CURRENT_BRANCH" != "$BRANCH" ]]; then
    die "Expected branch '$BRANCH', but currently on '$CURRENT_BRANCH'. Switch first: git switch $BRANCH"
fi

if ! git diff --quiet HEAD 2>/dev/null || ! git diff --cached --quiet 2>/dev/null; then
    die "Working tree has uncommitted tracked-file changes. Commit or stash before rebasing."
fi

ok "On branch $BRANCH, working tree clean."

# --- Fetch ---
info "Fetching $UPSTREAM"
git fetch origin dev 2>&1 | tail -3

UPSTREAM_HEAD="$(git rev-parse --short "$UPSTREAM")"
LOCAL_HEAD="$(git rev-parse --short HEAD)"

# Compute fork point: the common ancestor of our branch and upstream.
# This works regardless of how many local commits exist.
FORK_BASE="$(git merge-base "$UPSTREAM" HEAD 2>/dev/null)" \
    || die "Cannot find merge-base between $UPSTREAM and HEAD. Is the branch related to $UPSTREAM?"
FORK_BASE_SHORT="$(git rev-parse --short "$FORK_BASE")"

LOCAL_COMMIT_COUNT="$(git rev-list --count "$FORK_BASE"..HEAD)"

info "Local HEAD:     $LOCAL_HEAD ($LOCAL_COMMIT_COUNT local commit(s) since fork)"
info "Fork base:      $FORK_BASE_SHORT"
info "Upstream:       $UPSTREAM_HEAD"

if [[ "$LOCAL_COMMIT_COUNT" -eq 0 ]]; then
    die "No local commits on top of $UPSTREAM. Nothing to rebase — branch is at upstream tip."
fi

# Auto-derive the list of files modified by all local commits.
# No hardcoded file list — always accurate, never drifts.
mapfile -t LOCAL_MOD_FILES < <(git diff --name-only "$FORK_BASE"..HEAD)

info "Local modifications touch ${#LOCAL_MOD_FILES[@]} file(s):"
for f in "${LOCAL_MOD_FILES[@]}"; do
    echo "  $f"
done

# --- Check for new commits ---
NEW_COMMITS="$(git log --oneline "${BRANCH}".."${UPSTREAM}")"
if [[ -z "$NEW_COMMITS" ]]; then
    ok "Already up to date with $UPSTREAM. Nothing to do."
    exit 0
fi

COMMIT_COUNT="$(echo "$NEW_COMMITS" | wc -l | tr -d ' ')"
info "$COMMIT_COUNT new upstream commit(s):"
echo "$NEW_COMMITS" | sed 's/^/  /'

# --- Overlap detection ---
info "Checking file overlap between local modifications and upstream changes"

UPSTREAM_FILES="$(git diff --name-only "${FORK_BASE}".."${UPSTREAM}")"
OVERLAPPING=()

for f in "${LOCAL_MOD_FILES[@]}"; do
    if echo "$UPSTREAM_FILES" | grep -qxF "$f"; then
        OVERLAPPING+=("$f")
    fi
done

if [[ ${#OVERLAPPING[@]} -gt 0 ]]; then
    warn "File overlap detected (${#OVERLAPPING[@]} file(s)):"
    for f in "${OVERLAPPING[@]}"; do
        echo "  - $f" >&2
    done
    if [[ "$FORCE" == true ]]; then
        warn "Proceeding due to --force. Conflicts may require manual resolution."
    elif [[ "$DRY_RUN" == true ]]; then
        warn "Dry run: would stop here without --force."
        exit 1
    else
        echo ""
        echo "Overlap detected. Options:"
        echo "  1. Review the upstream changes, then re-run with --force"
        echo "  2. Manually rebase: git rebase origin/dev"
        echo "  3. Inspect upstream diff: git diff ${FORK_BASE_SHORT}..${UPSTREAM} -- <file>"
        exit 1
    fi
else
    ok "No file overlap. Clean rebase expected."
fi

if [[ "$DRY_RUN" == true ]]; then
    info "Dry run complete. Rebase would apply cleanly."
    exit 0
fi

# --- Rebase ---
info "Rebasing $BRANCH ($LOCAL_COMMIT_COUNT commit(s)) onto $UPSTREAM"
if ! git rebase "$UPSTREAM" 2>&1; then
    die "Rebase failed. Resolve conflicts, then: git rebase --continue"
fi

NEW_HEAD="$(git rev-parse --short HEAD)"
ok "Rebase successful. New HEAD: $NEW_HEAD"

# --- Verification ---
info "Running verification suite"

FAIL=0

# bash syntax check
info "bash -n on shell scripts"
if bash -n hooks/loop-codex-stop-hook.sh scripts/setup-rlcr-loop.sh 2>&1; then
    ok "Shell syntax OK"
else
    warn "Shell syntax check failed"
    FAIL=1
fi

# Upstream test suite
info "Running tests/test-commit-history-section.sh"
if bash tests/test-commit-history-section.sh 2>&1 | tail -5; then
    ok "Test suite passed"
else
    warn "Test suite had failures"
    FAIL=1
fi

# KB smoke grep
info "KB smoke grep: ## Knowledge Consulted"
KB_GREP_FILES=(
    hooks/loop-codex-stop-hook.sh
    scripts/setup-rlcr-loop.sh
    prompt-template/claude/finalize-phase-prompt.md
    prompt-template/claude/finalize-phase-skipped-prompt.md
    prompt-template/claude/review-phase-prompt.md
)
KB_MISSING=()
for f in "${KB_GREP_FILES[@]}"; do
    if ! grep -q "## Knowledge Consulted" "$f" 2>/dev/null; then
        KB_MISSING+=("$f")
    fi
done

if [[ ${#KB_MISSING[@]} -gt 0 ]]; then
    warn "KB stubs missing from: ${KB_MISSING[*]}"
    FAIL=1
else
    ok "KB stubs present in all expected files"
fi

# Codex-side provenance check
info "KB smoke grep: Knowledge Provenance Check (Codex review files)"
CODEX_FILES=(
    prompt-template/codex/full-alignment-review.md
    prompt-template/codex/regular-review.md
)
CODEX_MISSING=()
for f in "${CODEX_FILES[@]}"; do
    if ! grep -q "Knowledge Provenance Check" "$f" 2>/dev/null; then
        CODEX_MISSING+=("$f")
    fi
done

if [[ ${#CODEX_MISSING[@]} -gt 0 ]]; then
    warn "Codex-side KB check missing from: ${CODEX_MISSING[*]}"
    FAIL=1
else
    ok "Codex-side KB provenance check present in both review files"
fi

# KB hygiene: legacy bracket-placeholder absence check.
# The legacy stub text `[List concrete reference files...]` would satisfy a
# heading-level grep but NOT a project Stop hook that scans the rendered
# round-summary block for real repo-relative path tokens. Catch any leftover
# bracket-placeholder so the rebase can't silently re-introduce stale stubs.
info "KB hygiene: legacy bracket-placeholder absence check"
LEGACY_PLACEHOLDER_FILES=()
for f in "${KB_GREP_FILES[@]}"; do
    if grep -qF '[List concrete reference files' "$f" 2>/dev/null; then
        LEGACY_PLACEHOLDER_FILES+=("$f")
    fi
done
if [[ ${#LEGACY_PLACEHOLDER_FILES[@]} -gt 0 ]]; then
    warn "Legacy bracket-placeholder still present in: ${LEGACY_PLACEHOLDER_FILES[*]}"
    FAIL=1
else
    ok "Legacy bracket-placeholder fully replaced"
fi

# KB hygiene: TODO-KB-PROVENANCE sentinel presence check.
# Heredoc-emitting files must contain the new sentinel so that round
# summaries materialized from them carry an unfilled-stub indicator that
# both human reviewers and the project Stop hook can key on.
info "KB hygiene: TODO-KB-PROVENANCE sentinel presence check"
SENTINEL_FILES=(
    hooks/loop-codex-stop-hook.sh
    scripts/setup-rlcr-loop.sh
)
SENTINEL_MISSING=()
for f in "${SENTINEL_FILES[@]}"; do
    if ! grep -qF 'TODO-KB-PROVENANCE' "$f" 2>/dev/null; then
        SENTINEL_MISSING+=("$f")
    fi
done
if [[ ${#SENTINEL_MISSING[@]} -gt 0 ]]; then
    warn "TODO-KB-PROVENANCE sentinel missing from: ${SENTINEL_MISSING[*]}"
    FAIL=1
else
    ok "TODO-KB-PROVENANCE sentinel present in all heredoc-emitting files"
fi

# Upstream integral-context check
info "Verifying upstream COMMIT_HISTORY_SECTION wiring"
CH_COUNT="$(grep -c "COMMIT_HISTORY_SECTION" hooks/loop-codex-stop-hook.sh || true)"
if [[ "$CH_COUNT" -ge 4 ]]; then
    ok "COMMIT_HISTORY_SECTION found $CH_COUNT times in hook (expected >= 4)"
else
    warn "COMMIT_HISTORY_SECTION only found $CH_COUNT times (expected >= 4)"
    FAIL=1
fi

# --- Summary ---
echo ""
echo "========================================"
info "Summary"
echo "========================================"
echo "Branch:         $BRANCH"
echo "Fork base:      $FORK_BASE_SHORT"
echo "New base:       $UPSTREAM_HEAD"
echo "Local commits:  $LOCAL_COMMIT_COUNT"
echo "New HEAD:       $NEW_HEAD"
echo "Upstream delta: $COMMIT_COUNT commit(s)"
echo "File overlap:   ${#OVERLAPPING[@]}"

BEHIND="$(git log --oneline "${BRANCH}".."${UPSTREAM}" 2>/dev/null | wc -l | tr -d ' ')"
echo "Still behind:   $BEHIND"

if [[ "$FAIL" -eq 0 ]]; then
    echo ""
    ok "All verifications passed."
    exit 0
else
    echo ""
    warn "Some verifications failed. Review the warnings above."
    exit 1
fi
