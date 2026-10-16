"""Portability checks use temporary files and fake launch targets, never a daemon."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from dicta_config import (
    ConfigurationError, MODEL_FILES, load_runtime_config,
    upstream_binary, upstream_library,
)
import main as launcher

ROOT = Path(__file__).resolve().parent
SAFE_ENV = {"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"}


class ConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="dicta paths ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.upstream = self.root / "native lib"
        self.upstream.mkdir()
        self.worker = self.root / "worker python"
        self.worker.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
        self.worker.chmod(0o700)
        self.model = self.root / "external model"
        self.model.mkdir()
        # These empty test markers are not usable weights and are never loaded.
        for name in MODEL_FILES:
            (self.model / name).touch()
        self.environment = {
            "ARCA_DICTA_UPSTREAM_LIB": str(self.upstream),
            "ARCA_DICTA_WORKER_PYTHON": str(self.worker),
            "ARCA_DICTA_MODEL_DIR": str(self.model),
        }

    def test_explicit_paths_with_spaces_are_preserved(self) -> None:
        config = load_runtime_config(self.environment)
        self.assertEqual((config.upstream_lib, config.worker_python, config.model_dir),
                         (self.upstream, self.worker, self.model))

    def test_arch_defaults_do_not_consult_personal_directories(self) -> None:
        self.assertEqual(upstream_library({}), Path("/usr/lib/hyprwhspr/lib"))
        self.assertEqual(upstream_binary({}), Path("/usr/bin/hyprwhspr"))

    def test_required_paths_have_no_implicit_fallback(self) -> None:
        for name in ("ARCA_DICTA_WORKER_PYTHON", "ARCA_DICTA_MODEL_DIR"):
            with self.subTest(name=name):
                environment = {key: value for key, value in self.environment.items()
                               if key != name}
                with self.assertRaisesRegex(ConfigurationError, name):
                    load_runtime_config(environment)

    def test_relative_tilde_and_empty_paths_are_rejected_without_echoing_value(self) -> None:
        for name in self.environment:
            for value in ("private-relative-path", "~/private-path", ""):
                with self.subTest(name=name, value=value):
                    with self.assertRaises(ConfigurationError) as caught:
                        load_runtime_config({**self.environment, name: value})
                    self.assertIn(name, str(caught.exception))
                    if value:
                        self.assertNotIn(value, str(caught.exception))

    def test_missing_upstream_is_rejected(self) -> None:
        self.upstream.rmdir()
        with self.assertRaisesRegex(ConfigurationError, "ARCA_DICTA_UPSTREAM_LIB"):
            load_runtime_config(self.environment)

    def test_non_executable_worker_is_rejected(self) -> None:
        self.worker.chmod(0o600)
        with self.assertRaisesRegex(ConfigurationError, "ARCA_DICTA_WORKER_PYTHON"):
            load_runtime_config(self.environment)

    def test_incomplete_model_is_rejected_without_loading_it(self) -> None:
        (self.model / "tokenizer.json").unlink()
        with self.assertRaisesRegex(ConfigurationError, "ARCA_DICTA_MODEL_DIR"):
            load_runtime_config(self.environment)

    def test_missing_config_stops_before_importing_desktop_application(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch.object(sys, "argv", ["main.py"]):
            with patch.object(launcher, "upstream_supported", return_value=True):
                with patch.object(launcher.importlib.util, "spec_from_file_location") as load:
                    with self.assertRaisesRegex(SystemExit, "ARCA_DICTA_WORKER_PYTHON"):
                        launcher.main()
                    load.assert_not_called()

    def test_direct_cli_delegation_does_not_load_daemon_config(self) -> None:
        binary = str(self.root / "native binary")
        with patch.dict(os.environ, {"ARCA_DICTA_UPSTREAM_BIN": binary}, clear=True):
            with patch.object(sys, "argv", ["main.py", "--help", "two words"]):
                with patch.object(launcher.os, "execv", side_effect=SystemExit(23)) as execute:
                    with patch.object(launcher, "load_runtime_config") as load:
                        with self.assertRaises(SystemExit) as caught:
                            launcher.main()
                        self.assertEqual(caught.exception.code, 23)
                        execute.assert_called_once_with(binary, [binary, "--help", "two words"])
                        load.assert_not_called()

    def test_upstream_override_reaches_launcher_bridge_and_native_hud_loader(self) -> None:
        code = """
import json
from unittest.mock import patch
import main
import hud_bridge
from hud_runtime import mic_osd
from hud_runtime.mic_osd import main as hud_main
with patch.object(main.Path, 'read_bytes', autospec=True, side_effect=OSError) as read:
    assert not main.upstream_supported()
    guard_root = read.call_args.args[0].parent
with patch.object(hud_main.importlib.util, 'spec_from_file_location', side_effect=RuntimeError) as load:
    try:
        hud_main.load_native()
    except RuntimeError:
        pass
    paths = [guard_root, hud_bridge.UPSTREAM, mic_osd.UPSTREAM, load.call_args.args[1]]
print(json.dumps([str(path) for path in paths]))
"""
        result = subprocess.run([sys.executable, "-B", "-c", code], cwd=ROOT,
                                env={**SAFE_ENV, "ARCA_DICTA_UPSTREAM_LIB": str(self.upstream)},
                                capture_output=True, text=True, timeout=5, check=True)
        self.assertEqual(json.loads(result.stdout),
                         [str(self.upstream)] * 3 + [str(self.upstream / "mic_osd/main.py")])


class LaunchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="dicta launch ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.checkout = self.root / "checkout with spaces"
        self.checkout.mkdir()
        self.launch = self.checkout / "launch"
        shutil.copy2(ROOT / "launch", self.launch)
        self.target = self.root / "fake interpreter"
        self.target.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\"\nexit 23\n", encoding="utf-8")
        self.target.chmod(0o700)

    def run_launch(self, args=(), environment=None):
        return subprocess.run([str(self.launch), *args], cwd=self.root,
                              env={**SAFE_ENV, **(environment or {})},
                              capture_output=True, text=True, timeout=5)

    def test_different_cwd_and_checkout_with_spaces_use_adjacent_main(self) -> None:
        result = self.run_launch(environment={"ARCA_DICTA_PYTHON": str(self.target)})
        self.assertEqual(result.returncode, 23)
        self.assertEqual(result.stdout.splitlines(), ["-B", str(self.checkout / "main.py")])

    def test_cli_preserves_arguments_exit_code_and_needs_no_daemon_configuration(self) -> None:
        result = self.run_launch(["--help", "two words", "--literal=$VALUE"],
                                 {"ARCA_DICTA_UPSTREAM_BIN": str(self.target)})
        self.assertEqual(result.returncode, 23)
        self.assertEqual(result.stdout.splitlines(), ["--help", "two words", "--literal=$VALUE"])

    def test_direct_python_cli_ignores_daemon_library_configuration_on_fresh_start(self) -> None:
        for library in (None, "", "relative-library"):
            with self.subTest(library=library):
                environment = {**SAFE_ENV, "ARCA_DICTA_UPSTREAM_BIN": str(self.target)}
                if library is not None:
                    environment["ARCA_DICTA_UPSTREAM_LIB"] = library
                result = subprocess.run(
                    [sys.executable, "-B", str(ROOT / "main.py"), "--help", "two words"],
                    cwd=self.root, env=environment, capture_output=True, text=True, timeout=5,
                )
                self.assertEqual(result.returncode, 23)
                self.assertEqual(result.stdout.splitlines(), ["--help", "two words"])
                self.assertEqual(result.stderr, "")

    def test_missing_daemon_python_exits_without_running_upstream(self) -> None:
        result = self.run_launch(environment={"ARCA_DICTA_UPSTREAM_BIN": str(self.target)})
        self.assertEqual(result.returncode, 64)
        self.assertEqual(result.stdout, "")
        self.assertIn("ARCA_DICTA_PYTHON", result.stderr)

    def test_invalid_daemon_python_exits_without_running_a_target(self) -> None:
        for value in ("relative-python", str(self.root / "missing")):
            with self.subTest(value=value):
                result = self.run_launch(environment={"ARCA_DICTA_PYTHON": value})
                self.assertEqual(result.returncode, 64)
                self.assertEqual(result.stdout, "")
                self.assertNotIn(value, result.stderr)

    def test_relative_cli_binary_is_rejected(self) -> None:
        result = self.run_launch(["--help"], {"ARCA_DICTA_UPSTREAM_BIN": "relative-binary"})
        self.assertEqual(result.returncode, 64)
        self.assertEqual(result.stdout, "")

    def test_launcher_removes_pythonhome_and_disables_bytecode(self) -> None:
        self.target.write_text(
            '#!/bin/sh\nprintf "%s\\n" "${PYTHONHOME-unset}" "$PYTHONDONTWRITEBYTECODE"\n',
            encoding="utf-8",
        )
        result = self.run_launch(environment={"ARCA_DICTA_PYTHON": str(self.target),
                                              "PYTHONHOME": "/synthetic/unusable"})
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.splitlines(), ["unset", "1"])


if __name__ == "__main__":
    unittest.main()
