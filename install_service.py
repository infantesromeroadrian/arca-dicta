"""Reversible ownership of one native service and a narrow configuration patch."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import tempfile
import time

SERVICE = "hyprwhspr.service"
DROPIN_NAME = "zz-arca-dicta.conf"
# Managed settings from hyprwhspr 1.49.0 build_default_config(), checked against
# the pinned native source in test_install. Sparse native saves omit defaults.
# Keep this snapshot local: reading defaults must not construct ConfigManager
# or trigger native configuration/credential migrations during rollback.
NATIVE_MANAGED_DEFAULTS = {
    "transcription_backend": "pywhispercpp",
    "rest_api_provider": None,
    "rest_endpoint_url": None,
    "rest_fallback_endpoint_urls": [],
    "rest_headers": {},
    "rest_body": {},
    "language": None,
    "task": "transcribe",
    "post_transcription_hook": None,
    "realtime_live_typing": False,
    "auto_submit": False,
    "debug_recordings": False,
    "mic_osd_enabled": True,
    "mic_osd_style": "waveform",
    "audio_ducking": False,
    "audio_ducking_mode": "duck",
    "audio_ducking_percent": 50,
    "external_bindings": False,
    "recording_mode": "toggle",
}
_MISSING_SETTING = object()


class InstallError(RuntimeError):
    """An actionable installation failure with no secret-bearing payload."""


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise InstallError("invalid-json: restore the damaged installation/configuration file") from None
    if not isinstance(value, dict):
        raise InstallError("invalid-json: expected an object")
    return value


def atomic_write(path: Path, contents: bytes, mode: int = 0o600) -> None:
    if path.is_symlink():
        raise InstallError("symlink-conflict: refusing to replace a linked file")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".arca-dicta-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_json(path: Path, value: dict) -> None:
    atomic_write(path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode())


class Systemd:
    """The only live service boundary; tests supply an in-memory implementation."""

    def _run(self, *arguments: str) -> str:
        try:
            result = subprocess.run(
                ["/usr/bin/systemctl", "--user", *arguments],
                capture_output=True, text=True, timeout=30, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise InstallError("service-unavailable: run inside the user's systemd session") from None
        if result.returncode:
            raise InstallError("service-command-failed: inspect the native service locally; no logs were collected")
        return result.stdout.strip()

    def inspect(self) -> dict:
        output = self._run("show", SERVICE, "--property=FragmentPath,DropInPaths,ActiveState")
        fields = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
        if not fields.get("FragmentPath"):
            raise InstallError("service-missing: install the native hyprwhspr user unit first")
        return {
            "fragment": fields["FragmentPath"],
            "dropins": shlex.split(fields.get("DropInPaths", "")),
            "active": fields.get("ActiveState") == "active",
        }

    def stop(self) -> None:
        self._run("stop", SERVICE)

    def reload(self) -> None:
        self._run("daemon-reload")

    def start(self) -> None:
        self._run("start", SERVICE)
        # Catch immediate startup failure, without reading journal or dictation.
        for _ in range(5):
            time.sleep(0.2)
            if not self.inspect()["active"]:
                raise InstallError("service-start-failed: restoring the previous service and configuration")


def service_paths(config_home: Path) -> tuple[Path, Path]:
    return (config_home / "hyprwhspr/config.json",
            config_home / f"systemd/user/{SERVICE}.d/{DROPIN_NAME}")


def foreign_overrides(info: dict, own_dropin: Path) -> list[Path]:
    fragment = Path(info["fragment"])
    paths = [Path(value) for value in info["dropins"] if Path(value) != own_dropin]
    if fragment not in {Path("/usr/lib/systemd/user") / SERVICE,
                        Path("/lib/systemd/user") / SERVICE}:
        paths.append(fragment)
    return paths


def desired_config(original: dict, hook: Path) -> dict:
    """Preserve devices and bindings; own only the required dictation settings."""
    mode = original.get("recording_mode", "toggle")
    if mode not in {"toggle", "auto", "push_to_talk"}:
        raise InstallError("unsupported-recording-mode: select toggle, auto or push_to_talk first")
    body = original.get("rest_body", {})
    if not isinstance(body, dict):
        raise InstallError("invalid-rest-body: native configuration must contain an object")
    body = {key: value for key, value in body.items() if key != "language"}
    body["model"] = "whisper-large-v3-turbo"
    changes = {
        "transcription_backend": "rest-api",
        "rest_api_provider": "groq",
        "rest_endpoint_url": "https://api.groq.com/openai/v1/audio/transcriptions",
        "rest_fallback_endpoint_urls": [],
        "rest_headers": {},
        "rest_body": body,
        "language": None,
        "task": "transcribe",
        "post_transcription_hook": "exec " + shlex.quote(str(hook)),
        "realtime_live_typing": False,
        "auto_submit": False,
        "debug_recordings": False,
        "mic_osd_enabled": True,
        "mic_osd_style": "waveform",
        "audio_ducking": True,
        "audio_ducking_mode": "duck",
        "audio_ducking_percent": 100,
    }
    # Fresh installs wait for an explicit compositor binding instead of grabbing keys.
    if "external_bindings" not in original and "use_hypr_bindings" not in original:
        changes["external_bindings"] = True
        changes["use_hypr_bindings"] = True
    if "recording_mode" not in original:
        changes["recording_mode"] = mode
    # Legacy inline credentials would make upstream migrate and mutate the store.
    if original.get("rest_api_key"):
        raise InstallError("legacy-credential: migrate the native inline key before activation")
    return changes


def _unit_quote(value: str) -> str:
    if any(character in value for character in "\x00\r\n"):
        raise InstallError("invalid-path: service paths cannot contain control characters")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$") + '"'


def dropin_contents(runner: Path, secrets: Path) -> bytes:
    _unit_quote(str(secrets))  # Validate controls; EnvironmentFile takes one raw path, not shell quoting.
    return (
        "# Managed by ARCA Dicta; remove with install deactivate.\n"
        "[Service]\nExecStart=\nExecStart=" + _unit_quote(str(runner)) + "\n"
        "EnvironmentFile=-" + str(secrets).replace("%", "%%") + "\n"
    ).encode()


def _baseline_units(info: dict) -> dict[str, str]:
    files = [Path(info["fragment"]), *(Path(item) for item in info["dropins"])]
    try:
        return {str(path): digest(path) for path in files}
    except OSError:
        raise InstallError("unit-unreadable: cannot establish a reversible service baseline") from None


def _verify_units(saved: dict, own_dropin: Path, systemd: Systemd) -> None:
    current = systemd.inspect()
    units = _baseline_units({**current, "dropins": [
        value for value in current["dropins"] if Path(value) != own_dropin
    ]})
    if units != saved:
        raise InstallError("unit-drift: another service definition changed; reconcile it before rollback")


def managed_settings_match(current: dict, expected: dict) -> bool:
    """Only a missing known native default is equivalent to its explicit value."""
    return all(current.get(key, NATIVE_MANAGED_DEFAULTS.get(key, _MISSING_SETTING)) == value
               for key, value in expected.items())


def activation_checks(prefix: Path, systemd: Systemd) -> list[dict]:
    """Describe configured ownership, without claiming physical dictation works."""
    transaction = read_json(prefix / "activation.json")
    dropin = Path(transaction["dropin"])
    config = Path(transaction["config"])
    try:
        own_unit = (not dropin.is_symlink() and digest(dropin) == transaction["dropin_sha256"])
    except OSError:
        own_unit = False
    try:
        _verify_units(transaction["units"], dropin, systemd)
        baseline_intact = True
    except InstallError:
        baseline_intact = False
    try:
        current = read_json(config)
        config_intact = managed_settings_match(current, transaction["changes"])
    except InstallError:
        config_intact = False
    return [
        {"check": "activation-journal", "ok": transaction["phase"] == "active",
         "remedy": "complete deactivation after the interrupted transaction"},
        {"check": "owned-service-override", "ok": own_unit and baseline_intact,
         "remedy": "reconcile service drift against the private activation backup"},
        {"check": "managed-native-settings", "ok": config_intact,
         "remedy": "reconcile the managed settings before recording or deactivation"},
    ]


def activate(prefix: Path, config_home: Path, systemd: Systemd,
             *, adopt_existing: bool = False) -> None:
    state = read_json(prefix / "install-state.json")
    journal = prefix / "activation.json"
    config, dropin = service_paths(config_home)
    if journal.exists():
        raise InstallError("already-activated: use doctor or deactivate before changing activation")
    if dropin.exists() or dropin.is_symlink():
        raise InstallError("dropin-conflict: the target drop-in already exists and is not owned")
    info = systemd.inspect()
    foreign = foreign_overrides(info, dropin)
    if foreign and not adopt_existing:
        raise InstallError("existing-overrides: rerun activate --adopt-existing after reviewing the local baseline")
    if any(Path(value).name >= DROPIN_NAME for value in info["dropins"]):
        raise InstallError("dropin-order-conflict: a later override would supersede ARCA Dicta")
    if config.is_symlink():
        raise InstallError("config-symlink: resolve the native configuration explicitly before activation")
    original = read_json(config) if config.exists() else {}
    release = prefix / state["release"]
    changes = desired_config(original, release / "app/review-hook")
    contents = (json.dumps({**original, **changes}, indent=2, sort_keys=True) + "\n").encode()
    unit = dropin_contents(prefix / "run", config_home / "arca-dicta/secrets.env")
    baseline = _baseline_units(info)
    backup = prefix / "backups" / str(time.time_ns())
    backup.mkdir(parents=True, mode=0o700)
    os.chmod(backup.parent, 0o700)
    existing = config.exists()
    mode = stat.S_IMODE(config.stat().st_mode) if existing else 0o600
    if existing:
        atomic_write(backup / "config.json", config.read_bytes())
    for index, path in enumerate(baseline):
        atomic_write(backup / f"unit-{index}.conf", Path(path).read_bytes())
    transaction = {
        "phase": "prepared", "config": str(config), "dropin": str(dropin),
        "backup": str(backup.relative_to(prefix)), "config_existed": existing,
        "config_mode": mode, "previous_active": info["active"], "units": baseline,
        "original": original, "changes": changes,
        "config_sha256": hashlib.sha256(contents).hexdigest(),
        "dropin_sha256": hashlib.sha256(unit).hexdigest(),
    }
    write_json(journal, transaction)
    try:
        # Detect a concurrent editor before stopping the working runtime.
        _verify_units(baseline, dropin, systemd)
        if existing and config.read_bytes() != (backup / "config.json").read_bytes():
            raise InstallError("config-drift: native configuration changed during preflight")
        if not existing and config.exists():
            raise InstallError("config-drift: native configuration appeared during preflight")
        transaction["phase"] = "writing"
        write_json(journal, transaction)
        systemd.stop()
        atomic_write(config, contents, mode)
        atomic_write(dropin, unit)
        transaction["phase"] = "written"
        write_json(journal, transaction)
        systemd.reload()
        systemd.start()
        transaction["phase"] = "active"
        write_json(journal, transaction)
    except (InstallError, OSError):
        # The durable journal also supports an explicit retry after interruption.
        if transaction["phase"] != "prepared" or dropin.exists():
            deactivate(prefix, systemd)
        else:
            journal.unlink()
            if info["active"] and not systemd.inspect()["active"]:
                systemd.start()
        raise


def deactivate(prefix: Path, systemd: Systemd) -> None:
    journal = prefix / "activation.json"
    if not journal.exists():
        return
    transaction = read_json(journal)
    config = Path(transaction["config"])
    dropin = Path(transaction["dropin"])
    backup = prefix / transaction["backup"]
    if transaction["phase"] == "prepared" and not dropin.exists():
        # No configuration write is assumed: compare to the exact saved bytes.
        expected = (backup / "config.json").read_bytes() if transaction["config_existed"] else None
        current = config.read_bytes() if config.exists() else None
        if current != expected:
            raise InstallError("interrupted-activation: reconcile configuration against the private backup")
        journal.unlink()
        return
    missing_allowed = transaction["phase"] in {"writing", "restoring"}
    if (dropin.is_symlink() or (not dropin.is_file() and not missing_allowed)
            or (dropin.exists() and digest(dropin) != transaction["dropin_sha256"])):
        raise InstallError("dropin-drift: preserve the modified drop-in and reconcile before deactivation")
    _verify_units(transaction["units"], dropin, systemd)
    prior_bytes = ((backup / "config.json").read_bytes()
                   if transaction["config_existed"] else None)
    current_bytes = config.read_bytes() if config.exists() else None
    current = read_json(config) if config.exists() else {}
    original, changes = transaction["original"], transaction["changes"]
    # Native sparse saves may omit values equal to defaults; byte equality is
    # the fast path, while divergent managed values require explicit resolution.
    if transaction["phase"] == "restoring":
        restored = (backup / "restore-config").read_bytes() if transaction["restore_exists"] else None
        current_hash = hashlib.sha256(current_bytes).hexdigest() if current_bytes is not None else None
        if (current_bytes != restored and current_bytes != prior_bytes
                and current_hash != transaction["restore_source_sha256"]):
            raise InstallError("config-conflict: configuration changed during interrupted deactivation")
    elif current_bytes == prior_bytes or (config.exists() and digest(config) == transaction["config_sha256"]):
        restored = prior_bytes
    else:
        if not managed_settings_match(current, changes):
            raise InstallError("config-conflict: a managed setting changed; preserve it and reconcile before deactivation")
        for key in changes:
            if key in original:
                current[key] = original[key]
            else:
                current.pop(key, None)
        restored = (json.dumps(current, indent=2, sort_keys=True) + "\n").encode()
    if restored is not None:
        atomic_write(backup / "restore-config", restored)
    transaction["phase"] = "restoring"
    transaction["restore_exists"] = restored is not None
    transaction["restore_source_sha256"] = (
        hashlib.sha256(current_bytes).hexdigest() if current_bytes is not None else None
    )
    write_json(journal, transaction)
    systemd.stop()
    if restored is None:
        config.unlink(missing_ok=True)
    else:
        atomic_write(config, restored, transaction["config_mode"])
    dropin.unlink(missing_ok=True)
    systemd.reload()
    if transaction["previous_active"]:
        systemd.start()
    journal.unlink()
