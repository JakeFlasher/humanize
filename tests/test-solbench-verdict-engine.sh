#!/usr/bin/env bash
#
# Tests for the artifact verdict engine and its bash wrapper.
#
# Covers all 22 acceptance criteria from
# .humanize/plans/humanize_solbench_research_harness_plan.md:
#  - Engine activation and sidecar identity validation (AC-2, 13, 14, 17, 18)
#  - Sentinel-aware SOL score and per-surface manifest preservation (AC-3, 4)
#  - Hard-block / soft-warn matrix (AC-5a-e, AC-6a-d)
#  - Wrapper integration, mutation ordering, JSONL emission (AC-1a-e, 7, 8,
#    15, 16, 19, 20, 21)
#  - Adapter seam, registration, Python invocation, KB-provenance hygiene
#    (AC-9, 10, 11, 12, 22)
#
# All assertions use grep / bash / python3 / sha256sum only — no external
# binaries beyond a standard Linux toolchain. The verdict engine is invoked
# in process; the wrapper is exercised end-to-end via a small helper that
# sources loop-common.sh in a subshell.
#

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/test-helpers.sh"

ENGINE="$PROJECT_ROOT/hooks/lib/solbench_verdict_engine.py"
WRAPPER_FILE="$PROJECT_ROOT/hooks/lib/loop-common.sh"
STOP_HOOK="$PROJECT_ROOT/hooks/loop-codex-stop-hook.sh"
METHODOLOGY_HOOK="$PROJECT_ROOT/hooks/lib/methodology-analysis.sh"
FIXTURE_DIR="$SCRIPT_DIR/fixtures/solbench-verdict-engine"

echo "========================================"
echo "Testing solbench verdict engine"
echo "========================================"
echo ""

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

# Make an isolated env root with .humanize/rlcr/<loop_id>/ + state.md +
# adapter-config.json. Prints the loop directory path on stdout.
setup_test_env() {
    local env_root="$1"
    local round="${2:-1}"
    local loop_id="${3:-test-loop}"
    local activate_adapter="${4:-true}"
    local loop_dir="$env_root/.humanize/rlcr/$loop_id"
    mkdir -p "$loop_dir"

    cat > "$loop_dir/state.md" <<EOF
---
current_round: $round
max_iterations: 60
start_branch: my-KB-mod
base_branch: main
plan_file: .humanize/plans/test.md
plan_tracked: false
review_started: false
session_id: test-session
mainline_stall_count: 0
last_mainline_verdict: advanced
drift_status: normal
verdict_mismatch: false
verdict_mismatch_count: 0
last_computed_verdict: unknown
last_block_reason: null
---

Test state.
EOF

    if [[ "$activate_adapter" == "true" ]]; then
        mkdir -p "$env_root/.humanize"
        printf '%s\n' '{"adapter": "solbench"}' \
            > "$env_root/.humanize/adapter-config.json"
    fi

    printf '%s\n' "$loop_dir"
}

# Generate a sidecar JSON at $loop_dir/round-<N>-objectives.json with
# computed manifest hash, computed objective hash, and a fresh generated_at.
# Optional overrides_json is a small JSON object merged into the base
# template (top-level merge, recursive for nested known keys).
make_sidecar() {
    local loop_dir="$1"
    local round="$2"
    local manifest_full="$3"
    local overrides_json="${4:-{\}}"

    python3 - "$loop_dir" "$round" "$manifest_full" "$overrides_json" <<'PY'
import sys, json, os, hashlib, datetime, pathlib
loop_dir, round_, manifest_full, overrides_json = sys.argv[1:5]
round_num = int(round_)
loop_id = os.path.basename(loop_dir)
overrides = json.loads(overrides_json)

manifest_bytes = pathlib.Path(manifest_full).read_bytes()
manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()

objective_id = overrides.pop("objective_id", "test_objective")
objective_hash = hashlib.sha256(objective_id.encode()).hexdigest()

sidecar = {
    "sidecar_schema_version": "1.0",
    "loop_id": loop_id,
    "round": round_num,
    "adapter": "solbench",
    "objective_id": objective_id,
    "objective_hash": objective_hash,
    "manifest_path": manifest_full,
    "manifest_hash": manifest_hash,
    "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    ),
    "correctness": {"passed": True, "tests_passed": 9, "tests_failed": 0},
    "latency": {
        "required": False,
        "delta_pct": -2.1,
        "threshold_pct": None,
        "basis": "cupti_activity",
    },
    "sol_score": {
        "value": "unknown_t_sol",
        "provenance": {
            "authority": "local_proxy_5060",
            "basis": "solar_stage4_missing",
            "leaderboard_comparable": False,
        },
    },
    "leaderboard_comparable_required": False,
    "required_surfaces": ["nvbit", "cupti_activity"],
    "ac_deltas": {"AC-3": {"from": "pending", "to": "passed"}},
    "rule_compliance": {
        rid: {"status": "verified", "evidence": "fixture"}
        for rid in [
            "rule_1_no_ncu",
            "rule_2_no_clock_lock",
            "rule_3_no_privileged_cupti",
            "rule_4_no_host_driver_work",
            "rule_5_submission_language",
            "rule_6_no_evaluator_state_exploit",
            "rule_7_default_stream",
            "rule_8_precision_contract",
            "rule_9_iiswc_no_access",
        ]
    },
    "rule_required_by_objective": {"rule_7": False, "rule_9": False},
}

def deep_merge(base, override):
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_merge(base[k], v)
        else:
            base[k] = v

deep_merge(sidecar, overrides)
out_path = os.path.join(loop_dir, f"round-{round_num}-objectives.json")
with open(out_path, "w", encoding="utf-8") as fh:
    json.dump(sidecar, fh)
print(out_path)
PY
}

# Run the verdict engine; emit "exit=<code>\n<stdout>" on stdout.
# Stderr is captured but suppressed unless --keep-stderr is passed.
run_engine() {
    local env_root="$1"
    local loop_dir="$2"
    local round="$3"
    local transition="${4:-next_round}"
    local mode="${5:-gated}"
    local codex_verdict="${6:-}"

    local args=(
        "--loop-dir" "$loop_dir"
        "--round" "$round"
        "--transition" "$transition"
        "--mode" "$mode"
        "--project-root" "$env_root"
    )
    if [[ -n "$codex_verdict" ]]; then
        args+=("--codex-verdict" "$codex_verdict")
    fi

    local rc=0
    python3 "$ENGINE" "${args[@]}" 2>/dev/null || rc=$?
    printf '%d' "$rc"
}

last_jsonl_row() {
    local loop_dir="$1"
    local progress="$loop_dir/solbench-progress.jsonl"
    [[ -f "$progress" ]] || return 1
    tail -1 "$progress"
}

count_jsonl_rows() {
    local loop_dir="$1"
    local progress="$loop_dir/solbench-progress.jsonl"
    [[ -f "$progress" ]] || { echo 0; return 0; }
    wc -l < "$progress" | tr -d ' '
}

state_sha() {
    local loop_dir="$1"
    sha256sum "$loop_dir/state.md" | awk '{print $1}'
}

cleanup_env() {
    local env_root="$1"
    [[ -n "$env_root" && -d "$env_root" ]] && rm -rf "$env_root" || true
}

# ------------------------------------------------------------------
# Static contract checks (file presence, registration, sentinels)
# ------------------------------------------------------------------

# AC-9: test file registered in run-all-tests.sh TEST_SUITES.
if grep -q '"test-solbench-verdict-engine.sh"' "$PROJECT_ROOT/tests/run-all-tests.sh"; then
    pass "AC-9: test file registered in run-all-tests.sh TEST_SUITES"
else
    fail "AC-9: test file not registered in run-all-tests.sh"
fi

# AC-10: engine exists and has python3 shebang; bash hook references python3
# invocation; no uv run / bare python invocation in the engine path string.
if [[ -f "$ENGINE" ]] && head -1 "$ENGINE" | grep -q 'python3'; then
    pass "AC-10: engine has python3 shebang"
else
    fail "AC-10: engine missing or has wrong shebang"
fi
if grep -q 'python3 "$engine_path"' "$WRAPPER_FILE"; then
    pass "AC-10: bash wrapper invokes python3 (not uv run, not bare python)"
else
    fail "AC-10: bash wrapper missing python3 invocation"
fi
if grep -qE 'uv run python|uv_run' "$WRAPPER_FILE"; then
    fail "AC-10: wrapper must not invoke uv run"
else
    pass "AC-10: wrapper does not use uv run"
fi

# AC-12: adapter seam directory exists with README; engine has no --adapter
# CLI flag.
if [[ -f "$PROJECT_ROOT/hooks/lib/verdict_adapters/README.md" ]]; then
    pass "AC-12: hooks/lib/verdict_adapters/README.md exists"
else
    fail "AC-12: adapter seam README missing"
fi
if grep -qE '^[[:space:]]*"--adapter"' "$ENGINE"; then
    fail "AC-12: engine exposes --adapter CLI flag (contract violation)"
else
    pass "AC-12: engine does not expose --adapter CLI flag"
fi
if grep -q 'def detect_adapter' "$ENGINE"; then
    pass "AC-12: engine has internal detect_adapter seam"
else
    fail "AC-12: engine missing detect_adapter function"
fi

# AC-7: wrapper function declaration present; upsert_state_fields signature
# unchanged; no extra sed-on-current_round paths outside the wrapper.
if grep -q 'update_round_state_with_verdict()' "$WRAPPER_FILE"; then
    pass "AC-7: wrapper function declared in loop-common.sh"
else
    fail "AC-7: wrapper function declaration missing"
fi
if grep -q '^upsert_state_fields() {' "$WRAPPER_FILE"; then
    pass "AC-7: upsert_state_fields signature preserved"
else
    fail "AC-7: upsert_state_fields signature changed or missing"
fi

# AC-1a / AC-1b / AC-1c / AC-1d: count the wrapper call sites in the stop
# hook. 3 gated + 6 logged-only = 9 wrapper calls total.
wrapper_call_count=$(grep -c 'update_round_state_with_verdict' "$STOP_HOOK")
if [[ "$wrapper_call_count" -eq 9 ]]; then
    pass "AC-1a..1d: 9 wrapper calls present in stop hook (3 gated + 6 logged-only)"
else
    fail "AC-1a..1d: expected 9 wrapper calls, got $wrapper_call_count"
fi

# Verify each transition string is referenced at least once.
for transition in next_round review_fix enter_finalize finalize_completion stop_marker maxiter mainline_drift review_start complete_at_maxiter; do
    if grep -qE "\"$transition\"" "$STOP_HOOK"; then
        pass "AC-1: transition \"$transition\" wired into stop hook"
    else
        fail "AC-1: transition \"$transition\" missing from stop hook"
    fi
done

# AC-1e / AC-22: methodology-analysis.sh has zero wrapper calls.
if grep -q 'update_round_state_with_verdict' "$METHODOLOGY_HOOK"; then
    fail "AC-1e/AC-22: methodology-analysis.sh must not invoke the wrapper"
else
    pass "AC-1e/AC-22: methodology-analysis.sh has no wrapper calls (boundary preserved)"
fi

# AC-11: TODO-KB-PROVENANCE sentinel counts unchanged in heredoc-emitting
# files. The test-kb-provenance-format.sh suite also enforces this; here we
# add a redundant cardinality check so this engine's tests fail fast on a
# regression rather than waiting for the other suite.
hook_sentinels=$(grep -cF 'TODO-KB-PROVENANCE' "$STOP_HOOK")
setup_sentinels=$(grep -cF 'TODO-KB-PROVENANCE' "$PROJECT_ROOT/scripts/setup-rlcr-loop.sh")
if [[ "$hook_sentinels" -eq 2 ]] && [[ "$setup_sentinels" -eq 1 ]]; then
    pass "AC-11: TODO-KB-PROVENANCE sentinel counts preserved (hook=2, setup=1)"
else
    fail "AC-11: sentinel count drift (hook=$hook_sentinels expected 2; setup=$setup_sentinels expected 1)"
fi

# AC-16: engine emits schema_version "1.0" in JSONL (grep on the engine
# source itself; the functional tests below also assert on actual rows).
if grep -q 'SCHEMA_VERSION = "1.0"' "$ENGINE"; then
    pass "AC-16: engine SCHEMA_VERSION constant set to 1.0"
else
    fail "AC-16: engine SCHEMA_VERSION constant missing"
fi

# ------------------------------------------------------------------
# Functional tests: gated transitions and verdict matrix
# ------------------------------------------------------------------

# AC-2 + AC-1a + AC-16 + AC-3 + AC-4 + AC-19 happy path
TMP1=$(mktemp -d)
LOOP1=$(setup_test_env "$TMP1")
SIDECAR1=$(make_sidecar "$LOOP1" 1 "$FIXTURE_DIR/manifest-v2-clean.json")
rc=$(run_engine "$TMP1" "$LOOP1" 1)
if [[ "$rc" == "0" ]]; then
    pass "AC-1a/AC-2 (happy path): gated next_round engine exit 0"
else
    fail "AC-1a/AC-2 happy path: engine returned $rc, expected 0"
fi

row=$(last_jsonl_row "$LOOP1")
if python3 -c "import json,sys; r=json.loads(sys.argv[1]); assert r['schema_version']=='1.0'" "$row" 2>/dev/null; then
    pass "AC-16: JSONL row carries schema_version 1.0"
else
    fail "AC-16: JSONL row missing schema_version 1.0"
fi
if python3 -c "import json,sys; r=json.loads(sys.argv[1]); assert r['sol_score']['value']=='unknown_t_sol' and r['sol_score']['provenance']['authority']=='local_proxy_5060' and r['sol_score']['provenance']['leaderboard_comparable'] is False" "$row" 2>/dev/null; then
    pass "AC-3: SOL score nested provenance forwarded verbatim"
else
    fail "AC-3: SOL score nested provenance malformed in JSONL"
fi

# AC-4 per-surface preservation (mixed manifest with 5 surfaces, 1 blocked +
# 1 failed + 3 ok).
TMP_M=$(mktemp -d)
LOOP_M=$(setup_test_env "$TMP_M")
SIDECAR_M=$(make_sidecar "$LOOP_M" 1 "$FIXTURE_DIR/manifest-v2-mixed.json" '{"required_surfaces": []}')
run_engine "$TMP_M" "$LOOP_M" 1 >/dev/null
row_m=$(last_jsonl_row "$LOOP_M")
if python3 -c "import json,sys; r=json.loads(sys.argv[1]); s=r['surfaces']; assert len(s)==5 and all('status' in e and 'name' in e for e in s)" "$row_m" 2>/dev/null; then
    pass "AC-4: 5 per-surface entries preserved (no failed_count collapse)"
else
    fail "AC-4: per-surface manifest preservation failed"
fi
if python3 -c "import json,sys; r=json.loads(sys.argv[1]); assert 'failed_count' not in r" "$row_m" 2>/dev/null; then
    pass "AC-4 negative: JSONL row does not collapse to failed_count"
else
    fail "AC-4 negative: JSONL row collapsed to failed_count"
fi
cleanup_env "$TMP_M"

# AC-19: exactly-one JSONL row per attempted transition.
n_rows=$(count_jsonl_rows "$LOOP1")
if [[ "$n_rows" -eq 1 ]]; then
    pass "AC-19: exactly one JSONL row appended per transition"
else
    fail "AC-19: expected 1 JSONL row, got $n_rows"
fi
cleanup_env "$TMP1"

# AC-2 negative: malformed sidecar -> hard-block (exit 1).
TMP2=$(mktemp -d)
LOOP2=$(setup_test_env "$TMP2")
printf '%s' '{not valid json' > "$LOOP2/round-1-objectives.json"
rc=$(run_engine "$TMP2" "$LOOP2" 1)
if [[ "$rc" == "1" ]]; then
    pass "AC-2 negative: malformed sidecar -> exit 1 (no silent default)"
else
    fail "AC-2 negative: malformed sidecar exit $rc, expected 1"
fi
row2=$(last_jsonl_row "$LOOP2")
if python3 -c "import json,sys; r=json.loads(sys.argv[1]); assert r['block_reason']=='sidecar_malformed' and r['verdict_blocked'] is True" "$row2" 2>/dev/null; then
    pass "AC-2 negative: block_reason=sidecar_malformed in JSONL row"
else
    fail "AC-2 negative: expected block_reason=sidecar_malformed"
fi
cleanup_env "$TMP2"

# AC-5a: correctness.passed=false -> hard-block.
TMP3=$(mktemp -d)
LOOP3=$(setup_test_env "$TMP3")
make_sidecar "$LOOP3" 1 "$FIXTURE_DIR/manifest-v2-clean.json" \
    '{"correctness": {"passed": false, "tests_passed": 0, "tests_failed": 9}}' >/dev/null
rc=$(run_engine "$TMP3" "$LOOP3" 1)
if [[ "$rc" == "1" ]]; then
    pass "AC-5a: correctness.passed=false -> exit 1"
else
    fail "AC-5a: correctness.passed=false exit $rc, expected 1"
fi
row3=$(last_jsonl_row "$LOOP3")
if python3 -c "import json,sys; r=json.loads(sys.argv[1]); assert r['block_reason']=='correctness_failed' and r['verdict_blocked'] is True" "$row3" 2>/dev/null; then
    pass "AC-5a: block_reason=correctness_failed"
else
    fail "AC-5a: block_reason wrong"
fi
cleanup_env "$TMP3"

# AC-5b: required-surface failed -> hard-block.
TMP4=$(mktemp -d)
LOOP4=$(setup_test_env "$TMP4")
make_sidecar "$LOOP4" 1 "$FIXTURE_DIR/manifest-v2-failed-nvbit.json" \
    '{"required_surfaces": ["nvbit", "cupti_activity"]}' >/dev/null
rc=$(run_engine "$TMP4" "$LOOP4" 1)
if [[ "$rc" == "1" ]]; then
    pass "AC-5b: required surface failed -> exit 1"
else
    fail "AC-5b: required surface failed exit $rc, expected 1"
fi
row4=$(last_jsonl_row "$LOOP4")
if python3 -c "import json,sys; r=json.loads(sys.argv[1]); assert r['block_reason']=='required_surface_failed:nvbit'" "$row4" 2>/dev/null; then
    pass "AC-5b: block_reason=required_surface_failed:nvbit"
else
    fail "AC-5b: block_reason wrong (expected required_surface_failed:nvbit)"
fi
cleanup_env "$TMP4"

# AC-5b negative: non-required surface failed -> allow advance.
TMP5=$(mktemp -d)
LOOP5=$(setup_test_env "$TMP5")
make_sidecar "$LOOP5" 1 "$FIXTURE_DIR/manifest-v2-mixed.json" \
    '{"required_surfaces": ["nvbit", "cupti_activity"]}' >/dev/null
rc=$(run_engine "$TMP5" "$LOOP5" 1)
if [[ "$rc" == "0" ]]; then
    pass "AC-5b negative: non-required failed surface (static) -> exit 0"
else
    fail "AC-5b negative: expected exit 0 with non-required failure, got $rc"
fi
cleanup_env "$TMP5"

# AC-5c: required latency, threshold breach -> hard-block.
TMP6=$(mktemp -d)
LOOP6=$(setup_test_env "$TMP6")
make_sidecar "$LOOP6" 1 "$FIXTURE_DIR/manifest-v2-clean.json" \
    '{"latency": {"required": true, "delta_pct": -7.2, "threshold_pct": -5.0, "basis": "cupti_activity"}}' >/dev/null
rc=$(run_engine "$TMP6" "$LOOP6" 1)
if [[ "$rc" == "1" ]]; then
    pass "AC-5c: required latency below threshold -> exit 1"
else
    fail "AC-5c: required latency threshold breach exit $rc, expected 1"
fi
row6=$(last_jsonl_row "$LOOP6")
if python3 -c "import json,sys; r=json.loads(sys.argv[1]); assert r['block_reason']=='required_latency_threshold_breach'" "$row6" 2>/dev/null; then
    pass "AC-5c: block_reason=required_latency_threshold_breach"
else
    fail "AC-5c: block_reason wrong"
fi
cleanup_env "$TMP6"

# AC-5c negative: required latency within threshold -> allow.
TMP7=$(mktemp -d)
LOOP7=$(setup_test_env "$TMP7")
make_sidecar "$LOOP7" 1 "$FIXTURE_DIR/manifest-v2-clean.json" \
    '{"latency": {"required": true, "delta_pct": -3.0, "threshold_pct": -5.0, "basis": "cupti_activity"}}' >/dev/null
rc=$(run_engine "$TMP7" "$LOOP7" 1)
if [[ "$rc" == "0" ]]; then
    pass "AC-5c negative: required latency within threshold -> exit 0"
else
    fail "AC-5c negative: expected exit 0, got $rc"
fi
cleanup_env "$TMP7"

# AC-5d: advisory latency with significant regression -> soft-warn (exit 2).
TMP8=$(mktemp -d)
LOOP8=$(setup_test_env "$TMP8")
make_sidecar "$LOOP8" 1 "$FIXTURE_DIR/manifest-v2-clean.json" \
    '{"latency": {"required": false, "delta_pct": -15.0, "threshold_pct": null, "basis": "cupti_activity"}}' >/dev/null
rc=$(run_engine "$TMP8" "$LOOP8" 1)
if [[ "$rc" == "2" ]]; then
    pass "AC-5d: advisory latency regression -> exit 2 (soft-warn)"
else
    fail "AC-5d: advisory latency exit $rc, expected 2"
fi
row8=$(last_jsonl_row "$LOOP8")
if python3 -c "import json,sys; r=json.loads(sys.argv[1]); assert r['verdict_warned'] is True" "$row8" 2>/dev/null; then
    pass "AC-5d: JSONL row has verdict_warned=true"
else
    fail "AC-5d: verdict_warned missing or false"
fi
cleanup_env "$TMP8"

# AC-5e: leaderboard required + unknown_t_sol -> hard-block.
TMP9=$(mktemp -d)
LOOP9=$(setup_test_env "$TMP9")
make_sidecar "$LOOP9" 1 "$FIXTURE_DIR/manifest-v2-clean.json" \
    '{"leaderboard_comparable_required": true}' >/dev/null
rc=$(run_engine "$TMP9" "$LOOP9" 1)
if [[ "$rc" == "1" ]]; then
    pass "AC-5e: leaderboard required + unknown -> exit 1"
else
    fail "AC-5e: leaderboard required + unknown exit $rc, expected 1"
fi
row9=$(last_jsonl_row "$LOOP9")
if python3 -c "import json,sys; r=json.loads(sys.argv[1]); assert r['block_reason']=='leaderboard_comparable_required_but_unknown'" "$row9" 2>/dev/null; then
    pass "AC-5e: block_reason=leaderboard_comparable_required_but_unknown"
else
    fail "AC-5e: block_reason wrong"
fi
cleanup_env "$TMP9"

# AC-5e negative 1: leaderboard required + known value -> allow.
TMP10=$(mktemp -d)
LOOP10=$(setup_test_env "$TMP10")
make_sidecar "$LOOP10" 1 "$FIXTURE_DIR/manifest-v2-clean.json" \
    '{"leaderboard_comparable_required": true, "sol_score": {"value": 0.85, "provenance": {"authority": "local_proxy_5060", "basis": "solar_stage4_registered", "leaderboard_comparable": true}}}' >/dev/null
rc=$(run_engine "$TMP10" "$LOOP10" 1)
if [[ "$rc" == "0" ]]; then
    pass "AC-5e negative: leaderboard required + known value -> exit 0"
else
    fail "AC-5e negative: expected exit 0, got $rc"
fi
cleanup_env "$TMP10"

# AC-5e negative 2: leaderboard NOT required + unknown -> allow.
TMP11=$(mktemp -d)
LOOP11=$(setup_test_env "$TMP11")
make_sidecar "$LOOP11" 1 "$FIXTURE_DIR/manifest-v2-clean.json" >/dev/null
rc=$(run_engine "$TMP11" "$LOOP11" 1)
if [[ "$rc" == "0" ]]; then
    pass "AC-5e negative: leaderboard not required + unknown -> exit 0"
else
    fail "AC-5e negative: expected exit 0, got $rc"
fi
cleanup_env "$TMP11"

# AC-6a: rule 1 violated -> hard-block.
TMP12=$(mktemp -d)
LOOP12=$(setup_test_env "$TMP12")
make_sidecar "$LOOP12" 1 "$FIXTURE_DIR/manifest-v2-clean.json" \
    '{"rule_compliance": {"rule_1_no_ncu": {"status": "violated", "evidence": "ncu invocation detected"}}}' >/dev/null
rc=$(run_engine "$TMP12" "$LOOP12" 1)
if [[ "$rc" == "1" ]]; then
    pass "AC-6a: rule_1 violated -> exit 1"
else
    fail "AC-6a: rule_1 violated exit $rc, expected 1"
fi
row12=$(last_jsonl_row "$LOOP12")
if python3 -c "import json,sys; r=json.loads(sys.argv[1]); assert r['block_reason']=='rule_violation:rule_1_no_ncu'" "$row12" 2>/dev/null; then
    pass "AC-6a: block_reason=rule_violation:rule_1_no_ncu"
else
    fail "AC-6a: block_reason wrong"
fi
cleanup_env "$TMP12"

# AC-6b: rule 7 violated + required true -> hard-block.
TMP13=$(mktemp -d)
LOOP13=$(setup_test_env "$TMP13")
make_sidecar "$LOOP13" 1 "$FIXTURE_DIR/manifest-v2-clean.json" \
    '{"rule_compliance": {"rule_7_default_stream": {"status": "violated", "evidence": "non-default stream used"}}, "rule_required_by_objective": {"rule_7": true}}' >/dev/null
rc=$(run_engine "$TMP13" "$LOOP13" 1)
if [[ "$rc" == "1" ]]; then
    pass "AC-6b: rule_7 violated + required true -> exit 1"
else
    fail "AC-6b: expected exit 1, got $rc"
fi
cleanup_env "$TMP13"

# AC-6b ledger: rule 7 violated + required false -> exit 0 (ledger only).
TMP14=$(mktemp -d)
LOOP14=$(setup_test_env "$TMP14")
make_sidecar "$LOOP14" 1 "$FIXTURE_DIR/manifest-v2-clean.json" \
    '{"rule_compliance": {"rule_7_default_stream": {"status": "violated", "evidence": null}}, "rule_required_by_objective": {"rule_7": false}}' >/dev/null
rc=$(run_engine "$TMP14" "$LOOP14" 1)
if [[ "$rc" == "0" ]]; then
    pass "AC-6b ledger: rule_7 violated + required false -> exit 0"
else
    fail "AC-6b ledger: expected exit 0, got $rc"
fi
cleanup_env "$TMP14"

# AC-6c: rule 9 violated with evidence -> hard-block.
TMP15=$(mktemp -d)
LOOP15=$(setup_test_env "$TMP15")
make_sidecar "$LOOP15" 1 "$FIXTURE_DIR/manifest-v2-clean.json" \
    '{"rule_compliance": {"rule_9_iiswc_no_access": {"status": "violated", "evidence": "transcript references IISWC PDF"}}}' >/dev/null
rc=$(run_engine "$TMP15" "$LOOP15" 1)
if [[ "$rc" == "1" ]]; then
    pass "AC-6c: rule_9 violated + evidence -> exit 1"
else
    fail "AC-6c: expected exit 1, got $rc"
fi
cleanup_env "$TMP15"

# AC-6d: not_evaluated + required -> hard-block.
TMP16=$(mktemp -d)
LOOP16=$(setup_test_env "$TMP16")
make_sidecar "$LOOP16" 1 "$FIXTURE_DIR/manifest-v2-clean.json" \
    '{"rule_compliance": {"rule_1_no_ncu": {"status": "not_evaluated", "evidence": null}}, "rule_required_by_objective": {"rule_1_no_ncu": true}}' >/dev/null
rc=$(run_engine "$TMP16" "$LOOP16" 1)
if [[ "$rc" == "1" ]]; then
    pass "AC-6d: not_evaluated + required true -> exit 1"
else
    fail "AC-6d: expected exit 1, got $rc"
fi
cleanup_env "$TMP16"

# AC-13: sidecar identity validation - round mismatch.
TMP17=$(mktemp -d)
LOOP17=$(setup_test_env "$TMP17" 4)
make_sidecar "$LOOP17" 5 "$FIXTURE_DIR/manifest-v2-clean.json" >/dev/null
mv "$LOOP17/round-5-objectives.json" "$LOOP17/round-4-objectives.json"
rc=$(run_engine "$TMP17" "$LOOP17" 4)
if [[ "$rc" == "1" ]]; then
    pass "AC-13: round mismatch -> exit 1"
else
    fail "AC-13: round mismatch exit $rc, expected 1"
fi
row17=$(last_jsonl_row "$LOOP17")
if python3 -c "import json,sys; r=json.loads(sys.argv[1]); assert r['block_reason'].startswith('sidecar_identity_mismatch')" "$row17" 2>/dev/null; then
    pass "AC-13: block_reason starts with sidecar_identity_mismatch"
else
    fail "AC-13: block_reason wrong"
fi
cleanup_env "$TMP17"

# AC-13 missing field: removing manifest_path -> hard-block.
TMP18=$(mktemp -d)
LOOP18=$(setup_test_env "$TMP18")
make_sidecar "$LOOP18" 1 "$FIXTURE_DIR/manifest-v2-clean.json" >/dev/null
python3 -c "import json,sys; p=sys.argv[1]; d=json.load(open(p)); d.pop('manifest_path'); json.dump(d, open(p,'w'))" "$LOOP18/round-1-objectives.json"
rc=$(run_engine "$TMP18" "$LOOP18" 1)
if [[ "$rc" == "1" ]]; then
    pass "AC-13: missing identity field -> exit 1"
else
    fail "AC-13: missing field exit $rc, expected 1"
fi
cleanup_env "$TMP18"

# AC-13 stale: generated_at older than 24h -> hard-block.
TMP19=$(mktemp -d)
LOOP19=$(setup_test_env "$TMP19")
make_sidecar "$LOOP19" 1 "$FIXTURE_DIR/manifest-v2-clean.json" >/dev/null
python3 -c "import json,sys; p=sys.argv[1]; d=json.load(open(p)); d['generated_at']='2020-01-01T00:00:00Z'; json.dump(d, open(p,'w'))" "$LOOP19/round-1-objectives.json"
rc=$(run_engine "$TMP19" "$LOOP19" 1)
if [[ "$rc" == "1" ]]; then
    pass "AC-13: stale generated_at -> exit 1"
else
    fail "AC-13: stale exit $rc, expected 1"
fi
cleanup_env "$TMP19"

# AC-14 + AC-17: adapter-active (via problems dir) + sidecar absent ->
# blocked row, exit 1.
TMP20=$(mktemp -d)
LOOP20=$(setup_test_env "$TMP20" 1 test-loop false)
mkdir -p "$TMP20/.claude/knowledge/problems"
printf '%s' '# problem' > "$TMP20/.claude/knowledge/problems/example.md"
rc=$(run_engine "$TMP20" "$LOOP20" 1)
if [[ "$rc" == "1" ]]; then
    pass "AC-14/AC-17: adapter-active + sidecar absent -> exit 1"
else
    fail "AC-14/AC-17: expected exit 1, got $rc"
fi
row20=$(last_jsonl_row "$LOOP20")
if python3 -c "import json,sys; r=json.loads(sys.argv[1]); assert r['mode']=='blocked' and r['block_reason']=='adapter_active_sidecar_absent'" "$row20" 2>/dev/null; then
    pass "AC-14/AC-17: blocked row mode=blocked, reason=adapter_active_sidecar_absent"
else
    fail "AC-14/AC-17: row mode/reason wrong"
fi
cleanup_env "$TMP20"

# AC-14 + AC-18: adapter-absent (all 3 predicates negative) + sidecar absent
# -> skipped row, exit 0.
TMP21=$(mktemp -d)
LOOP21=$(setup_test_env "$TMP21" 1 test-loop false)
rc=$(run_engine "$TMP21" "$LOOP21" 1)
if [[ "$rc" == "0" ]]; then
    pass "AC-14/AC-18: adapter-absent + sidecar absent -> exit 0 (fail-open)"
else
    fail "AC-14/AC-18: expected exit 0, got $rc"
fi
row21=$(last_jsonl_row "$LOOP21")
if python3 -c "import json,sys; r=json.loads(sys.argv[1]); assert r['mode']=='skipped_no_adapter'" "$row21" 2>/dev/null; then
    pass "AC-14/AC-18: mode=skipped_no_adapter row recorded"
else
    fail "AC-14/AC-18: mode wrong"
fi
cleanup_env "$TMP21"

# AC-18 positive: one predicate positive (adapter-config.json declares
# solbench) and sidecar absent -> fail-closed.
TMP22=$(mktemp -d)
LOOP22=$(setup_test_env "$TMP22" 1 test-loop true)
rc=$(run_engine "$TMP22" "$LOOP22" 1)
if [[ "$rc" == "1" ]]; then
    pass "AC-18: adapter-config.json positive + sidecar absent -> exit 1"
else
    fail "AC-18: expected exit 1, got $rc"
fi
cleanup_env "$TMP22"

# AC-15: mutation ordering on hard-block - state.md byte-identical, no
# next-round artifacts created, exactly one JSONL row appended.
TMP23=$(mktemp -d)
LOOP23=$(setup_test_env "$TMP23")
make_sidecar "$LOOP23" 1 "$FIXTURE_DIR/manifest-v2-clean.json" \
    '{"correctness": {"passed": false}}' >/dev/null
before=$(state_sha "$LOOP23")
run_engine "$TMP23" "$LOOP23" 1 >/dev/null
after=$(state_sha "$LOOP23")
if [[ "$before" == "$after" ]]; then
    pass "AC-15: state.md byte-identical after hard-block"
else
    fail "AC-15: state.md mutated on hard-block ($before -> $after)"
fi
for name in finalize-state.md complete-state.md stop-state.md maxiter-state.md round-2-prompt.md; do
    if [[ -f "$LOOP23/$name" ]]; then
        fail "AC-15: hard-block created forbidden artifact $name"
    else
        pass "AC-15: hard-block did not create $name"
    fi
done
n23=$(count_jsonl_rows "$LOOP23")
if [[ "$n23" -eq 1 ]]; then
    pass "AC-15/AC-19: hard-block appended exactly one JSONL row"
else
    fail "AC-15/AC-19: expected 1 row, got $n23"
fi
cleanup_env "$TMP23"

# AC-21: complete_at_maxiter LOGGED-ONLY classification (mode=logged_only,
# terminal_reason=complete_at_maxiter, exit 0 even when engine would
# otherwise hard-block).
TMP24=$(mktemp -d)
LOOP24=$(setup_test_env "$TMP24")
make_sidecar "$LOOP24" 1 "$FIXTURE_DIR/manifest-v2-clean.json" \
    '{"correctness": {"passed": false}}' >/dev/null
rc=$(run_engine "$TMP24" "$LOOP24" 1 complete_at_maxiter logged_only)
if [[ "$rc" == "0" ]]; then
    pass "AC-21: complete_at_maxiter logged-only -> exit 0 even on failing sidecar"
else
    fail "AC-21: expected exit 0 in logged-only mode, got $rc"
fi
row24=$(last_jsonl_row "$LOOP24")
if python3 -c "import json,sys; r=json.loads(sys.argv[1]); assert r['terminal_reason']=='complete_at_maxiter' and r['mode']=='logged_only'" "$row24" 2>/dev/null; then
    pass "AC-21: JSONL row terminal_reason=complete_at_maxiter, mode=logged_only"
else
    fail "AC-21: terminal_reason/mode wrong"
fi
cleanup_env "$TMP24"

# AC-1d coverage: each of the 6 logged-only transitions returns exit 0 in
# the engine and records a JSONL row with mode=logged_only.
for transition in finalize_completion stop_marker maxiter mainline_drift review_start complete_at_maxiter; do
    TMPL=$(mktemp -d)
    LOOPL=$(setup_test_env "$TMPL")
    make_sidecar "$LOOPL" 1 "$FIXTURE_DIR/manifest-v2-clean.json" >/dev/null
    rc=$(run_engine "$TMPL" "$LOOPL" 1 "$transition" logged_only)
    if [[ "$rc" == "0" ]]; then
        pass "AC-1d: logged-only transition $transition returned exit 0"
    else
        fail "AC-1d: logged-only $transition returned $rc"
    fi
    rowL=$(last_jsonl_row "$LOOPL")
    if python3 -c "import json,sys,os; r=json.loads(sys.argv[1]); t=os.environ.get('T'); assert r['mode']=='logged_only' and r['transition']==t" "$rowL" 2>/dev/null T="$transition" \
        || T="$transition" python3 -c "import json,sys,os; r=json.loads(sys.argv[1]); t=os.environ['T']; assert r['mode']=='logged_only' and r['transition']==t" "$rowL" 2>/dev/null; then
        pass "AC-1d: $transition JSONL row mode=logged_only transition=$transition"
    else
        fail "AC-1d: $transition JSONL row mismatch"
    fi
    cleanup_env "$TMPL"
done

# AC-20: bash wrapper propagates Python exit code.
WRAPPER_TEST_TMP=$(mktemp -d)
WRAPPER_LOOP=$(setup_test_env "$WRAPPER_TEST_TMP")
make_sidecar "$WRAPPER_LOOP" 1 "$FIXTURE_DIR/manifest-v2-clean.json" \
    '{"correctness": {"passed": false}}' >/dev/null

WRAPPER_RC=$(
    bash -c "
        source '$WRAPPER_FILE' 2>/dev/null
        update_round_state_with_verdict gated next_round '$WRAPPER_LOOP' 1
        echo \$?
    " 2>/dev/null | tail -1
)
if [[ "$WRAPPER_RC" == "1" ]]; then
    pass "AC-20: bash wrapper propagates engine exit 1 (hard-block)"
else
    fail "AC-20: bash wrapper returned $WRAPPER_RC, expected 1"
fi
cleanup_env "$WRAPPER_TEST_TMP"

# AC-20 continued: soft-warn (exit 2) propagation.
WRAPPER_WARN_TMP=$(mktemp -d)
WRAPPER_WARN_LOOP=$(setup_test_env "$WRAPPER_WARN_TMP")
make_sidecar "$WRAPPER_WARN_LOOP" 1 "$FIXTURE_DIR/manifest-v2-clean.json" \
    '{"latency": {"required": false, "delta_pct": -15.0, "threshold_pct": null, "basis": "cupti_activity"}}' >/dev/null

WRAPPER_WARN_RC=$(
    bash -c "
        source '$WRAPPER_FILE' 2>/dev/null
        update_round_state_with_verdict gated next_round '$WRAPPER_WARN_LOOP' 1
        echo \$?
    " 2>/dev/null | tail -1
)
if [[ "$WRAPPER_WARN_RC" == "2" ]]; then
    pass "AC-20: bash wrapper propagates engine exit 2 (soft-warn)"
else
    fail "AC-20: bash wrapper warn returned $WRAPPER_WARN_RC, expected 2"
fi
cleanup_env "$WRAPPER_WARN_TMP"

# AC-20 logged-only: wrapper returns 0 regardless of engine exit.
WRAPPER_LO_TMP=$(mktemp -d)
WRAPPER_LO_LOOP=$(setup_test_env "$WRAPPER_LO_TMP")
make_sidecar "$WRAPPER_LO_LOOP" 1 "$FIXTURE_DIR/manifest-v2-clean.json" \
    '{"correctness": {"passed": false}}' >/dev/null

WRAPPER_LO_RC=$(
    bash -c "
        source '$WRAPPER_FILE' 2>/dev/null
        update_round_state_with_verdict logged_only maxiter '$WRAPPER_LO_LOOP' 1
        echo \$?
    " 2>/dev/null | tail -1
)
if [[ "$WRAPPER_LO_RC" == "0" ]]; then
    pass "AC-20: bash wrapper logged_only returns 0 regardless of engine result"
else
    fail "AC-20: logged_only wrapper returned $WRAPPER_LO_RC, expected 0"
fi
cleanup_env "$WRAPPER_LO_TMP"

# AC-8: state.md frontmatter is scalars-only after a round transition (the
# wrapper itself does not add JSON-shaped values).
TMP25=$(mktemp -d)
LOOP25=$(setup_test_env "$TMP25")
make_sidecar "$LOOP25" 1 "$FIXTURE_DIR/manifest-v2-clean.json" >/dev/null
run_engine "$TMP25" "$LOOP25" 1 >/dev/null
frontmatter=$(awk '/^---$/{c++; next} c==1{print}' "$LOOP25/state.md")
if printf '%s' "$frontmatter" | grep -qE '\{|\[\['; then
    fail "AC-8: state.md frontmatter contains JSON-shaped value"
else
    pass "AC-8: state.md frontmatter remains scalars-only"
fi
cleanup_env "$TMP25"

# AC-22: methodology terminal-state rename is OUT OF SCOPE.
# Already asserted via the methodology-analysis.sh grep above; here we add a
# stronger structural check: the wrapper string must not appear within the
# methodology-analysis.sh sourceable file at all.
if grep -qE 'update_round_state_with_verdict|solbench_verdict_engine' "$METHODOLOGY_HOOK"; then
    fail "AC-22: methodology-analysis.sh references verdict engine (boundary violation)"
else
    pass "AC-22: methodology-analysis.sh does not reference verdict engine"
fi

# ------------------------------------------------------------------
# Hardening assertions (from Codex second-pass review)
# ------------------------------------------------------------------

# AC-1a integration: the stop-hook wrapper call at the next_round site must
# pass CURRENT_ROUND (the round that just completed), not NEXT_ROUND. The
# sidecar's `round` field pins to current_round at sidecar-write time;
# passing NEXT_ROUND looks up a sidecar that does not yet exist.
if grep -E '"gated" "next_round" "\$LOOP_DIR" "\$CURRENT_ROUND"' "$STOP_HOOK" >/dev/null; then
    pass "AC-1a integration: next_round wrapper passes CURRENT_ROUND"
else
    fail "AC-1a integration: next_round wrapper must pass CURRENT_ROUND, not NEXT_ROUND"
fi
if grep -E '"gated" "next_round" "\$LOOP_DIR" "\$NEXT_ROUND"' "$STOP_HOOK" >/dev/null; then
    fail "AC-1a integration: stale NEXT_ROUND wrapper call present"
else
    pass "AC-1a integration: no stale NEXT_ROUND wrapper call"
fi

# AC-1b integration: review_fix wrapper call must use CURRENT_ROUND (the
# round whose review was just run), not the function's local $round (which
# is CURRENT_ROUND + 1 and would look up a non-existent sidecar).
if grep -E '"gated" "review_fix" "\$LOOP_DIR" "\$CURRENT_ROUND"' "$STOP_HOOK" >/dev/null; then
    pass "AC-1b integration: review_fix wrapper passes CURRENT_ROUND"
else
    fail "AC-1b integration: review_fix wrapper must pass CURRENT_ROUND, not target round"
fi

# AC-5d drift increment: the wrapper exports VERDICT_ENGINE_DRIFT_INCREMENT
# on soft-warn so the next_round caller bumps NEXT_MAINLINE_STALL_COUNT.
WRAPPER_DRIFT_TMP=$(mktemp -d)
WRAPPER_DRIFT_LOOP=$(setup_test_env "$WRAPPER_DRIFT_TMP")
make_sidecar "$WRAPPER_DRIFT_LOOP" 1 "$FIXTURE_DIR/manifest-v2-clean.json" \
    '{"latency": {"required": false, "delta_pct": -15.0, "threshold_pct": null, "basis": "cupti_activity"}}' >/dev/null
WRAPPER_DRIFT_OUT=$(
    bash -c "
        source '$WRAPPER_FILE' 2>/dev/null
        update_round_state_with_verdict gated next_round '$WRAPPER_DRIFT_LOOP' 1
        rc=\$?
        echo \"rc=\$rc drift=\$VERDICT_ENGINE_DRIFT_INCREMENT\"
    " 2>/dev/null | tail -1
)
if [[ "$WRAPPER_DRIFT_OUT" == "rc=2 drift=true" ]]; then
    pass "AC-5d: wrapper exports VERDICT_ENGINE_DRIFT_INCREMENT=true on soft-warn"
else
    fail "AC-5d: drift export wrong (got: $WRAPPER_DRIFT_OUT)"
fi

WRAPPER_DRIFT_CLEAR_TMP=$(mktemp -d)
WRAPPER_DRIFT_CLEAR_LOOP=$(setup_test_env "$WRAPPER_DRIFT_CLEAR_TMP")
make_sidecar "$WRAPPER_DRIFT_CLEAR_LOOP" 1 "$FIXTURE_DIR/manifest-v2-clean.json" >/dev/null
WRAPPER_DRIFT_CLEAR_OUT=$(
    bash -c "
        source '$WRAPPER_FILE' 2>/dev/null
        update_round_state_with_verdict gated next_round '$WRAPPER_DRIFT_CLEAR_LOOP' 1
        rc=\$?
        echo \"rc=\$rc drift=\$VERDICT_ENGINE_DRIFT_INCREMENT\"
    " 2>/dev/null | tail -1
)
if [[ "$WRAPPER_DRIFT_CLEAR_OUT" == "rc=0 drift=false" ]]; then
    pass "AC-5d: wrapper clears VERDICT_ENGINE_DRIFT_INCREMENT on non-warn paths"
else
    fail "AC-5d: drift clear wrong (got: $WRAPPER_DRIFT_CLEAR_OUT)"
fi
cleanup_env "$WRAPPER_DRIFT_TMP"
cleanup_env "$WRAPPER_DRIFT_CLEAR_TMP"

# AC-6 hardening: a sidecar that omits a rule entry but marks the rule
# required by objective must hard-block under rule_not_evaluated. This
# catches the failure mode where dropping a rule from the JSON would
# silently bypass the 9-rule contract.
TMP_R1=$(mktemp -d)
LOOP_R1=$(setup_test_env "$TMP_R1")
make_sidecar "$LOOP_R1" 1 "$FIXTURE_DIR/manifest-v2-clean.json" \
    '{"rule_required_by_objective": {"rule_1_no_ncu": true}}' >/dev/null
python3 -c "import json,sys; p=sys.argv[1]; d=json.load(open(p)); d['rule_compliance'].pop('rule_1_no_ncu'); json.dump(d, open(p,'w'))" "$LOOP_R1/round-1-objectives.json"
rc=$(run_engine "$TMP_R1" "$LOOP_R1" 1)
if [[ "$rc" == "1" ]]; then
    pass "AC-6 hardening: missing required rule entry -> exit 1"
else
    fail "AC-6 hardening: missing required rule entry exit $rc, expected 1"
fi
cleanup_env "$TMP_R1"

# AC-6 hardening: an invalid rule status string (e.g., "VIOLATED" wrong
# case) on a required rule is treated as not_evaluated and hard-blocks.
TMP_R2=$(mktemp -d)
LOOP_R2=$(setup_test_env "$TMP_R2")
make_sidecar "$LOOP_R2" 1 "$FIXTURE_DIR/manifest-v2-clean.json" \
    '{"rule_compliance": {"rule_1_no_ncu": {"status": "INVALID_VALUE", "evidence": null}}, "rule_required_by_objective": {"rule_1_no_ncu": true}}' >/dev/null
rc=$(run_engine "$TMP_R2" "$LOOP_R2" 1)
if [[ "$rc" == "1" ]]; then
    pass "AC-6 hardening: invalid status + required -> exit 1"
else
    fail "AC-6 hardening: invalid status + required exit $rc, expected 1"
fi
cleanup_env "$TMP_R2"

# AC-6 hardening: invalid status on a NON-required rule does not block.
TMP_R3=$(mktemp -d)
LOOP_R3=$(setup_test_env "$TMP_R3")
make_sidecar "$LOOP_R3" 1 "$FIXTURE_DIR/manifest-v2-clean.json" \
    '{"rule_compliance": {"rule_1_no_ncu": {"status": "INVALID_VALUE", "evidence": null}}}' >/dev/null
rc=$(run_engine "$TMP_R3" "$LOOP_R3" 1)
if [[ "$rc" == "0" ]]; then
    pass "AC-6 hardening: invalid status + not required -> exit 0 (ledger only)"
else
    fail "AC-6 hardening: invalid status + not required exit $rc, expected 0"
fi
cleanup_env "$TMP_R3"

# AC-15 extended: additional artifact names that a sloppy hard-block
# implementation could create. Mutation-ordering must leave these absent.
TMP_M15=$(mktemp -d)
LOOP_M15=$(setup_test_env "$TMP_M15")
make_sidecar "$LOOP_M15" 1 "$FIXTURE_DIR/manifest-v2-clean.json" \
    '{"correctness": {"passed": false}}' >/dev/null
run_engine "$TMP_M15" "$LOOP_M15" 1 >/dev/null
for name in round-2-summary.md round-2-contract.md round-2-review-prompt.md round-2-review-result.md .review-phase-started finalize-summary.md state.md.tmp; do
    if compgen -G "$LOOP_M15/$name*" >/dev/null; then
        fail "AC-15 extended: hard-block created forbidden artifact matching $name"
    else
        pass "AC-15 extended: hard-block did not create $name"
    fi
done
cleanup_env "$TMP_M15"

# ------------------------------------------------------------------
# Round 1 hardening assertions (post Round-0 Codex review)
# ------------------------------------------------------------------

# AC-13: sidecar_schema_version is the required identity field. The JSONL
# row still carries the unrelated schema_version="1.0" but identity is
# validated against sidecar_schema_version.
if grep -q '"sidecar_schema_version"' "$ENGINE"; then
    pass "AC-13 (round 1): engine requires sidecar_schema_version"
else
    fail "AC-13 (round 1): engine missing sidecar_schema_version identity field"
fi

TMP_SV_MISS=$(mktemp -d)
LOOP_SV_MISS=$(setup_test_env "$TMP_SV_MISS")
make_sidecar "$LOOP_SV_MISS" 1 "$FIXTURE_DIR/manifest-v2-clean.json" >/dev/null
python3 -c "import json,sys; p=sys.argv[1]; d=json.load(open(p)); d.pop('sidecar_schema_version'); json.dump(d, open(p,'w'))" "$LOOP_SV_MISS/round-1-objectives.json"
rc=$(run_engine "$TMP_SV_MISS" "$LOOP_SV_MISS" 1)
if [[ "$rc" == "1" ]]; then
    pass "AC-13 (round 1): missing sidecar_schema_version -> exit 1"
else
    fail "AC-13 (round 1): missing sidecar_schema_version exit $rc, expected 1"
fi
row_sv=$(last_jsonl_row "$LOOP_SV_MISS")
if python3 -c "import json,sys; r=json.loads(sys.argv[1]); assert r['block_reason']=='sidecar_identity_invalid:missing_sidecar_schema_version'" "$row_sv" 2>/dev/null; then
    pass "AC-13 (round 1): block_reason=sidecar_identity_invalid:missing_sidecar_schema_version"
else
    fail "AC-13 (round 1): missing-field reason wrong"
fi
cleanup_env "$TMP_SV_MISS"

TMP_SV_WRONG=$(mktemp -d)
LOOP_SV_WRONG=$(setup_test_env "$TMP_SV_WRONG")
make_sidecar "$LOOP_SV_WRONG" 1 "$FIXTURE_DIR/manifest-v2-clean.json" \
    '{"sidecar_schema_version": "2.0"}' >/dev/null
rc=$(run_engine "$TMP_SV_WRONG" "$LOOP_SV_WRONG" 1)
if [[ "$rc" == "1" ]]; then
    pass "AC-13 (round 1): wrong sidecar_schema_version -> exit 1"
else
    fail "AC-13 (round 1): wrong sidecar_schema_version exit $rc, expected 1"
fi
row_svw=$(last_jsonl_row "$LOOP_SV_WRONG")
if python3 -c "import json,sys; r=json.loads(sys.argv[1]); assert r['block_reason']=='sidecar_identity_invalid:bad_sidecar_schema_version'" "$row_svw" 2>/dev/null; then
    pass "AC-13 (round 1): wrong sidecar_schema_version block_reason"
else
    fail "AC-13 (round 1): wrong sidecar_schema_version block_reason wrong"
fi
cleanup_env "$TMP_SV_WRONG"

# AC-13 state.md cross-check via --state-file. Sidecar round == --round but
# state.md frontmatter current_round disagrees -> hard-block.
TMP_STATE=$(mktemp -d)
LOOP_STATE=$(setup_test_env "$TMP_STATE" 2)
make_sidecar "$LOOP_STATE" 2 "$FIXTURE_DIR/manifest-v2-clean.json" >/dev/null
sed -i 's/^current_round:.*/current_round: 5/' "$LOOP_STATE/state.md"
rc=0
python3 "$ENGINE" \
    --loop-dir "$LOOP_STATE" \
    --round 2 \
    --state-file "$LOOP_STATE/state.md" \
    --project-root "$TMP_STATE" 2>/dev/null || rc=$?
if [[ "$rc" == "1" ]]; then
    pass "AC-13 (round 1): sidecar round vs state.md current_round mismatch -> exit 1"
else
    fail "AC-13 (round 1): state mismatch exit $rc, expected 1"
fi
row_state=$(last_jsonl_row "$LOOP_STATE")
if python3 -c "import json,sys; r=json.loads(sys.argv[1]); assert r['block_reason']=='sidecar_identity_mismatch:state_round'" "$row_state" 2>/dev/null; then
    pass "AC-13 (round 1): block_reason=sidecar_identity_mismatch:state_round"
else
    fail "AC-13 (round 1): state-mismatch reason wrong"
fi
cleanup_env "$TMP_STATE"

# AC-3 hardening: missing sol_score block -> hard-block.
TMP_SOL_MISS=$(mktemp -d)
LOOP_SOL_MISS=$(setup_test_env "$TMP_SOL_MISS")
make_sidecar "$LOOP_SOL_MISS" 1 "$FIXTURE_DIR/manifest-v2-clean.json" >/dev/null
python3 -c "import json,sys; p=sys.argv[1]; d=json.load(open(p)); d.pop('sol_score'); json.dump(d, open(p,'w'))" "$LOOP_SOL_MISS/round-1-objectives.json"
rc=$(run_engine "$TMP_SOL_MISS" "$LOOP_SOL_MISS" 1)
if [[ "$rc" == "1" ]]; then
    pass "AC-3 (round 1): missing sol_score -> exit 1"
else
    fail "AC-3 (round 1): missing sol_score exit $rc, expected 1"
fi
row_sol=$(last_jsonl_row "$LOOP_SOL_MISS")
if python3 -c "import json,sys; r=json.loads(sys.argv[1]); assert r['block_reason']=='sol_score_missing'" "$row_sol" 2>/dev/null; then
    pass "AC-3 (round 1): block_reason=sol_score_missing"
else
    fail "AC-3 (round 1): missing-sol_score reason wrong"
fi
cleanup_env "$TMP_SOL_MISS"

# AC-3 hardening: invalid sol_score.value type -> hard-block.
TMP_SOL_INV=$(mktemp -d)
LOOP_SOL_INV=$(setup_test_env "$TMP_SOL_INV")
make_sidecar "$LOOP_SOL_INV" 1 "$FIXTURE_DIR/manifest-v2-clean.json" \
    '{"sol_score": {"value": "not_a_number_or_sentinel", "provenance": {"authority": "local_proxy_5060", "basis": "x", "leaderboard_comparable": false}}}' >/dev/null
rc=$(run_engine "$TMP_SOL_INV" "$LOOP_SOL_INV" 1)
if [[ "$rc" == "1" ]]; then
    pass "AC-3 (round 1): invalid sol_score.value -> exit 1"
else
    fail "AC-3 (round 1): invalid value exit $rc, expected 1"
fi
cleanup_env "$TMP_SOL_INV"

# AC-3 hardening: missing sol_score.provenance.authority -> hard-block.
TMP_SOL_PROV=$(mktemp -d)
LOOP_SOL_PROV=$(setup_test_env "$TMP_SOL_PROV")
make_sidecar "$LOOP_SOL_PROV" 1 "$FIXTURE_DIR/manifest-v2-clean.json" >/dev/null
python3 -c "import json,sys; p=sys.argv[1]; d=json.load(open(p)); d['sol_score']['provenance'].pop('authority'); json.dump(d, open(p,'w'))" "$LOOP_SOL_PROV/round-1-objectives.json"
rc=$(run_engine "$TMP_SOL_PROV" "$LOOP_SOL_PROV" 1)
if [[ "$rc" == "1" ]]; then
    pass "AC-3 (round 1): missing provenance.authority -> exit 1"
else
    fail "AC-3 (round 1): missing authority exit $rc, expected 1"
fi
row_prov=$(last_jsonl_row "$LOOP_SOL_PROV")
if python3 -c "import json,sys; r=json.loads(sys.argv[1]); assert r['block_reason']=='sol_score_provenance_missing_authority'" "$row_prov" 2>/dev/null; then
    pass "AC-3 (round 1): block_reason=sol_score_provenance_missing_authority"
else
    fail "AC-3 (round 1): provenance-missing reason wrong"
fi
cleanup_env "$TMP_SOL_PROV"

# AC-6 ledger normalization: empty rule_compliance still emits 9 explicit
# not_evaluated entries in the JSONL row. The make_sidecar helper's
# deep_merge cannot null-out the rule_compliance dict, so the sidecar
# file is rewritten with rule_compliance forced to {} after generation.
TMP_RC_EMPTY=$(mktemp -d)
LOOP_RC_EMPTY=$(setup_test_env "$TMP_RC_EMPTY")
make_sidecar "$LOOP_RC_EMPTY" 1 "$FIXTURE_DIR/manifest-v2-clean.json" >/dev/null
python3 -c "import json,sys; p=sys.argv[1]; d=json.load(open(p)); d['rule_compliance']={}; json.dump(d, open(p,'w'))" "$LOOP_RC_EMPTY/round-1-objectives.json"
run_engine "$TMP_RC_EMPTY" "$LOOP_RC_EMPTY" 1 >/dev/null
row_rc=$(last_jsonl_row "$LOOP_RC_EMPTY")
if python3 -c "
import json, sys
r = json.loads(sys.argv[1])
rc = r['rule_compliance']
assert len(rc) == 9
for rid in ['rule_1_no_ncu','rule_2_no_clock_lock','rule_3_no_privileged_cupti','rule_4_no_host_driver_work','rule_5_submission_language','rule_6_no_evaluator_state_exploit','rule_7_default_stream','rule_8_precision_contract','rule_9_iiswc_no_access']:
    assert rid in rc, f'missing {rid}'
    assert rc[rid]['status'] == 'not_evaluated', f'bad status for {rid}: {rc[rid]}'
" "$row_rc" 2>/dev/null; then
    pass "AC-6 (round 1): empty rule_compliance emits 9 explicit not_evaluated entries"
else
    fail "AC-6 (round 1): rule_compliance normalization missing"
fi
cleanup_env "$TMP_RC_EMPTY"

# AC-8 setup defaults: setup-rlcr-loop.sh writes the four scalar fields.
SETUP_FILE="$PROJECT_ROOT/scripts/setup-rlcr-loop.sh"
for fld in verdict_mismatch verdict_mismatch_count last_computed_verdict last_block_reason; do
    if grep -qE "^${fld}:" "$SETUP_FILE"; then
        pass "AC-8 (round 1): setup-rlcr-loop.sh writes default for $fld"
    else
        fail "AC-8 (round 1): setup-rlcr-loop.sh missing default for $fld"
    fi
done

# AC-8 wrapper export: after a successful transition the wrapper export
# variables carry sensible values.
TMP_AC8=$(mktemp -d)
LOOP_AC8=$(setup_test_env "$TMP_AC8")
make_sidecar "$LOOP_AC8" 1 "$FIXTURE_DIR/manifest-v2-clean.json" >/dev/null
WRAPPER_AC8_OUT=$(
    bash -c "
        source '$WRAPPER_FILE' 2>/dev/null
        update_round_state_with_verdict gated next_round '$LOOP_AC8' 1 advanced
        echo \"computed=\$VERDICT_ENGINE_COMPUTED mismatch=\$VERDICT_ENGINE_MISMATCH block=\$VERDICT_ENGINE_BLOCK_REASON\"
    " 2>/dev/null | tail -1
)
if [[ "$WRAPPER_AC8_OUT" == "computed=advanced mismatch=false block=null" ]]; then
    pass "AC-8 (round 1): wrapper exports scalar verdict metadata on continue"
else
    fail "AC-8 (round 1): wrapper exports wrong (got: $WRAPPER_AC8_OUT)"
fi
cleanup_env "$TMP_AC8"

# AC-8 hard-block path: state.md byte-identical AND the wrapper exports
# block-reason so the next-non-hard-block transition can record it. Hard
# block itself MUST NOT mutate the state file (AC-15).
TMP_AC8_HB=$(mktemp -d)
LOOP_AC8_HB=$(setup_test_env "$TMP_AC8_HB")
make_sidecar "$LOOP_AC8_HB" 1 "$FIXTURE_DIR/manifest-v2-clean.json" \
    '{"correctness": {"passed": false}}' >/dev/null
before_hb=$(state_sha "$LOOP_AC8_HB")
WRAPPER_AC8_HB_OUT=$(
    bash -c "
        source '$WRAPPER_FILE' 2>/dev/null
        update_round_state_with_verdict gated next_round '$LOOP_AC8_HB' 1 advanced
        rc=\$?
        echo \"rc=\$rc block=\$VERDICT_ENGINE_BLOCK_REASON\"
    " 2>/dev/null | tail -1
)
after_hb=$(state_sha "$LOOP_AC8_HB")
if [[ "$before_hb" == "$after_hb" ]]; then
    pass "AC-8 / AC-15 (round 1): wrapper hard-block leaves state.md byte-identical"
else
    fail "AC-8 / AC-15 (round 1): state.md mutated on hard-block"
fi
if [[ "$WRAPPER_AC8_HB_OUT" == "rc=1 block=correctness_failed" ]]; then
    pass "AC-8 (round 1): wrapper exports block_reason on hard-block"
else
    fail "AC-8 (round 1): block-reason export wrong (got: $WRAPPER_AC8_HB_OUT)"
fi
cleanup_env "$TMP_AC8_HB"

# AC-8 mismatch counter: Codex says ADVANCED but engine computes blocked
# (correctness failure) -> wrapper flags mismatch=true. The stop-hook code
# bumps verdict_mismatch_count on the upsert path. Test the wrapper export
# only; the upsert is integration code already exercised by AC-1a grep.
TMP_MM=$(mktemp -d)
LOOP_MM=$(setup_test_env "$TMP_MM")
# Use a malformed sidecar that exits 1 with mismatch flagged: codex
# advanced + computed blocked yields verdict_mismatch=true.
make_sidecar "$LOOP_MM" 1 "$FIXTURE_DIR/manifest-v2-clean.json" \
    '{"correctness": {"passed": false}}' >/dev/null
WRAPPER_MM=$(
    bash -c "
        source '$WRAPPER_FILE' 2>/dev/null
        update_round_state_with_verdict gated next_round '$LOOP_MM' 1 advanced
        echo \"mismatch=\$VERDICT_ENGINE_MISMATCH\"
    " 2>/dev/null | tail -1
)
if [[ "$WRAPPER_MM" == "mismatch=true" ]]; then
    pass "AC-8 (round 1): wrapper flags verdict_mismatch when codex+computed disagree"
else
    fail "AC-8 (round 1): mismatch flag wrong (got: $WRAPPER_MM)"
fi
cleanup_env "$TMP_MM"

# AC-8 stop-hook integration: the upsert call at next_round transition
# now includes all four scalar field references.
for var in FIELD_VERDICT_MISMATCH FIELD_VERDICT_MISMATCH_COUNT FIELD_LAST_COMPUTED_VERDICT FIELD_LAST_BLOCK_REASON; do
    if grep -q "\${$var}=" "$STOP_HOOK"; then
        pass "AC-8 (round 1): stop-hook upsert references \${$var}"
    else
        fail "AC-8 (round 1): stop-hook missing \${$var} in upsert call"
    fi
done

print_test_summary "Solbench Verdict Engine Tests"
