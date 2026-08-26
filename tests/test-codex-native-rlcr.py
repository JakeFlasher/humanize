#!/usr/bin/env python3
"""Protocol and state-machine tests for the Codex-native RLCR plugin."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "jakeshea-humanize-rlcr"
CONTROLLER = PLUGIN_ROOT / "scripts" / "rlcr.py"
SKILL_CONTROLLERS = tuple(
    PLUGIN_ROOT / "skills" / name / "scripts" / "rlcr.py"
    for name in ("humanize-rlcr", "humanize-plan", "humanize-review")
)
sys.dont_write_bytecode = True
sys.path.insert(0, str(PLUGIN_ROOT))

from controller import PLUGIN_VERSION  # pyright: ignore[reportMissingImports]
from controller.cli import (
    ControllerError,
    _has_required_reviewer_lanes,  # pyright: ignore[reportMissingImports]
    _validate_state,
)
from controller.migrations import migrate_state  # pyright: ignore[reportMissingImports]
from controller.review import (  # pyright: ignore[reportMissingImports]
    LaneResult,
    runtime_digest,
)

MOCK_CODEX = r"""#!/usr/bin/env python3
import json
import os
import pathlib
import re
import sys

root = pathlib.Path(__file__).resolve().parent
if sys.argv[1:] == ["--version"]:
    print("codex-cli 0.149.0-test")
    raise SystemExit(0)
if sys.argv[1:] == ["exec", "--help"]:
    print("--ephemeral --ignore-user-config --ignore-rules --output-schema --sandbox --disable --strict-config --json")
    raise SystemExit(0)

args = sys.argv[1:]
if "-a" in args or "--ask-for-approval" in args:
    print("approval flag is not valid after codex exec", file=sys.stderr)
    raise SystemExit(2)
prompt = sys.stdin.read()
lane_match = re.search(r"lane is `([^`]+)`", prompt)
digest_match = re.search(r"Artifact digest: `(sha256:[0-9a-f]{64})`", prompt)
project_match = re.search(r"Project root: `([^`]+)`", prompt)
patch_match = re.search(r"Cumulative patch snapshot: `([^`]+)`", prompt)
lane = lane_match.group(1) if lane_match else "unknown"
digest = digest_match.group(1) if digest_match else "sha256:" + "0" * 64
mode_path = root / "mock-mode.json"
mode_data = json.loads(mode_path.read_text()) if mode_path.exists() else {}
mode = mode_data.get(lane, mode_data.get("default", "accept"))

record = {
    "args": args,
    "guard": os.environ.get("JAKESHEA_HUMANIZE_RLCR_REVIEWER_CHILD"),
    "lane": lane,
    "prompt_has_materialized_patch": "Cumulative patch snapshot" in prompt and "Do not invoke\nGit" in prompt,
    "prompt_has_untrusted_boundary": "untrusted evidence" in prompt,
}
line = (json.dumps(record, sort_keys=True) + "\n").encode()
fd = os.open(root / "calls.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
try:
    os.write(fd, line)
finally:
    os.close(fd)

if mode == "fail":
    print(f"simulated {lane} failure", file=sys.stderr)
    raise SystemExit(7)
if mode == "sleep_changes":
    import time
    time.sleep(1)
    mode = "changes"
if mode == "sleep_long":
    import time
    time.sleep(30)
    mode = "accept"
if mode == "change_tree" and project_match:
    (pathlib.Path(project_match.group(1)) / "app.txt").write_text("changed during review\n")
if mode == "tamper_patch" and patch_match:
    pathlib.Path(patch_match.group(1)).write_text("forged patch\n")

output_path = pathlib.Path(args[args.index("-o") + 1])
if mode == "malformed":
    output_path.write_text("{}\n")
    print("{}")
    raise SystemExit(0)

changes = mode == "changes"
finding = {
    "severity": "high",
    "blocking": True,
    "category": "correctness" if lane == "correctness" else "plan_gap",
    "path": "app.txt",
    "start_line": 1,
    "end_line": 1,
    "claim": f"{lane} found a blocking defect",
    "evidence": ["app.txt:1 demonstrates the defect"],
    "remediation": "Correct the implementation and add regression coverage.",
    "acceptance_test": "The committed implementation satisfies the plan and its tests pass.",
    "scope_relation": "in_scope",
}
payload = {
    "schema_version": "rlcr.review.v1",
    "lane": lane,
    "artifact_digest": digest,
    "candidate_verdict": "changes_requested" if changes else "accept",
    "summary": f"{lane} review completed",
    "findings": [finding] if changes else [],
    "checks": [{"criterion_id": "AC-1", "status": "fail" if changes else "pass", "evidence": "review evidence"}],
    "residual_risks": [],
}
encoded = json.dumps(payload) + "\n"
output_path.write_text(encoded)
if "--json" in args:
    usage = mode_data.get(
        "usage",
        {
            "input_tokens": 100,
            "cached_input_tokens": 50,
            "output_tokens": 20,
            "reasoning_output_tokens": 5,
        },
    )
    print(json.dumps({"type": "turn.completed", "usage": usage}))
else:
    print(encoded, end="")
"""


class PluginPackagingTests(unittest.TestCase):
    def test_personal_identity_and_codex_only_boundary(self) -> None:
        manifest = json.loads(
            (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        marketplace = json.loads(
            (REPO_ROOT / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8")
        )
        hook_config = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        skill = (PLUGIN_ROOT / "skills" / "humanize-rlcr" / "SKILL.md").read_text(encoding="utf-8")
        skill_interface = (
            PLUGIN_ROOT / "skills" / "humanize-rlcr" / "agents" / "openai.yaml"
        ).read_text(encoding="utf-8")

        self.assertEqual(manifest["name"], "jakeshea-humanize-rlcr")
        self.assertEqual(manifest["version"], PLUGIN_VERSION)
        self.assertEqual(manifest["author"]["name"], "JakeShea")
        self.assertEqual(manifest["repository"], "https://github.com/JakeFlasher/humanize")
        self.assertEqual(marketplace["name"], "jakeshea-humanize")
        self.assertEqual(len(marketplace["plugins"]), 1)
        self.assertEqual(marketplace["plugins"][0]["name"], manifest["name"])
        self.assertEqual(
            marketplace["plugins"][0]["source"]["path"],
            "./plugins/jakeshea-humanize-rlcr",
        )
        self.assertRegex(skill, r"(?m)^name: humanize-rlcr$")
        self.assertIn("$jakeshea-humanize-rlcr:humanize-rlcr", skill_interface)

        stop_hooks = hook_config["hooks"]["Stop"]
        self.assertEqual(len(stop_hooks), 1)
        command_hook = stop_hooks[0]["hooks"][0]
        self.assertEqual(command_hook["type"], "command")
        self.assertIn("${PLUGIN_ROOT}/scripts/rlcr.py", command_hook["command"])
        self.assertEqual(command_hook["timeout"], 1800)

        expected_plugin_files = {
            ".codex-plugin/plugin.json",
            "controller/__init__.py",
            "controller/cli.py",
            "controller/config.py",
            "controller/consensus.py",
            "controller/contract.py",
            "controller/domain.py",
            "controller/evidence.py",
            "controller/migrations.py",
            "controller/processes.py",
            "controller/reporting.py",
            "controller/review.py",
            "controller/storage.py",
            "hooks/hooks.json",
            "prompts/correctness.md",
            "prompts/specification.md",
            "schemas/review-v1.json",
            "schemas/plan-contract-v1.json",
            "scripts/rlcr.py",
            "skills/humanize-plan/SKILL.md",
            "skills/humanize-plan/agents/openai.yaml",
            "skills/humanize-plan/references/contract-template.md",
            "skills/humanize-plan/scripts/rlcr.py",
            "skills/humanize-review/SKILL.md",
            "skills/humanize-review/agents/openai.yaml",
            "skills/humanize-review/scripts/rlcr.py",
            "skills/humanize-rlcr/SKILL.md",
            "skills/humanize-rlcr/agents/openai.yaml",
            "skills/humanize-rlcr/references/operations.md",
            "skills/humanize-rlcr/references/plan-contract.md",
            "skills/humanize-rlcr/scripts/rlcr.py",
        }
        actual_plugin_files = {
            path.relative_to(PLUGIN_ROOT).as_posix()
            for path in PLUGIN_ROOT.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        }
        self.assertEqual(actual_plugin_files, expected_plugin_files)

        forbidden_roots = {
            ".claude",
            ".claude-plugin",
            "agents",
            "commands",
            "config",
            "hooks",
            "prompt-template",
            "scripts",
            "skills",
            "templates",
            "viz",
        }
        present_forbidden = sorted(name for name in forbidden_roots if (REPO_ROOT / name).exists())
        self.assertEqual(present_forbidden, [])

        executable_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in PLUGIN_ROOT.rglob("*")
            if path.is_file() and path.suffix in {".json", ".md", ".py", ".yaml"}
        ).lower()
        legacy_markers = ("claude", "anthropic", "kimi", "gemini", "bitlesson", "polyarch")
        for legacy_marker in legacy_markers:
            self.assertNotIn(legacy_marker, executable_text)


class NativeRlcrTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="humanize-native-test-")
        self.temp_path = Path(self.temp.name)
        self.bin_dir = self.temp_path / "bin"
        self.bin_dir.mkdir()
        self.mock_codex = self.bin_dir / "codex"
        self.mock_codex.write_text(MOCK_CODEX, encoding="utf-8")
        self.mock_codex.chmod(0o755)
        self.environment = os.environ.copy()
        self.environment["PATH"] = f"{self.bin_dir}{os.pathsep}{self.environment.get('PATH', '')}"
        self.environment["JAKESHEA_HUMANIZE_RLCR_STATE_HOME"] = str(self.temp_path / "state")
        self.repo = self.temp_path / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.email", "rlcr-test@example.com")
        self.git("config", "user.name", "RLCR Test")
        (self.repo / "app.txt").write_text("initial\n", encoding="utf-8")
        (self.repo / "plan.md").write_text(
            textwrap.dedent(
                """\
                # Test plan

                ## Goal
                Change app.txt safely.

                ## Acceptance criteria
                - AC-1: app.txt contains the intended implementation.
                """
            ),
            encoding="utf-8",
        )
        self.git("add", "app.txt", "plan.md")
        self.git("commit", "-qm", "initial")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(self.repo), *args],
            check=True,
            text=True,
            capture_output=True,
        )

    def controller(self, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(CONTROLLER), *args],
            cwd=self.repo,
            env=self.environment,
            text=True,
            capture_output=True,
            check=False,
        )
        if check and result.returncode != 0:
            self.fail(f"controller failed: {result.stderr}\n{result.stdout}")
        return result

    def start(self, *, max_rounds: int = 3) -> None:
        result = self.controller(
            "start",
            "--plan",
            "plan.md",
            "--max-rounds",
            str(max_rounds),
            "--review-timeout",
            "30",
            "--max-minutes",
            "10",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("gpt-5.6-sol:xhigh", result.stdout)

    def implement_commit(self, text: str = "implemented\n") -> None:
        (self.repo / "app.txt").write_text(text, encoding="utf-8")
        self.git("add", "app.txt")
        self.git("commit", "-qm", text.strip())

    def set_modes(self, **modes: str) -> None:
        (self.bin_dir / "mock-mode.json").write_text(json.dumps(modes), encoding="utf-8")

    def calls(self) -> list[dict[str, object]]:
        path = self.bin_dir / "calls.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def hook(
        self, session_id: str = "session-a", cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        payload = {
            "session_id": session_id,
            "turn_id": "turn-1",
            "transcript_path": None,
            "cwd": str(cwd or self.repo),
            "hook_event_name": "Stop",
            "model": "gpt-5.6-sol",
            "permission_mode": "default",
            "stop_hook_active": False,
            "last_assistant_message": "implementation complete",
        }
        return subprocess.run(
            [sys.executable, str(CONTROLLER), "hook"],
            input=json.dumps(payload),
            cwd=cwd or self.repo,
            env=self.environment,
            text=True,
            capture_output=True,
            check=False,
        )

    def state(self) -> dict[str, object]:
        result = self.controller("status", "--json", check=True)
        return json.loads(result.stdout)

    def assert_reviewer_contract(self, records: list[dict[str, object]]) -> None:
        self.assertEqual({record["lane"] for record in records}, {"specification", "correctness"})
        for record in records:
            args = record["args"]
            self.assertIsInstance(args, list)
            assert isinstance(args, list)
            self.assertIn("gpt-5.6-sol", args)
            self.assertIn('model_reasoning_effort="xhigh"', args)
            self.assertIn("read-only", args)
            self.assertIn("--ephemeral", args)
            self.assertIn("--ignore-user-config", args)
            self.assertIn("--ignore-rules", args)
            self.assertIn("--strict-config", args)
            self.assertIn("hooks", args)
            self.assertIn("agents.enabled=false", args)
            self.assertIn('approval_policy="never"', args)
            self.assertIn("tools.web_search=false", args)
            for disabled in (
                "hooks",
                "multi_agent",
                "goals",
                "apps",
                "plugins",
                "skill_search",
                "browser_use",
                "image_generation",
            ):
                self.assertIn(disabled, args)
            self.assertIn("--json", args)
            self.assertTrue(
                any(
                    isinstance(arg, str)
                    and arg.startswith("shell_environment_policy.set=")
                    and "GIT_CONFIG_COUNT" in arg
                    and "GIT_NO_LAZY_FETCH" in arg
                    for arg in args
                )
            )
            self.assertEqual(record["guard"], "1")
            self.assertTrue(record["prompt_has_untrusted_boundary"])
            self.assertTrue(record["prompt_has_materialized_patch"])

    def test_reject_cache_correct_accept(self) -> None:
        self.start()
        self.implement_commit()
        self.set_modes(specification="changes", correctness="changes")

        first = self.hook()
        self.assertEqual(first.returncode, 0, first.stderr)
        first_payload = json.loads(first.stdout)
        self.assertEqual(first_payload["decision"], "block")
        self.assertIn("Blocking findings", first_payload["reason"])
        self.assert_reviewer_contract(self.calls())
        state = self.state()
        self.assertEqual(state["phase"], "correcting")
        self.assertEqual(state["rounds_completed"], 1)
        self.assertEqual(state["reviewer_calls"], 2)
        self.assertEqual(state["session_id"], "session-a")

        state_root = Path(self.environment["JAKESHEA_HUMANIZE_RLCR_STATE_HOME"])
        raw_results = list(
            state_root.glob("runs/*/*/rounds/round-001/attempt-*/specification.json")
        )
        normalized_results = list(
            state_root.glob("runs/*/*/rounds/round-001/attempt-*/specification.normalized.json")
        )
        self.assertEqual(len(raw_results), 1)
        self.assertEqual(len(normalized_results), 1)
        self.assertNotIn("finding_id", raw_results[0].read_text(encoding="utf-8"))
        self.assertNotIn("finding_id", normalized_results[0].read_text(encoding="utf-8"))
        packet_paths = list(state_root.glob("runs/*/*/rounds/round-001/packet.json"))
        self.assertEqual(len(packet_paths), 1)
        self.assertIn("finding_id", packet_paths[0].read_text(encoding="utf-8"))

        unchanged = self.hook()
        self.assertEqual(json.loads(unchanged.stdout)["decision"], "block")
        self.assertEqual(len(self.calls()), 2, "unchanged artifact must not be reviewed twice")

        self.implement_commit("fixed\n")
        self.set_modes(specification="accept", correctness="accept")
        accepted = self.hook()
        accepted_payload = json.loads(accepted.stdout)
        self.assertTrue(accepted_payload["continue"])
        self.assertIn("accepted", accepted_payload["systemMessage"].lower())
        self.assert_reviewer_contract(self.calls()[2:])
        state = self.state()
        self.assertEqual(state["phase"], "accepted")
        self.assertEqual(state["rounds_completed"], 2)
        self.assertEqual(state["reviewer_calls"], 4)

    def test_session_mismatch_is_a_noop(self) -> None:
        self.start()
        self.implement_commit()
        self.set_modes(default="changes")
        self.assertTrue(self.hook("owner-session").stdout)
        call_count = len(self.calls())
        mismatched = self.hook("other-session")
        self.assertEqual(mismatched.stdout, "")
        self.assertEqual(len(self.calls()), call_count)
        report = json.loads(self.controller("report", "--format", "json").stdout)
        bound = next(event for event in report["events"] if event["event"] == "session_bound")
        self.assertNotIn("session_id", bound)
        self.assertRegex(bound["session_id_digest"], r"^sha256:[0-9a-f]{64}$")

    def test_status_without_state_is_read_only(self) -> None:
        state_root = Path(self.environment["JAKESHEA_HUMANIZE_RLCR_STATE_HOME"])
        self.assertFalse(state_root.exists())
        result = self.controller("status")
        self.assertEqual(result.returncode, 20)
        self.assertIn("no RLCR run", result.stderr)
        self.assertFalse(state_root.exists(), "status created state in a read-only operation")

    def test_skill_local_controller_resolves_shared_runtime(self) -> None:
        for skill_controller in SKILL_CONTROLLERS:
            result = subprocess.run(
                [sys.executable, str(skill_controller), "--help"],
                cwd=self.repo,
                env=self.environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Humanize Codex-native RLCR controller", result.stdout)

    def test_manual_step_works_after_hook_session_binding(self) -> None:
        self.start()
        self.implement_commit()
        self.set_modes(default="changes")
        self.assertEqual(json.loads(self.hook("owner-session").stdout)["decision"], "block")
        self.implement_commit("manual fix\n")
        self.set_modes(default="accept")
        manual = self.controller("step")
        self.assertEqual(manual.returncode, 0, manual.stderr)
        self.assertIn("accepted", manual.stdout.lower())

    def test_repeated_infrastructure_failure_blocks_then_resumes(self) -> None:
        self.start(max_rounds=2)
        self.implement_commit()
        self.set_modes(default="fail")
        for _attempt in range(3):
            result = self.hook()
            payload = json.loads(result.stdout)
            self.assertEqual(payload["decision"], "block")
            self.assertIn("failure", payload["reason"].lower())
        blocked_state = self.state()
        self.assertEqual(blocked_state["phase"], "blocked")
        self.assertTrue(blocked_state["resumable"])
        terminal = self.hook()
        self.assertTrue(json.loads(terminal.stdout)["continue"])
        self.assertEqual(len(self.calls()), 6)
        resumed = self.controller("resume")
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        resumed_state = self.state()
        self.assertEqual(resumed_state["phase"], "active")
        self.assertFalse(resumed_state["resumable"])

    def test_review_is_discarded_when_repository_changes(self) -> None:
        self.start()
        self.implement_commit()
        self.set_modes(specification="change_tree", correctness="accept")
        result = self.hook()
        payload = json.loads(result.stdout)
        self.assertEqual(payload["decision"], "block")
        self.assertIn("discarded", payload["reason"].lower())
        self.assertEqual(self.state()["phase"], "active")

    def test_materialized_patch_mutation_terminally_blocks(self) -> None:
        self.start()
        self.implement_commit()
        self.set_modes(specification="tamper_patch", correctness="accept")
        result = self.hook()
        payload = json.loads(result.stdout)
        self.assertEqual(payload["decision"], "block")
        self.assertIn("patch changed", payload["reason"].lower())
        self.assertEqual(self.state()["phase"], "blocked")
        resumed = self.controller("resume")
        self.assertEqual(resumed.returncode, 20)
        self.assertIn("not resumable", resumed.stderr)

    def test_dirty_tree_blocks_without_spending_reviewer_calls(self) -> None:
        self.start()
        (self.repo / "app.txt").write_text("dirty\n", encoding="utf-8")
        result = self.hook()
        payload = json.loads(result.stdout)
        self.assertEqual(payload["decision"], "block")
        self.assertIn("clean committed", payload["reason"])
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.state()["reviewer_calls"], 0)

    def test_plan_mutation_blocks_without_review(self) -> None:
        self.start()
        (self.repo / "plan.md").write_text("changed plan\n", encoding="utf-8")
        self.git("add", "plan.md")
        self.git("commit", "-qm", "mutate plan")
        result = self.hook()
        payload = json.loads(result.stdout)
        self.assertEqual(payload["decision"], "block")
        self.assertIn("plan changed", payload["systemMessage"].lower())
        self.assertEqual(self.calls(), [])

    def test_concurrent_stop_does_not_duplicate_reviewers(self) -> None:
        self.start()
        self.implement_commit()
        self.set_modes(default="sleep_changes")
        payload = {
            "session_id": "session-a",
            "turn_id": "turn-1",
            "transcript_path": None,
            "cwd": str(self.repo),
            "hook_event_name": "Stop",
            "model": "gpt-5.6-sol",
            "permission_mode": "default",
            "stop_hook_active": False,
            "last_assistant_message": "implementation complete",
        }
        first = subprocess.Popen(
            [sys.executable, str(CONTROLLER), "hook"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.repo,
            env=self.environment,
            text=True,
        )
        assert first.stdin is not None
        first.stdin.write(json.dumps(payload))
        first.stdin.close()
        time.sleep(0.2)
        second = self.hook()
        second_payload = json.loads(second.stdout)
        self.assertEqual(second_payload["decision"], "block")
        self.assertIn("already running", second_payload["reason"])
        assert first.stdout is not None
        first_output = first.stdout.read()
        assert first.stderr is not None
        first_error = first.stderr.read()
        first.wait(timeout=10)
        first.stdout.close()
        first.stderr.close()
        self.assertEqual(json.loads(first_output)["decision"], "block", first_error)
        self.assertEqual(len(self.calls()), 2)

    def test_reviewer_child_guard_is_a_noop(self) -> None:
        environment = self.environment.copy()
        environment["JAKESHEA_HUMANIZE_RLCR_REVIEWER_CHILD"] = "1"
        result = subprocess.run(
            [sys.executable, str(CONTROLLER), "hook"],
            input="not-json",
            cwd=self.repo,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_missing_immutable_snapshot_returns_block_json(self) -> None:
        self.start()
        state_root = Path(self.environment["JAKESHEA_HUMANIZE_RLCR_STATE_HOME"])
        plan_snapshots = list(state_root.glob("runs/*/*/plan.md"))
        self.assertEqual(len(plan_snapshots), 1)
        plan_snapshots[0].unlink()
        result = self.hook()
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["decision"], "block")
        self.assertIn("snapshot", payload["reason"].lower())
        self.assertEqual(self.state()["phase"], "blocked")

    def test_modified_plan_snapshot_can_never_be_accepted(self) -> None:
        self.start()
        self.implement_commit()
        state_root = Path(self.environment["JAKESHEA_HUMANIZE_RLCR_STATE_HOME"])
        plan_snapshots = list(state_root.glob("runs/*/*/plan.md"))
        self.assertEqual(len(plan_snapshots), 1)
        plan_snapshots[0].write_text("forged easier plan\n", encoding="utf-8")
        self.set_modes(default="accept")
        result = self.hook()
        payload = json.loads(result.stdout)
        self.assertEqual(payload["decision"], "block")
        self.assertIn("snapshot changed", payload["reason"].lower())
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.state()["phase"], "blocked")

    def test_repeated_stale_artifacts_stop_at_infrastructure_limit(self) -> None:
        self.start()
        self.implement_commit()
        self.set_modes(specification="change_tree", correctness="accept")
        for attempt in range(3):
            result = self.hook()
            payload = json.loads(result.stdout)
            self.assertEqual(payload["decision"], "block")
            if attempt < 2:
                committed = self.git("show", "HEAD:app.txt").stdout
                (self.repo / "app.txt").write_text(committed, encoding="utf-8")
        state = self.state()
        self.assertEqual(state["phase"], "blocked")
        self.assertEqual(state["infrastructure_failures"], 3)
        self.assertEqual(state["reviewer_calls"], 6)

    def test_terminal_accept_survives_runtime_digest_change(self) -> None:
        self.start()
        self.implement_commit()
        self.set_modes(default="accept")
        self.assertTrue(json.loads(self.hook().stdout)["continue"])
        state_root = Path(self.environment["JAKESHEA_HUMANIZE_RLCR_STATE_HOME"])
        state_paths = list(state_root.glob("runs/*/*/state.json"))
        state = json.loads(state_paths[0].read_text(encoding="utf-8"))
        state["runtime_digest"] = "sha256:" + "0" * 64
        state_paths[0].write_text(json.dumps(state), encoding="utf-8")
        terminal = self.hook()
        self.assertTrue(json.loads(terminal.stdout)["continue"])
        self.assertEqual(self.state()["phase"], "accepted")

    def test_repository_git_filters_and_textconv_never_execute_in_controller(self) -> None:
        self.start()
        (self.repo / ".gitattributes").write_text("*.txt filter=evil diff=evil\n", encoding="utf-8")
        (self.repo / "app.txt").write_text("implementation with attributes\n", encoding="utf-8")
        self.git("add", ".gitattributes", "app.txt")
        self.git("commit", "-qm", "attribute fixture")

        marker = self.temp_path / "git-driver-executed"
        clean_code = (
            "import pathlib,sys;"
            f"pathlib.Path({str(marker)!r}).write_text('clean');"
            "sys.stdout.buffer.write(sys.stdin.buffer.read())"
        )
        textconv_code = (
            "import pathlib,sys;"
            f"pathlib.Path({str(marker)!r}).write_text('textconv');"
            "sys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes())"
        )
        self.git("config", "filter.evil.clean", f'{sys.executable} -c "{clean_code}"')
        self.git("config", "filter.evil.required", "true")
        self.git("config", "diff.evil.textconv", f'{sys.executable} -c "{textconv_code}"')
        fsmonitor = self.temp_path / "fsmonitor.py"
        fsmonitor.write_text(
            "#!/usr/bin/env python3\n"
            "from pathlib import Path\n"
            f"Path({str(marker)!r}).write_text('fsmonitor')\n",
            encoding="utf-8",
        )
        fsmonitor.chmod(0o755)
        self.git("config", "core.fsmonitor", str(fsmonitor))
        self.set_modes(default="accept")

        result = self.hook()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["continue"])
        self.assertFalse(
            marker.exists(), "trusted controller executed repository-configured Git code"
        )

    def test_repository_git_config_includes_fail_closed_for_active_run(self) -> None:
        self.start()
        marker = self.temp_path / "included-driver-executed"
        included_config = self.temp_path / "included.gitconfig"
        included_config.write_text(
            '[filter "evil"]\n'
            f"\tclean = {sys.executable} -c \"from pathlib import Path; Path({str(marker)!r}).write_text('ran')\"\n",
            encoding="utf-8",
        )
        self.git("config", "include.path", str(included_config))
        result = self.hook()
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["decision"], "block")
        self.assertIn("external config", payload["reason"])
        self.assertFalse(marker.exists())
        self.assertEqual(self.calls(), [])
        status = self.controller("status")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIn("Phase: blocked", status.stdout)
        canceled = self.controller("cancel", "--reason", "unsafe Git config")
        self.assertEqual(canceled.returncode, 0, canceled.stderr)
        self.assertIn("Phase: canceled", canceled.stdout)

    def test_per_worktree_git_config_fails_closed_for_active_run(self) -> None:
        self.start()
        marker = self.temp_path / "worktree-driver-executed"
        self.git("config", "extensions.worktreeConfig", "true")
        self.git(
            "config",
            "--worktree",
            "filter.evil.clean",
            f"{sys.executable} -c \"from pathlib import Path; Path({str(marker)!r}).write_text('ran')\"",
        )
        result = self.hook()
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["decision"], "block")
        self.assertIn("config.worktree", payload["reason"])
        self.assertFalse(marker.exists())
        self.assertEqual(self.calls(), [])

    def test_consensus_requires_exactly_one_result_per_lane(self) -> None:
        specification = LaneResult("specification", {}, None, 0.0, ())
        correctness = LaneResult("correctness", {}, None, 0.0, ())
        self.assertTrue(_has_required_reviewer_lanes([specification, correctness]))
        self.assertFalse(_has_required_reviewer_lanes([]))
        self.assertFalse(_has_required_reviewer_lanes([specification]))
        self.assertFalse(_has_required_reviewer_lanes([specification, specification]))
        self.assertFalse(_has_required_reviewer_lanes([correctness, specification]))

    def test_invalid_schema_never_accepts(self) -> None:
        self.start()
        self.implement_commit()
        self.set_modes(specification="malformed", correctness="accept")
        result = self.hook()
        payload = json.loads(result.stdout)
        self.assertEqual(payload["decision"], "block")
        self.assertIn("invalid structured reviewer output", payload["reason"])
        self.assertEqual(self.state()["phase"], "active")

    def test_successful_lane_is_cached_while_failed_lane_retries(self) -> None:
        self.start()
        self.implement_commit()
        self.set_modes(specification="accept", correctness="malformed")

        first = json.loads(self.hook().stdout)
        self.assertEqual(first["decision"], "block")
        self.assertEqual([call["lane"] for call in self.calls()].count("specification"), 1)
        self.assertEqual([call["lane"] for call in self.calls()].count("correctness"), 1)
        state = self.state()
        self.assertEqual(state["reviewer_calls"], 2)
        self.assertEqual(len(state["lane_cache"]), 1)

        self.set_modes(default="accept")
        second = json.loads(self.hook().stdout)
        self.assertTrue(second["continue"])
        calls = self.calls()
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[-1]["lane"], "correctness")
        state = self.state()
        self.assertEqual(state["phase"], "accepted")
        self.assertEqual(state["reviewer_calls"], 3)
        self.assertEqual(state["token_usage"]["input_tokens"], 200)

    def test_token_budget_is_enforced_from_codex_jsonl_usage(self) -> None:
        started = self.controller(
            "start",
            "--plan",
            "plan.md",
            "--max-rounds",
            "3",
            "--review-timeout",
            "30",
            "--max-minutes",
            "10",
            "--max-input-tokens",
            "150",
        )
        self.assertEqual(started.returncode, 0, started.stderr)
        self.implement_commit()
        self.set_modes(default="changes")
        result = json.loads(self.hook().stdout)
        self.assertEqual(result["decision"], "block")
        self.assertIn("token budget", result["reason"].lower())
        state = self.state()
        self.assertEqual(state["phase"], "exhausted")
        self.assertEqual(state["token_usage"]["input_tokens"], 200)
        self.assertEqual(state["token_usage"]["cached_input_tokens"], 100)
        self.assertEqual(state["token_usage"]["output_tokens"], 40)
        self.assertEqual(state["token_usage"]["reasoning_output_tokens"], 10)

    def test_required_evidence_must_pass_at_the_candidate_commit(self) -> None:
        started = self.controller(
            "start",
            "--plan",
            "plan.md",
            "--review-timeout",
            "30",
            "--max-minutes",
            "10",
            "--require-check",
            "tests",
        )
        self.assertEqual(started.returncode, 0, started.stderr)
        self.implement_commit()
        missing = json.loads(self.hook().stdout)
        self.assertIn("no evidence", missing["reason"])
        self.assertEqual(self.calls(), [])

        evidence = self.controller(
            "evidence",
            "run",
            "--name",
            "tests",
            "--",
            sys.executable,
            "-c",
            "print('passed')",
        )
        self.assertEqual(evidence.returncode, 0, evidence.stderr)
        self.set_modes(default="accept")
        accepted = json.loads(self.hook().stdout)
        self.assertTrue(accepted["continue"])
        self.assertEqual(self.state()["phase"], "accepted")

    def test_stale_or_tampered_evidence_cannot_authorize_review(self) -> None:
        started = self.controller(
            "start",
            "--plan",
            "plan.md",
            "--review-timeout",
            "30",
            "--max-minutes",
            "10",
            "--require-check",
            "tests",
        )
        self.assertEqual(started.returncode, 0, started.stderr)
        self.implement_commit()
        evidence = self.controller(
            "evidence",
            "run",
            "--name",
            "tests",
            "--",
            sys.executable,
            "-c",
            "raise SystemExit(0)",
        )
        self.assertEqual(evidence.returncode, 0, evidence.stderr)
        state_root = Path(self.environment["JAKESHEA_HUMANIZE_RLCR_STATE_HOME"])
        evidence_paths = list(state_root.glob("runs/*/*/evidence/tests-*.json"))
        self.assertEqual(len(evidence_paths), 1)
        evidence_paths[0].write_text("{}\n", encoding="utf-8")
        tampered = json.loads(self.hook().stdout)
        self.assertEqual(tampered["decision"], "block")
        self.assertIn("evidence", tampered["reason"].lower())
        self.assertEqual(self.state()["phase"], "blocked")
        self.assertEqual(self.calls(), [])

    def test_evidence_file_symlink_cannot_reuse_an_attestation(self) -> None:
        if os.name == "nt":
            self.skipTest("symbolic-link setup is platform-specific")
        started = self.controller(
            "start",
            "--plan",
            "plan.md",
            "--review-timeout",
            "30",
            "--max-minutes",
            "10",
            "--require-check",
            "tests",
        )
        self.assertEqual(started.returncode, 0, started.stderr)
        self.implement_commit()
        evidence = self.controller(
            "evidence",
            "run",
            "--name",
            "tests",
            "--",
            sys.executable,
            "-c",
            "raise SystemExit(0)",
        )
        self.assertEqual(evidence.returncode, 0, evidence.stderr)
        state_root = Path(self.environment["JAKESHEA_HUMANIZE_RLCR_STATE_HOME"])
        evidence_paths = list(state_root.glob("runs/*/*/evidence/tests-*.json"))
        self.assertEqual(len(evidence_paths), 1)
        copy = evidence_paths[0].with_name("copied-attestation.json")
        copy.write_bytes(evidence_paths[0].read_bytes())
        evidence_paths[0].unlink()
        evidence_paths[0].symlink_to(copy.name)
        result = json.loads(self.hook().stdout)
        self.assertEqual(result["decision"], "block")
        self.assertIn("symbolic link", result["reason"].lower())
        self.assertEqual(self.calls(), [])

    def test_failed_evidence_is_recorded_but_never_satisfies_a_check(self) -> None:
        started = self.controller(
            "start",
            "--plan",
            "plan.md",
            "--review-timeout",
            "30",
            "--max-minutes",
            "10",
            "--require-check",
            "tests",
        )
        self.assertEqual(started.returncode, 0, started.stderr)
        self.implement_commit()
        evidence = self.controller(
            "evidence",
            "run",
            "--name",
            "tests",
            "--",
            sys.executable,
            "-c",
            "raise SystemExit(7)",
        )
        self.assertEqual(evidence.returncode, 10, evidence.stderr)
        self.assertFalse(json.loads(evidence.stdout)["passed"])
        blocked = json.loads(self.hook().stdout)
        self.assertEqual(blocked["decision"], "block")
        self.assertIn("did not pass", blocked["reason"])
        self.assertEqual(self.state()["phase"], "active")
        self.assertEqual(self.calls(), [])

    def test_structured_contract_gap_overrides_accepting_reviewers(self) -> None:
        contract = {
            "schema_version": "rlcr.plan.v1",
            "goal": "Implement all required behavior.",
            "criteria": [
                {
                    "id": "AC-2",
                    "description": "A second explicit criterion must be satisfied.",
                    "required": True,
                    "required_checks": [],
                }
            ],
        }
        (self.repo / "plan-contract.json").write_text(json.dumps(contract), encoding="utf-8")
        self.git("add", "plan-contract.json")
        self.git("commit", "-qm", "add plan contract")
        started = self.controller(
            "start",
            "--plan",
            "plan.md",
            "--contract",
            "plan-contract.json",
            "--review-timeout",
            "30",
            "--max-minutes",
            "10",
        )
        self.assertEqual(started.returncode, 0, started.stderr)
        self.implement_commit()
        self.set_modes(default="accept")
        outcome = json.loads(self.hook().stdout)
        self.assertEqual(outcome["decision"], "block")
        self.assertIn("AC-2", outcome["reason"])
        self.assertEqual(self.state()["phase"], "correcting")

    def test_contract_validation_is_normalized_and_read_only(self) -> None:
        contract = {
            "schema_version": "rlcr.plan.v1",
            "goal": "  Verify the contract.  ",
            "criteria": [
                {
                    "id": "AC-1",
                    "description": "  Produce a valid normalized record.  ",
                    "required": True,
                    "required_checks": ["unit-tests", "unit-tests"],
                }
            ],
        }
        (self.repo / "plan-contract.json").write_text(json.dumps(contract), encoding="utf-8")
        state_root = Path(self.environment["JAKESHEA_HUMANIZE_RLCR_STATE_HOME"])
        validated = self.controller("contract", "validate", "--contract", "plan-contract.json")
        self.assertEqual(validated.returncode, 0, validated.stderr)
        result = json.loads(validated.stdout)
        self.assertTrue(result["valid"])
        self.assertEqual(result["contract"]["goal"], "Verify the contract.")
        self.assertEqual(result["contract"]["criteria"][0]["required_checks"], ["unit-tests"])
        self.assertFalse(state_root.exists())

        contract["unexpected"] = True
        (self.repo / "plan-contract.json").write_text(json.dumps(contract), encoding="utf-8")
        invalid = self.controller("contract", "validate", "--contract", "plan-contract.json")
        self.assertEqual(invalid.returncode, 20)
        self.assertIn("unsupported", invalid.stderr)
        self.assertNotIn("Traceback", invalid.stderr)

    def test_rewritten_history_is_not_reviewed(self) -> None:
        self.start()
        tree = self.git("rev-parse", "HEAD^{tree}").stdout.strip()
        unrelated = self.git("commit-tree", tree, "-m", "unrelated root").stdout.strip()
        self.git("update-ref", "HEAD", unrelated)
        result = json.loads(self.hook().stdout)
        self.assertEqual(result["decision"], "block")
        self.assertIn("ancestor", result["reason"].lower())
        self.assertEqual(self.calls(), [])

    def test_explicit_adoption_rebinds_the_next_session(self) -> None:
        self.start()
        self.implement_commit()
        self.set_modes(default="changes")
        self.assertEqual(json.loads(self.hook("owner-session").stdout)["decision"], "block")
        self.assertEqual(self.hook("new-session").stdout, "")
        adopted = self.controller("adopt")
        self.assertEqual(adopted.returncode, 0, adopted.stderr)
        self.assertIsNone(self.state()["session_id"])

        self.implement_commit("adopted fix\n")
        self.set_modes(default="accept")
        accepted = json.loads(self.hook("new-session").stdout)
        self.assertTrue(accepted["continue"])
        state = self.state()
        self.assertEqual(state["session_id"], "new-session")
        self.assertEqual(state["adoptions"], 1)

    def test_event_journal_recovers_a_lagging_state_snapshot(self) -> None:
        self.start()
        result = self.controller(
            "evidence",
            "run",
            "--name",
            "smoke",
            "--",
            sys.executable,
            "-c",
            "print('ok')",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        state_root = Path(self.environment["JAKESHEA_HUMANIZE_RLCR_STATE_HOME"])
        run_dirs = list(state_root.glob("runs/*/*"))
        self.assertEqual(len(run_dirs), 1)
        run_dir = run_dirs[0]
        events = sorted((run_dir / "events").glob("*.json"))
        self.assertEqual(len(events), 2)
        first_event = json.loads(events[0].read_text(encoding="utf-8"))
        (run_dir / "state.json").write_text(
            json.dumps(first_event["_state_after"]), encoding="utf-8"
        )
        recovered = self.state()
        self.assertEqual(recovered["sequence"], 2)
        self.assertIn("smoke", recovered["evidence"])
        persisted = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(persisted["sequence"], 2)

    def test_event_journal_tampering_is_detected(self) -> None:
        self.start()
        state_root = Path(self.environment["JAKESHEA_HUMANIZE_RLCR_STATE_HOME"])
        event_paths = list(state_root.glob("runs/*/*/events/00000001.json"))
        self.assertEqual(len(event_paths), 1)
        event = json.loads(event_paths[0].read_text(encoding="utf-8"))
        event["event"] = "forged"
        event_paths[0].write_text(json.dumps(event), encoding="utf-8")
        status = self.controller("status")
        self.assertEqual(status.returncode, 20)
        self.assertIn("digest", status.stderr.lower())

    def test_event_journal_repairs_same_sequence_snapshot_tampering(self) -> None:
        self.start()
        state_root = Path(self.environment["JAKESHEA_HUMANIZE_RLCR_STATE_HOME"])
        state_paths = list(state_root.glob("runs/*/*/state.json"))
        self.assertEqual(len(state_paths), 1)
        forged = json.loads(state_paths[0].read_text(encoding="utf-8"))
        forged["phase"] = "accepted"
        forged["terminal_reason"] = "forged acceptance"
        state_paths[0].write_text(json.dumps(forged), encoding="utf-8")
        recovered = self.state()
        self.assertEqual(recovered["phase"], "active")
        self.assertIsNone(recovered["terminal_reason"])
        persisted = json.loads(state_paths[0].read_text(encoding="utf-8"))
        self.assertEqual(persisted["phase"], "active")

    def test_v1_state_migration_is_one_way_and_non_mutating(self) -> None:
        legacy = {
            "schema_version": "rlcr.run.v1",
            "max_rounds": 2,
            "max_reviewer_calls": 10,
            "max_infrastructure_failures": 3,
            "review_timeout_seconds": 45,
            "created_at": "2026-08-26T00:00:00Z",
            "deadline_at": "2026-08-26T01:30:00Z",
            "reviewer_model": "gpt-5.6-sol",
            "reviewer_effort": "xhigh",
            "reviewer_lanes": ["specification", "correctness"],
        }
        original = json.loads(json.dumps(legacy))
        migrated = migrate_state(legacy)
        self.assertEqual(legacy, original)
        self.assertEqual(migrated["schema_version"], "rlcr.run.v2")
        self.assertEqual(migrated["migrated_from"], "rlcr.run.v1")
        self.assertEqual(migrated["run_config"]["schema_version"], "rlcr.run-config.v1")
        self.assertEqual(migrated["run_config"]["max_minutes"], 90)
        self.assertEqual(migrated["max_input_tokens"], 8_000_000)
        self.assertEqual(migrated["token_usage"]["input_tokens"], 0)
        self.assertEqual(migrated["snapshot_files"][0], "plan.md")

    def test_mutable_policy_fields_must_match_immutable_configuration(self) -> None:
        self.start()
        state = self.state()
        state["max_input_tokens"] += 1
        with self.assertRaisesRegex(ControllerError, "immutable configuration"):
            _validate_state(state, self.repo.resolve(), runtime_digest(PLUGIN_ROOT))

    def test_history_reports_and_trace_export_are_read_only(self) -> None:
        self.start()
        state_before = self.state()
        history = self.controller("history", "--json")
        self.assertEqual(history.returncode, 0, history.stderr)
        rows = json.loads(history.stdout)
        self.assertEqual(rows[0]["run_id"], state_before["run_id"])

        report = self.controller("report", "--format", "json")
        self.assertEqual(report.returncode, 0, report.stderr)
        report_value = json.loads(report.stdout)
        self.assertEqual(report_value["schema_version"], "rlcr.report.v1")
        self.assertEqual(report_value["summary"]["run_id"], state_before["run_id"])
        trace_path = self.temp_path / "trace.json"
        exported = self.controller("export", "--output", str(trace_path))
        self.assertEqual(exported.returncode, 0, exported.stderr)
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        self.assertEqual(trace["displayTimeUnit"], "ms")
        self.assertTrue(trace["traceEvents"])
        self.assertEqual(self.state()["sequence"], state_before["sequence"])

    def test_cancel_terminates_registered_reviewer_processes(self) -> None:
        if os.name == "nt":
            self.skipTest("process-group timing assertion is POSIX-specific")
        self.start()
        self.implement_commit()
        self.set_modes(default="sleep_long")
        payload = {
            "session_id": "session-a",
            "turn_id": "turn-1",
            "transcript_path": None,
            "cwd": str(self.repo),
            "hook_event_name": "Stop",
            "model": "gpt-5.6-sol",
            "permission_mode": "default",
            "stop_hook_active": False,
            "last_assistant_message": "implementation complete",
        }
        hook_process = subprocess.Popen(
            [sys.executable, str(CONTROLLER), "hook"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.repo,
            env=self.environment,
            text=True,
        )
        assert hook_process.stdin is not None
        hook_process.stdin.write(json.dumps(payload))
        hook_process.stdin.close()
        state_root = Path(self.environment["JAKESHEA_HUMANIZE_RLCR_STATE_HOME"])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if len(list(state_root.glob("runs/*/*/rounds/**/*.process.json"))) == 2:
                break
            time.sleep(0.05)
        self.assertEqual(len(list(state_root.glob("runs/*/*/rounds/**/*.process.json"))), 2)
        canceled = self.controller("cancel", "--reason", "test cancellation")
        self.assertEqual(canceled.returncode, 0, canceled.stderr)
        hook_process.wait(timeout=8)
        assert hook_process.stdout is not None
        assert hook_process.stderr is not None
        hook_process.stdout.close()
        hook_process.stderr.close()
        self.assertEqual(self.state()["phase"], "canceled")
        self.assertFalse(list(state_root.glob("runs/*/*/rounds/**/*.process.json")))
        tombstones = list(state_root.glob("runs/*/*/canceled-attempts/*.json"))
        self.assertEqual(len(tombstones), 1)
        self.assertEqual(
            json.loads(tombstones[0].read_text(encoding="utf-8"))["schema_version"],
            "rlcr.cancellation.v1",
        )

    def test_round_budget_exhaustion_has_one_terminal_continuation(self) -> None:
        self.start(max_rounds=1)
        self.implement_commit()
        self.set_modes(default="changes")
        self.assertEqual(json.loads(self.hook().stdout)["decision"], "block")
        self.implement_commit("still not fixed\n")
        exhausted = json.loads(self.hook().stdout)
        self.assertEqual(exhausted["decision"], "block")
        self.assertIn("EXHAUSTED", exhausted["reason"])
        self.assertEqual(self.state()["phase"], "exhausted")
        terminal = json.loads(self.hook().stdout)
        self.assertTrue(terminal["continue"])
        self.assertEqual(len(self.calls()), 2)

    def test_hook_outside_git_is_noop(self) -> None:
        outside = self.temp_path / "not-a-repo"
        outside.mkdir()
        result = self.hook(cwd=outside)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_ambient_git_directory_cannot_redirect_controller(self) -> None:
        self.environment["GIT_DIR"] = str(self.temp_path / "attacker-controlled-git-dir")
        self.start()
        state = self.state()
        self.assertEqual(state["project_root"], str(self.repo))

    def test_private_state_permissions(self) -> None:
        if os.name == "nt":
            self.skipTest("POSIX permission bits are not available on Windows")
        self.start()
        state_root = Path(self.environment["JAKESHEA_HUMANIZE_RLCR_STATE_HOME"])
        self.assertEqual(stat.S_IMODE(state_root.stat().st_mode), 0o700)
        state_files = list(state_root.glob("runs/*/*/state.json"))
        self.assertEqual(len(state_files), 1)
        self.assertEqual(stat.S_IMODE(state_files[0].stat().st_mode), 0o600)

    def test_active_pointer_symlink_cannot_silence_the_hook(self) -> None:
        if os.name == "nt":
            self.skipTest("symbolic-link setup is platform-specific")
        self.start()
        state_root = Path(self.environment["JAKESHEA_HUMANIZE_RLCR_STATE_HOME"])
        pointers = list((state_root / "projects").glob("*.json"))
        self.assertEqual(len(pointers), 1)
        target = self.temp_path / "pointer-target.json"
        target.write_bytes(pointers[0].read_bytes())
        pointers[0].unlink()
        pointers[0].symlink_to(target)
        result = self.hook()
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["decision"], "block")
        self.assertIn("symbolic link", payload["reason"].lower())

    def test_evidence_directory_symlink_is_rejected_before_execution(self) -> None:
        if os.name == "nt":
            self.skipTest("symbolic-link setup is platform-specific")
        self.start()
        self.implement_commit()
        state_root = Path(self.environment["JAKESHEA_HUMANIZE_RLCR_STATE_HOME"])
        evidence_dirs = list(state_root.glob("runs/*/*/evidence"))
        self.assertEqual(len(evidence_dirs), 1)
        outside = self.temp_path / "outside-evidence"
        outside.mkdir()
        evidence_dirs[0].rmdir()
        evidence_dirs[0].symlink_to(outside, target_is_directory=True)
        marker = self.temp_path / "evidence-command-ran"
        result = self.controller(
            "evidence",
            "run",
            "--name",
            "tests",
            "--",
            sys.executable,
            "-c",
            f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')",
        )
        self.assertEqual(result.returncode, 20)
        self.assertIn("evidence directory", result.stderr.lower())
        self.assertFalse(marker.exists())
        self.assertFalse(list(outside.iterdir()))


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    failed = len(result.failures) + len(result.errors)
    print(f"Passed: {result.testsRun - failed}")
    print(f"Failed: {failed}")
    raise SystemExit(0 if result.wasSuccessful() else 1)
