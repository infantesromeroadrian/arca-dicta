"""Prepare an owned native runtime; activation is a separate explicit command."""

from __future__ import annotations

import argparse
import ast
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import stat
import subprocess
import sys
import urllib.error
import urllib.request

# -I prevents caller Python settings from importing code ahead of this module.
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from install_service import (  # noqa: E402
    InstallError, Systemd, activate, activation_checks, atomic_write, deactivate, digest,
    foreign_overrides, read_json, service_paths, write_json,
)

NATIVE_LIB = Path("/usr/lib/hyprwhspr/lib")
NATIVE_BIN = Path("/usr/bin/hyprwhspr")
PYTHON = Path("/usr/bin/python3")
UV_MIN_VERSION = (0, 11, 14)
MODEL_REVISION = "536b0662742c02347bc0e980a01041f333bce120"
DAEMON_IMPORTS = (
    "import sounddevice, numpy, soxr, soundfile, evdev, pyperclip, pyudev, "
    "pulsectl, rich, jsonschema, requests, cairo, gi; "
    "gi.require_version('Gtk', '4.0'); gi.require_version('Gtk4LayerShell', '1.0'); "
    "gi.require_version('PangoCairo', '1.0'); "
    "from gi.repository import Gtk, Gtk4LayerShell, PangoCairo"
)
WORKER_IMPORTS = "import faster_whisper, ctranslate2, numpy, av, onnxruntime, tokenizers"


def absolute_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or any(char in value for char in "\x00\n\r"):
        raise InstallError("invalid-path: use an absolute path without control characters")
    return path


def relative_path(value: str) -> Path:
    if not isinstance(value, str):
        raise InstallError("invalid-manifest-path: expected a string")
    path = Path(value)
    if (not value or path.is_absolute()
            or ".." in path.parts or path.as_posix() != value):
        raise InstallError("invalid-manifest-path: only normalized relative paths are allowed")
    return path


def safe_file(root: Path, name: str) -> Path:
    relative = relative_path(name)
    path = root / relative
    if any(parent.is_symlink() for parent in [path, *path.parents] if parent != root.parent):
        raise InstallError("symlink-conflict: artifact and owned paths must not contain links")
    return path


def clean_environment() -> dict[str, str]:
    names = ("HOME", "PATH", "LANG", "LC_ALL", "XDG_CACHE_HOME", "UV_CACHE_DIR",
             "SSL_CERT_FILE", "SSL_CERT_DIR")
    return {**{name: os.environ[name] for name in names if name in os.environ},
            "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
            "UV_NO_PROGRESS": "1", "UV_NO_CONFIG": "1"}


def run_checked(arguments: list[str], *, timeout: int = 300) -> str:
    try:
        result = subprocess.run(arguments, capture_output=True, text=True,
                                timeout=timeout, env=clean_environment(), check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise InstallError("runtime-command-failed: check network, disk space and required native packages") from None
    if result.returncode:
        raise InstallError("runtime-command-failed: dependency preparation/import failed; inspect the pinned requirements")
    return result.stdout.strip()


def verify_artifact(source: Path) -> dict:
    manifest = read_json(source / "release-manifest.json")
    version = manifest.get("version")
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.]+)?", version):
        raise InstallError("invalid-release-version: use a built release artifact")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise InstallError("invalid-release-manifest: rebuild the release artifact")
    required = {"VERSION", "install", "install_runtime.py", "install_service.py", "launch",
                "main.py", "review-hook", "worker.py", "requirements/daemon.lock",
                "requirements/worker.lock", "packaging/model-small.json"}
    if not required <= files.keys():
        raise InstallError("incomplete-release: required installer/runtime files are absent")
    for name, expected in files.items():
        path = safe_file(source, name)
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise InstallError("invalid-release-hash: rebuild the release artifact")
        if not path.is_file() or digest(path) != expected:
            raise InstallError("artifact-integrity: a release file is missing or changed; download it again")
    if (source / "VERSION").read_text().strip() != version:
        raise InstallError("artifact-version-mismatch: rebuild the release artifact")
    return manifest


def contract_checks(app: Path, upstream: Path = NATIVE_LIB) -> list[dict]:
    checks = []
    for module, group in (("main.py", "core"), ("hud_bridge.py", "hud")):
        tree = ast.parse((app / module).read_text())
        hashes = next(ast.literal_eval(node.value) for node in tree.body
                      if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == "SUPPORTED_FILES"
                              for target in node.targets))
        for name, expected in hashes.items():
            try:
                passed = digest(upstream / name) == expected
            except OSError:
                passed = False
            checks.append({"check": f"{group}:{name}", "ok": passed,
                           "remedy": "install the unchanged hyprwhspr 1.49.0-1 package" if not passed else ""})
    return checks


def native_preflight(app: Path) -> None:
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise InstallError("unsupported-platform: this artifact supports Arch/CachyOS x86_64")
    if sys.version_info[:2] != (3, 14):
        raise InstallError("unsupported-python: the locked alpha runtime requires system Python 3.14")
    if not NATIVE_BIN.is_file() or not all(item["ok"] for item in contract_checks(app)):
        raise InstallError("native-contract: install the unchanged hyprwhspr 1.49.0-1 package first")
    run_checked([str(PYTHON), "-I", "-B", "-c",
                 "import gi, cairo, evdev; gi.require_version('Gtk','4.0'); "
                 "gi.require_version('Gtk4LayerShell','1.0'); from gi.repository import Gtk, Gtk4LayerShell"])


def model_manifest(app: Path) -> dict:
    model = read_json(app / "packaging/model-small.json")
    if model.get("repository") != "Systran/faster-whisper-small" or model.get("revision") != MODEL_REVISION:
        raise InstallError("model-contract: unexpected model repository/revision")
    if set(model.get("files", {})) != {"model.bin", "config.json", "tokenizer.json", "vocabulary.txt"}:
        raise InstallError("model-contract: expected the four audited upstream files")
    return model


def verify_model(directory: Path, model: dict) -> bool:
    return all((directory / name).is_file() and not (directory / name).is_symlink()
               and digest(directory / name) == expected for name, expected in model["files"].items())


def download_model(directory: Path, model: dict) -> None:
    directory.mkdir(parents=True, mode=0o700)
    for name, expected in model["files"].items():
        target = directory / name
        partial = directory / (name + ".part")
        url = f"https://huggingface.co/{model['repository']}/resolve/{model['revision']}/{name}"
        try:
            with urllib.request.urlopen(url, timeout=60) as response, partial.open("xb") as output:
                size = 0
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > 600_000_000:
                        raise InstallError("model-download-limit: unexpected model size")
                    output.write(chunk)
            if digest(partial) != expected:
                raise InstallError("model-integrity: downloaded bytes do not match the pinned revision")
            partial.replace(target)
        except (OSError, urllib.error.URLError):
            raise InstallError("model-download-failed: check connectivity and retry installation") from None
        finally:
            partial.unlink(missing_ok=True)


def file_inventory(root: Path, paths: list[Path]) -> dict:
    inventory = {}
    for start in paths:
        for path in ([start] if not start.is_dir() else sorted(start.rglob("*"))):
            relative = str(path.relative_to(root))
            if path.is_symlink():
                inventory[relative] = {"link": os.readlink(path)}
            elif path.is_file():
                inventory[relative] = {"sha256": digest(path),
                                       "mode": stat.S_IMODE(path.stat().st_mode)}
    return inventory


def linked_parent(path: Path, prefix: Path) -> bool:
    parent = path.parent
    while parent != prefix:
        if parent.is_symlink():
            return True
        parent = parent.parent
    return False


def inventory_matches(prefix: Path, inventory: dict) -> bool:
    for name, expected in inventory.items():
        path = prefix / relative_path(name)
        if linked_parent(path, prefix):
            return False
        if "link" in expected:
            if not path.is_symlink() or os.readlink(path) != expected["link"]:
                return False
        elif (not path.is_file() or path.is_symlink() or digest(path) != expected["sha256"]
              or stat.S_IMODE(path.stat().st_mode) != expected["mode"]):
            return False
    return True


@contextmanager
def installation_lock(prefix: Path):
    prefix.mkdir(parents=True, exist_ok=True, mode=0o700)
    if prefix.is_symlink() or prefix.stat().st_uid != os.getuid():
        raise InstallError("prefix-ownership: choose an owned, non-linked installation directory")
    descriptor = os.open(prefix / ".install.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise InstallError("installation-busy: another installer is operating on this prefix") from None
        yield
    finally:
        os.close(descriptor)


def prepare(source: Path, prefix: Path, config_home: Path, *, uv: str,
            download: bool, external_model: Path | None) -> dict:
    manifest = verify_artifact(source)
    native_preflight(source)
    state_file = prefix / "install-state.json"
    if state_file.exists():
        state = read_json(state_file)
        if state["artifact"] != manifest or not inventory_matches(prefix, state["owned"]):
            raise InstallError("installation-conflict: installed files differ; use a fresh prefix or reconcile before uninstall")
        return state
    if any(path.name != ".install.lock" for path in prefix.iterdir()):
        raise InstallError("prefix-not-empty: choose an empty directory; existing files are preserved")
    if download == (external_model is not None):
        raise InstallError("model-source-required: choose --download-model or --model-dir, exclusively")
    version_match = re.match(r"uv (\d+)\.(\d+)\.(\d+)\b", run_checked([uv, "--version"]))
    if not version_match or tuple(map(int, version_match.groups())) < UV_MIN_VERSION:
        raise InstallError("uv-version: install uv >=0.11.14 to prepare the locked alpha runtime")
    release = prefix / "releases" / manifest["version"]
    app = release / "app"
    model = model_manifest(source)
    model_dir = external_model or prefix / "models" / MODEL_REVISION
    made = [prefix / "releases", prefix / "run"]
    if download:
        made.append(prefix / "models")
    try:
        app.mkdir(parents=True, mode=0o700)
        for name in manifest["files"]:
            target = app / relative_path(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / name, target)
        shutil.copy2(source / "release-manifest.json", app / "release-manifest.json")
        for name, system_site in (("daemon", True), ("worker", False)):
            environment = release / name
            command = [uv, "venv", "--python", str(PYTHON), "--no-project", "--no-managed-python"]
            if system_site:
                command.append("--system-site-packages")
            run_checked([*command, str(environment)])
            run_checked([uv, "pip", "sync", "--python", str(environment / "bin/python"),
                         "--require-hashes", "--no-build", "--no-config",
                         str(app / "requirements" / (name + ".lock"))])
        if download:
            download_model(model_dir, model)
        if not verify_model(model_dir, model):
            raise InstallError("model-integrity: supply all four files from the pinned model revision")
        runtime = {
            "ARCA_DICTA_PYTHON": str(release / "daemon/bin/python"),
            "ARCA_DICTA_WORKER_PYTHON": str(release / "worker/bin/python"),
            "ARCA_DICTA_MODEL_DIR": str(model_dir),
            "ARCA_DICTA_UPSTREAM_LIB": str(NATIVE_LIB),
            "ARCA_DICTA_UPSTREAM_BIN": str(NATIVE_BIN),
        }
        write_json(release / "runtime.json", runtime)
        wrapper = "#!/bin/sh\nset -eu\nunset PYTHONHOME PYTHONPATH PYTHONUSERBASE\nexport PYTHONNOUSERSITE=1\n"
        wrapper += "".join(f"export {name}={shlex.quote(value)}\n" for name, value in runtime.items())
        wrapper += f"exec {shlex.quote(str(app / 'launch'))} \"$@\"\n"
        atomic_write(prefix / "run", wrapper.encode(), 0o700)
        run_checked([runtime["ARCA_DICTA_PYTHON"], "-I", "-B", "-c", DAEMON_IMPORTS])
        run_checked([runtime["ARCA_DICTA_WORKER_PYTHON"], "-I", "-B", "-c", WORKER_IMPORTS])
        state = {"version": manifest["version"], "artifact": manifest,
                 "release": str(release.relative_to(prefix)), "config_home": str(config_home),
                 "model": str(model_dir), "owned": file_inventory(prefix, made),
                 "directories": sorted({str(path.relative_to(prefix))
                                        for start in made if start.is_dir()
                                        for path in [start, *start.rglob("*")]
                                        if path.is_dir() and not path.is_symlink()})}
        write_json(state_file, state)
        return state
    except (InstallError, OSError):
        for path in reversed(made):
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        raise


def doctor(prefix: Path, *, check_service: bool = True) -> list[dict]:
    state = read_json(prefix / "install-state.json")
    release = prefix / relative_path(state["release"])
    app = release / "app"
    checks = contract_checks(app)
    checks.append({"check": "installed-files", "ok": inventory_matches(prefix, state["owned"]),
                   "remedy": "reconcile modified files or reinstall in a fresh prefix"})
    runtime = read_json(release / "runtime.json")
    for name, code in (("daemon", DAEMON_IMPORTS), ("worker", WORKER_IMPORTS)):
        try:
            run_checked([str(release / name / "bin/python"), "-I", "-B", "-c", code], timeout=30)
            passed = True
        except InstallError:
            passed = False
        checks.append({"check": name + "-imports", "ok": passed,
                       "remedy": "restore native packages or reinstall the pinned environment"})
    checks.append({"check": "model-integrity", "ok": verify_model(Path(runtime["ARCA_DICTA_MODEL_DIR"]), model_manifest(app)),
                   "remedy": "download the pinned model again"})
    if check_service:
        try:
            config_home = absolute_path(state["config_home"])
            _, dropin = service_paths(config_home)
            systemd = Systemd()
            info = systemd.inspect()
            foreign = foreign_overrides(info, dropin)
            activated = (prefix / "activation.json").exists()
            checks.append({"check": "service-overrides", "ok": activated or not foreign,
                           "remedy": "review baseline, then use activate --adopt-existing"})
            checks.append({"check": "native-service-active", "ok": info["active"],
                           "remedy": "activate explicitly after configuring provider credentials"})
            if activated:
                checks.extend(activation_checks(prefix, systemd))
        except InstallError:
            checks.append({"check": "service-session", "ok": False,
                           "remedy": "run inside the user's systemd session or use --no-service"})
    return checks


def uninstall(prefix: Path) -> list[str]:
    if (prefix / "activation.json").exists():
        raise InstallError("active-installation: deactivate successfully before uninstalling")
    state_file = prefix / "install-state.json"
    if not state_file.exists():
        return []
    state = read_json(state_file)
    preserved = []
    for name, expected in state["owned"].items():
        path = prefix / relative_path(name)
        if not path.exists() and not path.is_symlink():
            continue
        if not inventory_matches(prefix, {name: expected}):
            preserved.append(name)
            continue
        path.unlink()
    # rmdir never removes unknown user files. Backups are outside this tree.
    for directory in sorted((prefix / relative_path(name) for name in state["directories"]),
                            key=lambda path: len(path.parts), reverse=True):
        try:
            if directory.is_symlink() or linked_parent(directory, prefix):
                continue
            directory.rmdir()
        except OSError:
            pass
    state_file.unlink()
    return preserved


def main(argv: list[str] | None = None) -> int:
    default_data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", default=str(default_data / "arca-dicta"))
    commands = parser.add_subparsers(dest="command", required=True)
    install_parser = commands.add_parser("install", help="prepare only; never starts a service")
    install_parser.add_argument("--download-model", action="store_true")
    install_parser.add_argument("--model-dir")
    install_parser.add_argument("--uv", default=shutil.which("uv") or "uv")
    diagnostic = commands.add_parser("doctor", help="read-only local checks; no capture or provider call")
    diagnostic.add_argument("--no-service", action="store_true")
    activation = commands.add_parser("activate", help="replace the one native service entrypoint reversibly")
    activation.add_argument("--adopt-existing", action="store_true")
    commands.add_parser("deactivate", help="restore prior service/configuration; preserve unrelated changes")
    commands.add_parser("uninstall", help="remove unchanged owned files, retaining backups and user edits")
    arguments = parser.parse_args(argv)
    try:
        prefix = absolute_path(arguments.prefix)
        if arguments.command == "doctor":
            checks = doctor(prefix, check_service=not arguments.no_service)
            print(json.dumps({"checks": checks, "physical_validation": "not-performed",
                              "providers": "not-contacted", "microphone": "not-opened"}, indent=2))
            return 0 if all(item["ok"] for item in checks) else 1
        with installation_lock(prefix):
            if arguments.command == "install":
                config_home = absolute_path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
                prepare(ROOT, prefix, config_home, uv=arguments.uv, download=arguments.download_model,
                        external_model=absolute_path(arguments.model_dir) if arguments.model_dir else None)
                print("Prepared ARCA Dicta. Service and desktop configuration were not changed.")
            elif arguments.command == "activate":
                if not all(item["ok"] for item in doctor(prefix, check_service=False)):
                    raise InstallError("preflight-failed: run doctor --no-service and resolve its failures")
                state = read_json(prefix / "install-state.json")
                activate(prefix, absolute_path(state["config_home"]), Systemd(), adopt_existing=arguments.adopt_existing)
                print("Activated the candidate in hyprwhspr.service. Physical validation is still required.")
            elif arguments.command == "deactivate":
                deactivate(prefix, Systemd())
                print("Previous service/configuration restored; private backups retained.")
            else:
                preserved = uninstall(prefix)
                print(f"Uninstalled owned files; preserved {len(preserved)} modified files and private backups.")
        return 0
    except (InstallError, OSError, ValueError, KeyError):
        error = sys.exception()
        message = str(error) if isinstance(error, InstallError) else "local-operation-failed: check permissions, disk space and installation state"
        print(message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
