import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import wag
import conductor
import kimi
import triage
import wb


class Branding(unittest.TestCase):
    def test_renamed_python_modules_import_in_a_fresh_process(self):
        for name in ("wag.py", "wagmsg.py"):
            self.assertTrue((ROOT / "lib" / name).is_file(), name)
        result = subprocess.run([sys.executable, "-c", "import wag, wagmsg; print(wagmsg.__name__)"],
                                cwd=ROOT / "lib", capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual("wagmsg", result.stdout.strip())

    def test_mailbox_help_uses_wagmsg(self):
        result = subprocess.run([sys.executable, str(ROOT / "lib/wagmsg.py"), "--help"],
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("usage: wagmsg ", result.stdout)

    def test_entry_points_use_wagflow(self):
        for name in ("wagflow.cmd", "lib/wagflow.ps1", "lib/wagflow-entry.ps1", "lib/WagFlow.Common.ps1",
                     "claude/commands/wagflow-implementer.md", "codex/skills/wagflow-implementer/SKILL.md"):
            with self.subTest(name=name):
                self.assertTrue((ROOT / name).is_file(), name)
        wrapper = (ROOT / "wagflow.cmd").read_text(encoding="utf-8-sig")
        self.assertIn(r"lib\wagflow.ps1", wrapper)
        launcher = (ROOT / "lib/wagflow.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("[CmdletBinding(", launcher)
        self.assertIn("'WagFlow.Common.ps1'", launcher)
        self.assertNotIn("function Get-WagFlowConfig", launcher)

    def test_readme_uses_display_name_and_fork(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8-sig")
        self.assertTrue(readme.startswith("# WagFlow\n"))
        self.assertIn("https://github.com/msize/wagflow", readme)
        self.assertIn("https://github.com/msize/wagterm", readme)

    def test_agent_commands_are_installed_under_their_own_names(self):
        installer = (ROOT / "install.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("'start-github-issue.md' = 'wagflow-start.md'", installer)
        self.assertIn("'triage-issue.md' = 'wagflow-triage.md'", installer)
        planner = (ROOT / "lib/pane-claude.ps1").read_text(encoding="utf-8-sig")
        self.assertIn('"/wagflow-start $Issue"', planner)
        helpers = (ROOT / "lib/WagFlow.Common.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("run /wagflow-start for $Issue", helpers)

    def test_terminal_pipe_has_its_own_identity(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual("wagterm", wag.pipe_name())
        with patch.dict(os.environ, WAGTERM_PIPE="wagterm-test"):
            self.assertEqual("wagterm-test", wag.pipe_name())

    def test_terminal_cli_override_uses_wagterm(self):
        with patch.dict(os.environ, WAGTERMCTL="custom-wagtermctl.exe"):
            self.assertEqual("custom-wagtermctl.exe", wag.ctl_path())

    def test_terminal_pane_variables_use_wagterm(self):
        with patch.dict(os.environ, WAGTERM_PANE_ID="right", WAGTERM_SESSION_ID="left"):
            self.assertEqual("right", wag.my_pane())
        with patch.dict(os.environ, {"WAGTERM_SESSION_ID": "left"}, clear=True):
            self.assertEqual("left", wag.my_pane())

    def test_config_has_its_own_identity(self):
        home = Path.home()
        with patch.dict(os.environ, {"USERPROFILE": str(home)}, clear=True):
            self.assertEqual((home / ".wagflow.json").resolve(), conductor.config_path())
        with patch.dict(os.environ, WAGFLOW_CONFIG=str(ROOT / "custom.json")):
            self.assertEqual((ROOT / "custom.json").resolve(), conductor.config_path())

    def test_markers_and_kimi_guardrails_keep_the_same_brand(self):
        self.assertEqual("<!-- wagflow:planner -->", triage.PLANNER_MARKER)
        self.assertEqual(triage.PLANNER_MARKER, wb.PLANNER_MARKER)
        self.assertTrue(kimi.MARKER.startswith("<!-- wagflow:"))
        self.assertEqual("wagflow-push-refused://", kimi.REFUSED_SCHEME)
        self.assertEqual("wagflow-refused", kimi.REFUSED_TOKEN)
        self.assertTrue((ROOT / "kimi/AGENTS.md").read_text(encoding="utf-8").startswith(kimi.MARKER))
