"""Installer boundaries use temporary homes and fake systemd, never devices."""

from __future__ import annotations

import ast
import builtins
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import install_runtime as runtime
import install_service as service
from dicta_config import upstream_library


def native_sparse_save(path: Path, edits: dict) -> dict:
    """Run only the pinned native save method against a synthetic configuration."""
    source = upstream_library() / "src/config_manager.py"
    expected = "172de29dad31f32119c7128ef3ea7192fe2783a0e3bb6a3d6d823828ef0ea9e4"
    if service.digest(source) != expected:
        raise AssertionError("native config fixture must use the verified hyprwhspr 1.49.0 source")
    tree = ast.parse(source.read_text())
    factory = next(node for node in tree.body
                   if isinstance(node, ast.FunctionDef) and node.name == "build_default_config")
    values = next(node.value for node in factory.body if isinstance(node, ast.Return))
    defaults = {}
    for key, value in zip(values.keys, values.values, strict=True):
        try:
            defaults[ast.literal_eval(key)] = ast.literal_eval(value)
        except ValueError:
            # CPU count and hallucination constants are unrelated to managed keys.
            continue
    manager = next(node for node in tree.body
                   if isinstance(node, ast.ClassDef) and node.name == "ConfigManager")
    save = next(node for node in manager.body
                if isinstance(node, ast.FunctionDef) and node.name == "save_config")
    schema = next(ast.literal_eval(node.value) for node in manager.body
                  if isinstance(node, ast.Assign)
                  and any(isinstance(target, ast.Name) and target.id == "SCHEMA_URL"
                          for target in node.targets))

    def record_ownership(kind, target, category):
        if (kind, target, category) != ("file", path, "personal"):
            raise AssertionError("native save escaped its synthetic file boundary")

    def fixture_import(name, globals=None, locals=None, fromlist=(), level=0):
        # The native receipt hook would otherwise touch a user-managed install.
        if name != "managed_install":
            raise AssertionError("unexpected import in the isolated native save method")
        return SimpleNamespace(record_ownership=record_ownership)

    namespace = {"__builtins__": {**vars(builtins), "__import__": fixture_import},
                 "json": json, "os": os, "Path": Path, "tempfile": tempfile,
                 "LEGACY_BINDINGS_KEY": "use_hypr_bindings"}
    exec(compile(ast.Module(body=[save], type_ignores=[]), str(source), "exec"), namespace)
    fixture = SimpleNamespace(config_file=path, config_dir=path.parent,
                              config={**defaults, **service.read_json(path), **edits},
                              default_config=defaults, SCHEMA_URL=schema, verbose=False)
    if not namespace["save_config"](fixture):
        raise AssertionError("isolated native save failed")
    return defaults


class FakeSystemd:
    def __init__(self, root: Path, config_home: Path) -> None:
        self.fragment = root / "native.service"
        self.fragment.write_text("[Service]\nExecStart=/native/launcher\n")
        self.dropin = service.service_paths(config_home)[1]
        self.active = True
        self.fail_starts = 0
        self.actions = []

    def inspect(self) -> dict:
        return {"fragment": str(self.fragment), "active": self.active,
                "dropins": [str(path) for path in sorted(self.dropin.parent.glob("*.conf"))]}

    def stop(self) -> None:
        self.actions.append("stop")
        self.active = False

    def reload(self) -> None:
        self.actions.append("reload")

    def start(self) -> None:
        self.actions.append("start")
        if self.fail_starts:
            self.fail_starts -= 1
            raise service.InstallError("synthetic-start-failure")
        self.active = True


class ServiceTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="dicta install ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.prefix = self.root / "owned prefix"
        self.prefix.mkdir()
        self.config_home = self.root / "user config"
        self.config, self.dropin = service.service_paths(self.config_home)
        self.original = {"audio_device_name": "synthetic input", "external_bindings": True,
                         "recording_mode": "auto", "other": {"preserve": 42}}
        self.original_bytes = json.dumps(self.original, indent=4).encode() + b"\n"
        service.atomic_write(self.config, self.original_bytes, 0o640)
        service.write_json(self.prefix / "install-state.json", {"release": "releases/test"})
        self.systemd = FakeSystemd(self.root, self.config_home)

    def activate(self) -> None:
        service.activate(self.prefix, self.config_home, self.systemd, adopt_existing=True)

    def test_activation_and_deactivation_restore_exact_bytes_mode_and_running_state(self) -> None:
        self.activate()
        configured = service.read_json(self.config)
        self.assertEqual(configured["other"], self.original["other"])
        self.assertEqual(configured["audio_device_name"], "synthetic input")
        self.assertIsNone(configured["language"])
        self.assertEqual(configured["rest_body"]["model"], "whisper-large-v3-turbo")
        self.assertFalse(configured["auto_submit"])
        self.assertTrue(self.dropin.exists())
        self.assertEqual(self.systemd.actions, ["stop", "reload", "start"])

        service.deactivate(self.prefix, self.systemd)

        self.assertEqual(self.config.read_bytes(), self.original_bytes)
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o640)
        self.assertFalse(self.dropin.exists())
        self.assertFalse((self.prefix / "activation.json").exists())
        self.assertTrue(self.systemd.active)
        self.assertTrue(list((self.prefix / "backups").glob("*/config.json")))

    def test_existing_overrides_require_adoption_before_any_service_action(self) -> None:
        with self.assertRaisesRegex(service.InstallError, "existing-overrides"):
            service.activate(self.prefix, self.config_home, self.systemd)
        self.assertEqual(self.systemd.actions, [])
        self.assertEqual(self.config.read_bytes(), self.original_bytes)

    def test_foreign_dropin_preserved_and_later_override_rejected(self) -> None:
        other = self.dropin.parent / "20-other.conf"
        service.atomic_write(other, b"[Service]\nEnvironment=SAFE=example\n")
        expected = other.read_bytes()
        self.activate()
        service.deactivate(self.prefix, self.systemd)
        self.assertEqual(other.read_bytes(), expected)
        service.atomic_write(self.dropin.parent / "zzz-later.conf", b"[Service]\n")
        with self.assertRaisesRegex(service.InstallError, "dropin-order-conflict"):
            self.activate()

    def test_failed_candidate_start_restores_previous_runtime(self) -> None:
        self.systemd.fail_starts = 1
        with self.assertRaisesRegex(service.InstallError, "synthetic-start-failure"):
            self.activate()
        self.assertEqual(self.config.read_bytes(), self.original_bytes)
        self.assertFalse(self.dropin.exists())
        self.assertTrue(self.systemd.active)
        self.assertFalse((self.prefix / "activation.json").exists())

    def test_failure_between_config_and_dropin_restores_config(self) -> None:
        original_write = service.atomic_write

        def fail_dropin(path, data, mode=0o600):
            if path == self.dropin:
                raise OSError("synthetic disk failure")
            original_write(path, data, mode)

        with patch.object(service, "atomic_write", side_effect=fail_dropin):
            with self.assertRaises(OSError):
                self.activate()
        self.assertEqual(self.config.read_bytes(), self.original_bytes)
        self.assertFalse(self.dropin.exists())
        self.assertTrue(self.systemd.active)

    def test_inactive_baseline_is_restored_to_inactive(self) -> None:
        self.systemd.active = False
        self.activate()
        self.assertTrue(self.systemd.active)
        service.deactivate(self.prefix, self.systemd)
        self.assertFalse(self.systemd.active)

    def test_unrelated_config_edits_survive_deactivation(self) -> None:
        self.activate()
        current = service.read_json(self.config)
        current["other"] = {"new": 7}
        current["new_user_setting"] = "keep"
        service.write_json(self.config, current)
        service.deactivate(self.prefix, self.systemd)
        expected = {**self.original, "other": {"new": 7}, "new_user_setting": "keep"}
        self.assertEqual(service.read_json(self.config), expected)

    def test_native_sparse_save_of_unrelated_setting_allows_doctor_and_deactivation(self) -> None:
        self.activate()
        native_sparse_save(self.config, {"clipboard_settle_delay": 0.4})
        sparse = service.read_json(self.config)
        for name in ("auto_submit", "rest_headers", "language", "realtime_live_typing"):
            self.assertNotIn(name, sparse)

        self.assertTrue(all(item["ok"] for item in service.activation_checks(self.prefix, self.systemd)))
        service.deactivate(self.prefix, self.systemd)

        restored = service.read_json(self.config)
        self.assertEqual(restored["clipboard_settle_delay"], 0.4)
        for name, value in self.original.items():
            self.assertEqual(restored[name], value)
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o640)
        self.assertFalse(self.dropin.exists())
        self.assertFalse((self.prefix / "activation.json").exists())
        state = service.read_json(self.prefix / "install-state.json")
        service.write_json(self.prefix / "install-state.json", {**state, "owned": {}, "directories": []})
        self.assertEqual(runtime.uninstall(self.prefix), [])
        self.assertEqual(service.read_json(self.config)["clipboard_settle_delay"], 0.4)

    def test_managed_default_snapshot_matches_the_pinned_native_source(self) -> None:
        defaults = native_sparse_save(self.config, {})
        self.assertEqual(service.NATIVE_MANAGED_DEFAULTS,
                         {name: defaults[name] for name in service.NATIVE_MANAGED_DEFAULTS})
        self.assertFalse(service.managed_settings_match({}, {"unrecognized_setting": None}))

    def test_missing_nondefault_setting_after_native_save_remains_a_conflict(self) -> None:
        self.activate()
        native_sparse_save(self.config, {"clipboard_settle_delay": 0.4})
        sparse = service.read_json(self.config)
        sparse.pop("post_transcription_hook")
        service.write_json(self.config, sparse)
        before = (self.config.read_bytes(), self.dropin.read_bytes(), self.systemd.actions[:])

        checks = service.activation_checks(self.prefix, self.systemd)
        self.assertFalse(next(item["ok"] for item in checks if item["check"] == "managed-native-settings"))
        with self.assertRaisesRegex(service.InstallError, "config-conflict"):
            service.deactivate(self.prefix, self.systemd)

        self.assertEqual(before, (self.config.read_bytes(), self.dropin.read_bytes(), self.systemd.actions))

    def test_real_managed_change_after_native_save_is_preserved_and_rejected(self) -> None:
        self.activate()
        native_sparse_save(self.config, {"auto_submit": True, "clipboard_settle_delay": 0.4})
        before = (self.config.read_bytes(), self.dropin.read_bytes(), self.systemd.actions[:])

        checks = service.activation_checks(self.prefix, self.systemd)
        self.assertFalse(next(item["ok"] for item in checks if item["check"] == "managed-native-settings"))
        with self.assertRaisesRegex(service.InstallError, "config-conflict"):
            service.deactivate(self.prefix, self.systemd)

        self.assertEqual(before, (self.config.read_bytes(), self.dropin.read_bytes(), self.systemd.actions))

    def test_managed_config_conflict_keeps_service_and_every_file_unchanged(self) -> None:
        self.activate()
        current = service.read_json(self.config)
        current["language"] = "en"
        service.write_json(self.config, current)
        before = (self.config.read_bytes(), self.dropin.read_bytes(), self.systemd.actions[:])
        with self.assertRaisesRegex(service.InstallError, "config-conflict"):
            service.deactivate(self.prefix, self.systemd)
        self.assertEqual(before, (self.config.read_bytes(), self.dropin.read_bytes(), self.systemd.actions))

    def test_unit_drift_and_owned_dropin_drift_are_not_overwritten(self) -> None:
        self.activate()
        self.systemd.fragment.write_text("[Service]\nExecStart=/new/choice\n")
        with self.assertRaisesRegex(service.InstallError, "unit-drift"):
            service.deactivate(self.prefix, self.systemd)
        self.dropin.write_text("user edit")
        with self.assertRaisesRegex(service.InstallError, "dropin-drift"):
            service.deactivate(self.prefix, self.systemd)

    def test_doctor_distinguishes_activation_journal_from_effective_owned_config(self) -> None:
        self.activate()
        self.assertTrue(all(item["ok"] for item in service.activation_checks(self.prefix, self.systemd)))
        self.dropin.unlink()
        checks = service.activation_checks(self.prefix, self.systemd)
        self.assertFalse(next(item["ok"] for item in checks if item["check"] == "owned-service-override"))
        current = service.read_json(self.config)
        current["post_transcription_hook"] = "exec /different/hook"
        service.write_json(self.config, current)
        checks = service.activation_checks(self.prefix, self.systemd)
        self.assertFalse(next(item["ok"] for item in checks if item["check"] == "managed-native-settings"))

    def test_interrupted_deactivation_resumes_without_losing_original(self) -> None:
        self.activate()
        self.systemd.fail_starts = 1
        with self.assertRaisesRegex(service.InstallError, "synthetic-start-failure"):
            service.deactivate(self.prefix, self.systemd)
        self.assertFalse(self.dropin.exists())
        self.assertEqual(self.config.read_bytes(), self.original_bytes)
        service.deactivate(self.prefix, self.systemd)
        self.assertTrue(self.systemd.active)
        self.assertFalse((self.prefix / "activation.json").exists())

    def test_fresh_native_config_is_removed_again_and_no_binding_installed(self) -> None:
        self.config.unlink()
        self.activate()
        configured = service.read_json(self.config)
        self.assertTrue(configured["external_bindings"])
        self.assertNotIn("primary_shortcut", configured)
        service.deactivate(self.prefix, self.systemd)
        self.assertFalse(self.config.exists())

    def test_inline_credential_is_refused_without_printing_the_value(self) -> None:
        current = {**self.original, "rest_api_key": "synthetic-secret-never-echo"}
        service.write_json(self.config, current)
        with self.assertRaises(service.InstallError) as caught:
            self.activate()
        self.assertNotIn(current["rest_api_key"], str(caught.exception))
        self.assertEqual(self.systemd.actions, [])

    def test_hook_and_unit_paths_quote_spaces_and_specifiers(self) -> None:
        hook = self.root / "some 'quoted' folder" / "review-hook"
        configured = service.desired_config({}, hook)
        import shlex
        self.assertEqual(shlex.split(configured["post_transcription_hook"]), ["exec", str(hook)])
        self.assertIn(b"%%", service.dropin_contents(Path("/some %h/run"), Path("/secret file")))

    @unittest.skipUnless(shutil.which("systemd-analyze"), "native systemd parser unavailable")
    def test_generated_unit_with_spaces_has_no_ignored_directives(self) -> None:
        runner = self.root / "command with spaces"
        service.atomic_write(runner, b"#!/bin/sh\nexit 0\n", 0o700)
        unit = self.root / "arca-dicta-parser-test.service"
        unit.write_bytes(service.dropin_contents(runner, self.root / "private settings.env"))
        result = subprocess.run(["systemd-analyze", "--user", "verify", "--man=no", str(unit)],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("ignoring", result.stderr.lower())
        self.assertNotIn("invalid", result.stderr.lower())


class RuntimePreparationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="dicta artifact ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "artifact"
        self.source.mkdir()
        names = ["VERSION", "install", "install_runtime.py", "install_service.py", "launch", "main.py",
                 "review-hook", "worker.py", "hud_bridge.py", "requirements/daemon.lock",
                 "requirements/worker.lock", "packaging/model-small.json"]
        for name in names:
            target = self.source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(runtime.ROOT / name, target)
        self.manifest = {"version": (runtime.ROOT / "VERSION").read_text().strip(),
                         "files": {name: service.digest(self.source / name) for name in names}}
        service.write_json(self.source / "release-manifest.json", self.manifest)
        self.prefix = self.root / "installation with spaces"
        self.prefix.mkdir()
        self.model_dir = self.root / "external model"
        self.model_dir.mkdir()
        self.model = {"files": {}}
        for name in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt"):
            (self.model_dir / name).write_bytes(b"synthetic model marker")
            self.model["files"][name] = service.digest(self.model_dir / name)
        self.commands = []
        self.addCleanup(patch.stopall)
        patch.object(runtime, "native_preflight").start()
        patch.object(runtime, "model_manifest", return_value=self.model).start()
        patch.object(runtime, "run_checked", side_effect=self.fake_command).start()

    def fake_command(self, arguments, **kwargs) -> str:
        self.commands.append(arguments)
        if arguments[-1] == "--version":
            return "uv 0.11.14"
        if "venv" in arguments:
            environment = Path(arguments[-1])
            (environment / "bin").mkdir(parents=True)
            (environment / "bin/python").symlink_to("/usr/bin/python3")
        return ""

    def prepare(self) -> dict:
        return runtime.prepare(self.source, self.prefix, self.root / "config",
                               uv="uv", download=False, external_model=self.model_dir)

    def test_verified_artifact_prepares_idempotently_without_systemd(self) -> None:
        with patch.object(service.Systemd, "_run", side_effect=AssertionError("service called")):
            state = self.prepare()
            commands_before = len(self.commands)
            self.assertEqual(self.prepare(), state)
        self.assertEqual(len(self.commands), commands_before)
        self.assertTrue(runtime.inventory_matches(self.prefix, state["owned"]))
        self.assertTrue(os.access(self.prefix / "run", os.X_OK))
        self.assertIn("installation with spaces", (self.prefix / "run").read_text())
        self.assertFalse((self.root / "config").exists())

    def test_tampered_source_is_rejected_before_dependency_preparation(self) -> None:
        (self.source / "worker.py").write_text("tampered")
        with self.assertRaisesRegex(service.InstallError, "artifact-integrity"):
            self.prepare()
        self.assertEqual(self.commands, [])

    def test_manifest_traversal_absolute_and_symlink_sources_are_rejected(self) -> None:
        for name in ("../outside", "/absolute", "a/../outside", "a//b"):
            with self.subTest(name=name), self.assertRaises(service.InstallError):
                runtime.relative_path(name)
        original = self.source / "worker.py"
        original.unlink()
        original.symlink_to(runtime.ROOT / "worker.py")
        with self.assertRaisesRegex(service.InstallError, "symlink-conflict"):
            self.prepare()

    def test_preexisting_prefix_content_is_preserved(self) -> None:
        foreign = self.prefix / "user-file"
        foreign.write_text("preserve")
        with self.assertRaisesRegex(service.InstallError, "prefix-not-empty"):
            self.prepare()
        self.assertEqual(foreign.read_text(), "preserve")

    def test_dependency_failure_cleans_only_newly_created_preparation(self) -> None:
        def fail_sync(arguments, **kwargs):
            if "sync" in arguments:
                raise service.InstallError("synthetic dependency failure")
            return self.fake_command(arguments, **kwargs)
        with patch.object(runtime, "run_checked", side_effect=fail_sync):
            with self.assertRaises(service.InstallError):
                self.prepare()
        self.assertEqual(list(self.prefix.iterdir()), [])
        self.assertTrue(self.model_dir.is_dir())

    def test_model_hash_failure_cleans_runtime_and_preserves_external_model(self) -> None:
        (self.model_dir / "model.bin").write_bytes(b"invalid")
        with self.assertRaisesRegex(service.InstallError, "model-integrity"):
            self.prepare()
        self.assertFalse((self.prefix / "releases").exists())
        self.assertEqual((self.model_dir / "model.bin").read_bytes(), b"invalid")

    def test_uninstall_removes_only_matching_owned_files(self) -> None:
        state = self.prepare()
        changed = self.prefix / state["release"] / "app/worker.py"
        changed.write_text("user edit")
        foreign = self.prefix / state["release"] / "user notes.txt"
        foreign.write_text("keep me")
        empty_foreign = self.prefix / "user empty directory"
        empty_foreign.mkdir()
        backup = self.prefix / "backups" / "test" / "config.json"
        service.atomic_write(backup, b"private backup")
        preserved = runtime.uninstall(self.prefix)
        self.assertEqual(preserved, [str(changed.relative_to(self.prefix))])
        self.assertEqual(changed.read_text(), "user edit")
        self.assertEqual(foreign.read_text(), "keep me")
        self.assertTrue(empty_foreign.is_dir())
        self.assertEqual(backup.read_bytes(), b"private backup")
        self.assertFalse((self.prefix / "run").exists())
        self.assertTrue((self.model_dir / "model.bin").exists())
        self.assertEqual(runtime.uninstall(self.prefix), [])

    def test_uninstall_does_not_follow_replaced_parent_symlink(self) -> None:
        state = self.prepare()
        installed = self.prefix / state["release"] / "app"
        outside = self.root / "user backup"
        installed.rename(outside)
        installed.symlink_to(outside, target_is_directory=True)
        before = (outside / "worker.py").read_bytes()
        runtime.uninstall(self.prefix)
        self.assertEqual((outside / "worker.py").read_bytes(), before)
        self.assertTrue(installed.is_symlink())

    def test_uninstall_refuses_active_installation(self) -> None:
        self.prepare()
        service.write_json(self.prefix / "activation.json", {"phase": "active"})
        with self.assertRaisesRegex(service.InstallError, "active-installation"):
            runtime.uninstall(self.prefix)
        self.assertTrue((self.prefix / "run").exists())

    def test_download_failure_removes_partial_bytes(self) -> None:
        target = self.root / "download"
        model = {"repository": "Systran/faster-whisper-small", "revision": runtime.MODEL_REVISION,
                 "files": {"model.bin": hashlib.sha256(b"expected").hexdigest()}}
        import io
        with patch.object(runtime.urllib.request, "urlopen", return_value=io.BytesIO(b"bad")):
            with self.assertRaisesRegex(service.InstallError, "model-integrity"):
                runtime.download_model(target, model)
        self.assertEqual(list(target.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
