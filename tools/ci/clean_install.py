"""Exercise a real source installer as a new container user, without activation."""

from __future__ import annotations

import argparse
from array import array
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import tarfile
import tempfile
import time
import wave


def sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def file_state(path: Path) -> tuple[str, int]:
    return sha256(path), stat.S_IMODE(path.stat().st_mode)


def run(arguments: list[str], *, input_bytes: bytes | None = None,
        timeout: int = 900, environment: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    result = subprocess.run(arguments, input=input_bytes, capture_output=True,
                            timeout=timeout, env=environment, check=False)
    if result.returncode:
        # Inputs here are synthetic; still keep command output out of reports.
        raise RuntimeError(f"integration command failed with exit {result.returncode}")
    return result


def frame(payload: bytes) -> bytes:
    return struct.pack("!I", len(payload)) + payload


def worker_inference(runtime: dict[str, str], app: Path) -> dict:
    spoken = run(["/usr/bin/espeak-ng", "--stdout", "-s", "140",
                  "This is a synthetic installation check."], timeout=15).stdout
    with wave.open(io.BytesIO(spoken)) as audio:
        if audio.getnchannels() != 1 or audio.getsampwidth() != 2:
            raise RuntimeError("synthetic speech format changed")
        sample_rate = audio.getframerate()
        pcm = audio.readframes(sample_rate * 8)
    samples = array("h")
    samples.frombytes(pcm)
    payload = struct.pack(f"<{len(samples)}f", *(value / 32768 for value in samples))
    request = {"request": 1, "samples": len(samples), "sample_rate": sample_rate, "language": "en"}
    environment = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
                   "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
    result = run([runtime["ARCA_DICTA_WORKER_PYTHON"], "-B", str(app / "worker.py"),
                  runtime["ARCA_DICTA_MODEL_DIR"]],
                 input_bytes=frame(json.dumps(request).encode()) + frame(payload),
                 timeout=120, environment=environment)
    stream = io.BytesIO(result.stdout)
    messages = []
    while header := stream.read(4):
        if len(header) != 4:
            raise RuntimeError("worker returned a truncated frame")
        length = struct.unpack("!I", header)[0]
        if not 0 < length <= 8192:
            raise RuntimeError("worker returned an invalid frame size")
        messages.append(json.loads(stream.read(length)))
    if (len(messages) != 2 or messages[0] != {"kind": "ready", "protocol": 1}
            or messages[1].get("kind") != "result" or messages[1].get("request") != 1
            or not messages[1].get("text", "").strip()):
        raise RuntimeError("worker did not return ready and a nonempty synthetic result")
    return {"ready": True, "result": True, "sample_rate": sample_rate,
            "samples": len(samples), "text_characters": len(messages[1]["text"]),
            "decode_ms": messages[1]["decode_ms"], "audio_origin": "espeak-ng stdout",
            "audio_persisted": False, "text_persisted": False}


def exercise(artifact: Path, output: Path) -> dict:
    if not Path("/.dockerenv").is_file() or os.getuid() == 0:
        raise RuntimeError("use the unprivileged disposable container user")
    report = {
        "scope": "Clean Arch userspace and fresh user; no service activation or physical desktop",
        "artifact_sha256": sha256(artifact),
        "uid": os.getuid(), "steps": [], "status": "running",
        "host_devices_mounted": False, "provider_credentials_used": False,
    }
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / "clean-install.json"

    def record(name: str, **fields) -> None:
        report["steps"].append({"step": name, **fields})
        report_path.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")

    with tempfile.TemporaryDirectory(prefix="dicta-clean-extract-") as scratch:
        directory = Path(scratch)
        with tarfile.open(artifact) as archive:
            if any(not (entry.isfile() or entry.isdir()) for entry in archive):
                raise RuntimeError("release contains a non-regular archive entry")
            archive.extractall(directory, filter="data")
        roots = list(directory.iterdir())
        if len(roots) != 1 or not roots[0].is_dir():
            raise RuntimeError("release root is not unique")
        source = roots[0]
        report["manifest_sha256"] = sha256(source / "release-manifest.json")
        report["version"] = (source / "VERSION").read_text().strip()
        prefix = Path.home() / ".local/share/ARCA Dicta"
        if prefix.exists():
            raise RuntimeError("clean installation prefix already exists")
        fixtures = {
            Path.home() / ".config/hyprwhspr/config.json": b'{"synthetic_fixture":true,"recording_mode":"toggle"}\n',
            Path.home() / ".config/systemd/user/hyprwhspr.service.d/10-foreign.conf": b"[Service]\nEnvironment=SYNTHETIC_FIXTURE=true\n",
            Path.home() / ".local/share/hyprwhspr/credentials/groq": b"synthetic-placeholder-not-a-credential\n",
            Path.home() / ".config/arca-dicta/secrets.env": b"SYNTHETIC_FIXTURE=not-a-credential\n",
            Path.home() / ".config/hypr/hyprland.conf": b"# Synthetic unrelated compositor configuration.\n",
        }
        for path, contents in fixtures.items():
            if path.exists():
                raise RuntimeError("fixture destination is not fresh")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(contents)
            path.chmod(0o600)
        before = {path: file_state(path) for path in fixtures}
        native = Path("/usr/bin/hyprwhspr")
        native_before = file_state(native)
        command = [str(source / "install"), "--prefix", str(prefix)]
        started = time.monotonic()
        run([*command, "install", "--download-model"])
        record("install", command="<artifact>/install --prefix <prefix> install --download-model",
               elapsed_seconds=round(time.monotonic() - started, 3))
        diagnostic = json.loads(run([*command, "doctor", "--no-service"]).stdout)
        if not all(item["ok"] for item in diagnostic["checks"]):
            raise RuntimeError("doctor reported a failed check")
        record("doctor", command="<artifact>/install --prefix <prefix> doctor --no-service",
               checks=len(diagnostic["checks"]), passed=sum(item["ok"] for item in diagnostic["checks"]),
               native_contract_entries=sum(item["check"].startswith(("core:", "hud:"))
                                           for item in diagnostic["checks"]))
        state_path = prefix / "install-state.json"
        state_before = state_path.read_bytes()
        state = json.loads(state_before)
        release = prefix / state["release"]
        runtime = json.loads((release / "runtime.json").read_text())
        record("worker", command="<worker-python> -B <app>/worker.py <model>",
               **worker_inference(runtime, release / "app"))
        run([*command, "install", "--download-model"])
        if state_path.read_bytes() != state_before:
            raise RuntimeError("repeated installation changed its state")
        record("idempotent-install", command="<artifact>/install --prefix <prefix> install --download-model",
               state_unchanged=True)

        unknown = prefix / "user-note.txt"
        unknown.write_text("Synthetic user-owned file.\n")
        empty = prefix / "user-empty-directory"
        empty.mkdir()
        edited = release / "app/README.md"
        edited.write_bytes(edited.read_bytes() + b"\nSynthetic user edit for removal preservation.\n")
        preserved_before = {path: file_state(path) for path in (unknown, edited)}
        run([*command, "uninstall"])
        if state_path.exists() or (prefix / "run").exists() or (prefix / "models").exists():
            raise RuntimeError("uninstall left an owned runtime or model")
        if any(file_state(path) != previous for path, previous in preserved_before.items()) or not empty.is_dir():
            raise RuntimeError("uninstall changed an unknown or edited user file")
        residual_owned = [name for name in state["owned"] if (prefix / name).exists()
                          or (prefix / name).is_symlink()]
        if residual_owned != [str(edited.relative_to(prefix))]:
            raise RuntimeError("uninstall retained unexpected owned files")
        if any(file_state(path) != previous for path, previous in before.items()) or file_state(native) != native_before:
            raise RuntimeError("installer changed a foreign fixture or native binary")
        record("uninstall", command="<artifact>/install --prefix <prefix> uninstall",
               residual_owned_files=len(residual_owned), preserved_edited_files=1,
               preserved_unknown_files=1, preserved_empty_directories=1,
               preserved_foreign_files=len(fixtures), native_binary_unchanged=True,
               native_binary_sha256=native_before[0], remaining_lock_file=(prefix / ".install.lock").is_file())
        report["status"] = "pass"
        report_path.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        report = exercise(arguments.artifact, arguments.output)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Clean installation check failed: {type(error).__name__}: {error}")
        return 1
    print(f"Clean installation check: {report['status']}; {len(report['steps'])} steps")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
