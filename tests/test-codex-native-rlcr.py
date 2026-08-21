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
SKILL_CONTROLLER = PLUGIN_ROOT / "skills" / "humanize-rlcr" / "scripts" / "rlcr.py"
sys.dont_write_bytecode = True
sys.path.insert(0, str(PLUGIN_ROOT))

from controller import PLUGIN_VERSION  # pyright: ignore[reportMissingImports]  # noqa: E402
from controller.cli import _has_required_reviewer_lanes  # pyright: ignore[reportMissingImports]  # noqa: E402
from controller.review import LaneResult  # pyright: ignore[reportMissingImports]  # noqa: E402


MOCK_CODEX = r'''#!/usr/bin/env python3
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
    print("--ephemeral --ignore-user-config --ignore-rules --output-schema --sandbox --disable --strict-config")
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
print(encoded, end="")
'''


class PluginPackagingTests(unittest.TestCase):
    def test_personal_identity_and_codex_only_boundary(self) -> None:
        manifest = json.loads(
            (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        marketplace = json.loads(
            (REPO_ROOT / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8")
        )
        hook_config = json.loads(
            (PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8")
        )
        skill = (PLUGIN_ROOT / "skills" / "humanize-rlcr" / "SKILL.md").read_text(
            encoding="utf-8"
        )
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
            "controller/review.py",
            "controller/storage.py",
            "hooks/hooks.json",
            "prompts/correctness.md",
            "prompts/specification.md",
            "schemas/review-v1.json",
            "scripts/rlcr.py",
            "skills/humanize-rlcr/SKILL.md",
            "skills/humanize-rlcr/agents/openai.yaml",
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
        present_forbidden = sorted(
            name for name in forbidden_roots if (REPO_ROOT / name).exists()
        )
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
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def controller(self, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(CONTROLLER), *args],
            cwd=self.repo,
            env=self.environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
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

    def hook(self, session_id: str = "session-a", cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
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
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
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
            self.assertIn('web_search="disabled"', args)
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
        raw_results = list(state_root.glob("runs/*/*/rounds/round-001/attempt-*/specification.json"))
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

    def test_status_without_state_is_read_only(self) -> None:
        state_root = Path(self.environment["JAKESHEA_HUMANIZE_RLCR_STATE_HOME"])
        self.assertFalse(state_root.exists())
        result = self.controller("status")
        self.assertEqual(result.returncode, 20)
        self.assertIn("no RLCR run", result.stderr)
        self.assertFalse(state_root.exists(), "status created state in a read-only operation")

    def test_skill_local_controller_resolves_shared_runtime(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SKILL_CONTROLLER), "--help"],
            cwd=self.repo,
            env=self.environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
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
        for attempt in range(3):
            result = self.hook()
            payload = json.loads(result.stdout)
            self.assertEqual(payload["decision"], "block")
            self.assertIn("failure", payload["reason"].lower())
        self.assertEqual(self.state()["phase"], "blocked")
        terminal = self.hook()
        self.assertTrue(json.loads(terminal.stdout)["continue"])
        self.assertEqual(len(self.calls()), 6)
        resumed = self.controller("resume")
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual(self.state()["phase"], "active")

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
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
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
        self.assertFalse(marker.exists(), "trusted controller executed repository-configured Git code")

    def test_repository_git_config_includes_fail_closed_for_active_run(self) -> None:
        self.start()
        marker = self.temp_path / "included-driver-executed"
        included_config = self.temp_path / "included.gitconfig"
        included_config.write_text(
            "[filter \"evil\"]\n"
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
            f'{sys.executable} -c "from pathlib import Path; Path({str(marker)!r}).write_text(\'ran\')"',
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


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    failed = len(result.failures) + len(result.errors)
    print(f"Passed: {result.testsRun - failed}")
    print(f"Failed: {failed}")
    raise SystemExit(0 if result.wasSuccessful() else 1)
