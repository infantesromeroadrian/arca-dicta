"""Explicit, bounded physical-test instrumentation; never imported by the product."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import runpy
import stat
import subprocess
import sys
import time
from typing import Callable


SERVICE_LOG_SHA256 = "f97744bfd813c2b91a60ce7cab6e776f559467f196d5b6b8757ddec41eb5fad4"
MAX_FRAME_BYTES = 10_000_000
MAX_TOTAL_BYTES = 128_000_000
MAX_MANIFEST_BYTES = 1_000_000
FRAME_NAME = re.compile(r"(?:hud|target)-[0-9]{4}\.png")
REGION_PATTERN = re.compile(r"(-?\d+),(-?\d+) (\d+)x(\d+)")


class CaptureError(ValueError):
    """The explicit validation-only capture contract was not met."""


def private_file(path: Path, *, create: bool) -> int:
    flags = os.O_WRONLY | os.O_NOFOLLOW | (os.O_CREAT | os.O_EXCL if create else os.O_APPEND)
    descriptor = os.open(path, flags, 0o600)
    metadata = os.fstat(descriptor)
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600):
        os.close(descriptor)
        raise CaptureError("output must be an owned regular file with mode 0600")
    return descriptor


def write_private(path: Path, data: bytes) -> None:
    descriptor = private_file(path, create=True)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)


def emit_numeric(descriptor: int, event: dict) -> None:
    payload = (json.dumps(event, separators=(",", ":")) + "\n").encode("ascii")
    if os.write(descriptor, payload) != len(payload):
        raise OSError("incomplete numeric event write")


class AckObserver:
    """Observe only the exact native post-callback ACK; preserve normal logging."""

    def __init__(self, original: Callable, emit: Callable[[dict], None], *,
                 max_events: int, duration_seconds: float,
                 clock: Callable[[], int] = time.monotonic_ns) -> None:
        if not 1 <= max_events <= 200 or not 1 <= duration_seconds <= 3600:
            raise CaptureError("ACK collection must have finite event/time limits")
        self.original, self.emit, self.clock = original, emit, clock
        self.max_events = max_events
        self.deadline = clock() + int(duration_seconds * 1_000_000_000)
        self.count = 0
        self.errors = 0
        self.max_write_ns = 0

    def __call__(self, message: object = "") -> None:
        if type(message) is str and message == "Recording started" and self.count < self.max_events:
            timestamp = self.clock()
            if timestamp <= self.deadline:
                self.count += 1
                try:
                    self.emit({"event": "capture_ack", "sequence": self.count,
                               "monotonic_ns": timestamp})
                except OSError:
                    # Observation failure must never change capture/review/delivery.
                    self.errors += 1
                finally:
                    self.max_write_ns = max(self.max_write_ns, self.clock() - timestamp)
        self.original(message)


def wrap(entrypoint: Path, events: Path, service_log: Path, *,
         max_events: int, duration_seconds: float) -> None:
    if hashlib.sha256(service_log.read_bytes()).hexdigest() != SERVICE_LOG_SHA256:
        raise CaptureError("native service_log contract differs from verified 1.49.0")
    if not entrypoint.is_file() or entrypoint.name != "main.py":
        raise CaptureError("wrapper requires the explicit baseline/candidate main.py")
    descriptor = private_file(events, create=True)
    previous_module = sys.modules.get("service_log")
    previous_argv, previous_path = sys.argv, sys.path[:]
    try:
        spec = importlib.util.spec_from_file_location("service_log", service_log)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        observer = AckObserver(module.log, lambda event: emit_numeric(descriptor, event),
                               max_events=max_events, duration_seconds=duration_seconds)
        module.log = observer
        sys.modules["service_log"] = module
        sys.path.insert(0, str(entrypoint.resolve().parent))
        sys.argv = [str(entrypoint)]
        try:
            runpy.run_path(str(entrypoint), run_name="__main__")
        finally:
            try:
                emit_numeric(descriptor, {"event": "collector_end", "acks": observer.count,
                                          "errors": observer.errors,
                                          "max_write_ns": observer.max_write_ns})
            except OSError:
                pass
    finally:
        os.close(descriptor)
        sys.argv, sys.path[:] = previous_argv, previous_path
        if previous_module is None:
            sys.modules.pop("service_log", None)
        else:
            sys.modules["service_log"] = previous_module


def control(cli: Path, action: str, events: Path, trial_id: int) -> dict:
    """Bracket dispatch through the unchanged CLI, without reading its output."""
    if action not in {"start", "stop", "cancel"} or not 1 <= trial_id <= 200:
        raise CaptureError("invalid explicit control request")
    if not cli.is_absolute() or not cli.is_file() or not os.access(cli, os.X_OK):
        raise CaptureError("control requires an existing absolute executable path")
    descriptor = private_file(events, create=True)
    try:
        before = time.monotonic_ns()
        status = "completed"
        try:
            process = subprocess.run([str(cli), "record", action], stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     timeout=5, check=False)
            returncode = process.returncode
        except subprocess.TimeoutExpired:
            returncode, status = -1, "timeout"
        except OSError:
            returncode, status = -2, "launch-failed"
        after = time.monotonic_ns()
        result = {"event": "command_dispatch", "action": action, "trial_id": trial_id,
                  "interval_ns": [before, after], "returncode": returncode, "status": status}
        emit_numeric(descriptor, result)
        return result
    finally:
        os.close(descriptor)


def parse_region(value: str) -> str:
    match = REGION_PATTERN.fullmatch(value)
    if not match:
        raise argparse.ArgumentTypeError("ROI must be 'x,y WIDTHxHEIGHT'")
    x, y, width, height = map(int, match.groups())
    if (abs(x) > 32768 or abs(y) > 32768 or not 1 <= width <= 4096
            or not 1 <= height <= 4096 or width * height > 2_000_000):
        raise argparse.ArgumentTypeError("ROI exceeds the bounded synthetic test region")
    return f"{x},{y} {width}x{height}"


def capture_frames(directory: Path, regions: dict[str, str], *, seconds: float,
                   fps: float, grim: Path) -> dict:
    if (not 1 <= seconds <= 60 or not 1 <= fps <= 30
            or not grim.is_absolute() or not grim.is_file()):
        raise CaptureError("capture requires a bounded duration/rate and absolute grim executable")
    if set(regions) != {"hud", "target"}:
        raise CaptureError("capture requires exactly HUD and target ROIs")
    regions = {name: parse_region(value) for name, value in regions.items()}
    directory.mkdir(mode=0o700)
    if stat.S_IMODE(directory.stat().st_mode) != 0o700:
        raise CaptureError("temporary frame directory must have mode 0700")
    started = time.monotonic_ns()
    deadline = started + int(seconds * 1_000_000_000)
    period = int(1_000_000_000 / fps)
    frame_limit = math.ceil(seconds * fps)
    manifest = {"schema": 1, "clock": "host-monotonic", "regions": regions, "capture_scale": 1,
                "started_ns": started, "ended_ns": started, "frames": [],
                "bytes": 0, "failures": 0, "stop_reason": "duration"}
    try:
        for index in range(1, frame_limit + 1):
            due = started + (index - 1) * period
            if time.monotonic_ns() < due:
                time.sleep(max(0, due - time.monotonic_ns()) / 1_000_000_000)
            if time.monotonic_ns() >= deadline:
                break
            for region, geometry in regions.items():
                before = time.monotonic_ns()
                if before >= deadline:
                    break
                try:
                    result = subprocess.run([str(grim), "-g", geometry, "-s", "1", "-t", "png", "-"],
                                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                            stderr=subprocess.DEVNULL, timeout=2, check=False)
                    after = time.monotonic_ns()
                    valid = (result.returncode == 0 and 8 < len(result.stdout) <= MAX_FRAME_BYTES
                             and result.stdout.startswith(b"\x89PNG\r\n\x1a\n"))
                    payload = result.stdout if valid else b""
                except subprocess.TimeoutExpired:
                    after, payload = time.monotonic_ns(), b""
                if manifest["bytes"] + len(payload) > MAX_TOTAL_BYTES:
                    manifest["stop_reason"] = "byte-limit"
                    return manifest
                name = f"{region}-{index:04d}.png"
                if payload:
                    write_private(directory / name, payload)
                    manifest["bytes"] += len(payload)
                else:
                    manifest["failures"] += 1
                manifest["frames"].append({"region": region, "index": index,
                                           "interval_ns": [before, after],
                                           "status": "captured" if payload else "failed"})
    except KeyboardInterrupt:
        manifest["stop_reason"] = "interrupted"
    finally:
        manifest["ended_ns"] = time.monotonic_ns()
        write_private(directory / "manifest.json", (json.dumps(manifest, indent=2) + "\n").encode())
    return manifest


def read_manifest(directory: Path) -> dict:
    metadata = directory.lstat()
    if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700):
        raise CaptureError("not an owned private frame directory")
    path = directory / "manifest.json"
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_MANIFEST_BYTES:
        raise CaptureError("invalid numeric frame manifest")
    data = json.loads(path.read_bytes())
    if (not isinstance(data, dict) or data.get("schema") != 1
            or data.get("clock") != "host-monotonic" or not isinstance(data.get("frames"), list)
            or len(data["frames"]) > 3600):
        raise CaptureError("invalid numeric frame manifest")
    seen = set()
    for frame in data["frames"]:
        if (not isinstance(frame, dict)
                or set(frame) != {"region", "index", "interval_ns", "status"}
                or not isinstance(frame["region"], str) or frame["region"] not in {"hud", "target"}
                or type(frame["index"]) is not int or not 1 <= frame["index"] <= 1800
                or not isinstance(frame["status"], str) or frame["status"] not in {"captured", "failed"}):
            raise CaptureError("invalid numeric frame row")
        interval = frame["interval_ns"]
        if (not isinstance(interval, list) or len(interval) != 2
                or any(type(value) is not int or value < 0 for value in interval)
                or interval[0] > interval[1]):
            raise CaptureError("invalid frame clock interval")
        key = (frame["region"], frame["index"])
        if key in seen:
            raise CaptureError("duplicate frame row")
        seen.add(key)
    return data


def observation_bounds(data: dict, region: str, last_absent: int, first_present: int) -> list[int]:
    frames = {row["index"]: row for row in data["frames"] if row["region"] == region}
    if last_absent >= first_present or last_absent not in frames or first_present not in frames:
        raise CaptureError("select an absent frame followed by a present frame")
    before, after = frames[last_absent], frames[first_present]
    if before["status"] != "captured" or after["status"] != "captured":
        raise CaptureError("observation endpoints require captured images")
    return [before["interval_ns"][0], after["interval_ns"][1]]


def purge_frames(directory: Path) -> dict:
    data = read_manifest(directory)
    names = {f"{row['region']}-{row['index']:04d}.png" for row in data["frames"]
             if row["status"] == "captured"}
    if any(not FRAME_NAME.fullmatch(name) for name in names):
        raise CaptureError("invalid generated frame name")
    expected = names | {"manifest.json"}
    if {path.name for path in directory.iterdir()} != expected:
        raise CaptureError("unexpected files present; cleanup refused without deleting anything")
    for name in expected:
        metadata = (directory / name).lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise CaptureError("unexpected file type/owner; cleanup refused")
    for name in names:
        (directory / name).unlink()
    (directory / "manifest.json").unlink()
    directory.rmdir()
    return {"status": "removed", "frames": len(names)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    wrapper = commands.add_parser("wrap", help="run the sole runtime under the temporary ACK observer")
    wrapper.add_argument("--entrypoint", type=Path, required=True)
    wrapper.add_argument("--events", type=Path, required=True)
    wrapper.add_argument("--service-log", type=Path, default=Path("/usr/lib/hyprwhspr/lib/src/service_log.py"))
    wrapper.add_argument("--max-events", type=int, default=100)
    wrapper.add_argument("--seconds", type=float, default=1800)
    dispatch = commands.add_parser("control", help="explicitly request start/stop/cancel using the existing CLI")
    dispatch.add_argument("action", choices=("start", "stop", "cancel"))
    dispatch.add_argument("--cli", type=Path, default=Path("/usr/bin/hyprwhspr"))
    dispatch.add_argument("--events", type=Path, required=True)
    dispatch.add_argument("--trial-id", type=int, required=True)
    frames = commands.add_parser("frames", help="temporarily capture only the two explicit synthetic ROIs")
    frames.add_argument("directory", type=Path)
    frames.add_argument("--hud", type=parse_region, required=True)
    frames.add_argument("--target", type=parse_region, required=True)
    frames.add_argument("--seconds", type=float, default=15)
    frames.add_argument("--fps", type=float, default=10)
    frames.add_argument("--grim", type=Path, default=Path("/usr/bin/grim"))
    bounds = commands.add_parser("bounds", help="convert human-classified frames into conservative timestamp bounds")
    bounds.add_argument("directory", type=Path)
    bounds.add_argument("--region", choices=("hud", "target"), required=True)
    bounds.add_argument("--last-absent", type=int, required=True)
    bounds.add_argument("--first-present", type=int, required=True)
    purge = commands.add_parser("purge-frames", help="remove only validated generated files from one trial directory")
    purge.add_argument("directory", type=Path)
    commands.add_parser("stamp", help="read host monotonic_ns; this alone is not a product event")
    args = parser.parse_args(argv)
    try:
        if args.command == "wrap":
            wrap(args.entrypoint, args.events, args.service_log,
                 max_events=args.max_events, duration_seconds=args.seconds)
        elif args.command == "control":
            print(json.dumps(control(args.cli, args.action, args.events, args.trial_id)))
        elif args.command == "frames":
            result = capture_frames(args.directory, {"hud": args.hud, "target": args.target},
                                    seconds=args.seconds, fps=args.fps, grim=args.grim)
            print(json.dumps({key: result[key] for key in ("started_ns", "ended_ns", "bytes", "failures", "stop_reason")}))
        elif args.command == "bounds":
            interval = observation_bounds(read_manifest(args.directory), args.region,
                                          args.last_absent, args.first_present)
            print(json.dumps({"interval_ns": interval, "width_ns": interval[1] - interval[0]}))
        elif args.command == "purge-frames":
            print(json.dumps(purge_frames(args.directory)))
        else:
            print(json.dumps({"clock": "host-monotonic", "monotonic_ns": time.monotonic_ns()}))
        return 0
    except (OSError, ValueError) as exc:
        print(f"physical-capture: {exc if isinstance(exc, CaptureError) else 'operation failed; no raw diagnostics retained'}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
