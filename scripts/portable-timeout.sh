#!/usr/bin/env bash
#
# Portable timeout wrapper for macOS/Linux compatibility
# Usage: source portable-timeout.sh; run_with_timeout <seconds> <command> [args...]
#
# Priority: gtimeout (Homebrew) > timeout (GNU) > python3 > no timeout
#

# Detect available timeout implementation
detect_timeout_impl() {
    if command -v gtimeout &>/dev/null; then
        echo "gtimeout"
        return
    fi
    if command -v timeout &>/dev/null; then
        # Require recognizable GNU coreutils output to avoid matching shims
        # (shims typically output nothing for --version and lack "timeout" in output)
        if timeout --version 2>&1 | grep -qiE 'GNU|coreutils|timeout [0-9]'; then
            echo "timeout"
            return
        fi
    fi
    if command -v python3 &>/dev/null; then
        echo "python3"
        return
    fi
    if command -v python &>/dev/null; then
        echo "python"
        return
    fi
    echo "none"
}

TIMEOUT_IMPL=$(detect_timeout_impl)

# Run command with timeout
# Args: timeout_seconds command [args...]
run_with_timeout() {
    local timeout_secs="$1"
    shift
    local cmd=("$@")

    case "$TIMEOUT_IMPL" in
        gtimeout)
            gtimeout --kill-after=30s "$timeout_secs" "${cmd[@]}"
            return $?
            ;;
        timeout)
            timeout --kill-after=30s "$timeout_secs" "${cmd[@]}"
            return $?
            ;;
        python3|python)
            # Use Python's subprocess with timeout. Start a process group so
            # timeout cleanup reaches nested CLI children.
            "$TIMEOUT_IMPL" -c "
import os
import signal
import subprocess
import sys

try:
    proc = subprocess.Popen(sys.argv[1:], preexec_fn=os.setsid)
    try:
        proc.wait(timeout=$timeout_secs)
        sys.exit(proc.returncode)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=30)
        except Exception:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except Exception:
                pass
        sys.exit(124)  # Match GNU timeout exit code
except Exception as e:
    print(f'Error: {e}', file=sys.stderr)
    sys.exit(1)
" "${cmd[@]}"
            return $?
            ;;
        none)
            # No timeout available - run without timeout
            echo "Warning: No timeout implementation available. Running without timeout." >&2
            "${cmd[@]}"
            return $?
            ;;
    esac
}

# Make TIMEOUT_IMPL available to sourcing scripts
# Note: export -f is bash-specific, but not needed since the function
# is used directly in the sourcing script, not in child processes
export TIMEOUT_IMPL
