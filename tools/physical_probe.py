"""Numeric physical-trial evidence; importing this module has no side effects."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import math
import os
from pathlib import Path
import stat
import sys


MAX_TRIALS = 200
MAX_FILE_BYTES = 1_000_000
EVENTS = ("capture_ack", "preview_visible", "stop_dispatch", "paste_visible")
CASES = ("ES-TECH", "EN-TECH", "MIX-TECH", "PREVIEW", "RETRY", "CANCEL", "AUDIO")
PHRASES = {
    "ES-TECH": "No cambies request_id. Revisa el RAG pipeline con Docker y conserva 42.",
    "EN-TECH": "Do not rename request_id. Check the RAG pipeline with Docker and keep 42.",
    "MIX-TECH": "Revisa el deployment, keep request_id unchanged y no cambies 42.",
}
OUTCOMES = ("success", "failure", "timeout", "cancelled")
META_CHOICES = {
    "variant": ("baseline", "candidate"),
    "target": ("gtk-editor", "browser"),
    "temperature": ("cold", "warm"),
    "clock": ("host-monotonic", "video-relative"),
    "observation": ("frame-intervals", "manual-unbounded", "events-only"),
    "ack_source": ("native-capture-ack", "visual-proxy"),
}


class ProbeError(ValueError):
    """Numeric evidence violates the measurement contract."""


def require_integer(value: object, lower: int, upper: int) -> int:
    if type(value) is not int or not lower <= value <= upper:
        raise ProbeError("integer outside allowed bounds")
    return value


@dataclass(frozen=True, slots=True)
class Interval:
    lower_ns: int
    upper_ns: int

    def __post_init__(self) -> None:
        require_integer(self.lower_ns, 0, 2**63 - 1)
        require_integer(self.upper_ns, self.lower_ns, 2**63 - 1)

    @classmethod
    def from_json(cls, value: object) -> Interval:
        if not isinstance(value, list) or len(value) != 2:
            raise ProbeError("event must be a two-element nanosecond interval")
        return cls(*value)

    def as_list(self) -> list[int]:
        return [self.lower_ns, self.upper_ns]


def elapsed(start: Interval, end: Interval) -> Interval:
    if end.upper_ns < start.lower_ns:
        raise ProbeError("event order is impossible")
    return Interval(max(0, end.lower_ns - start.upper_ns), end.upper_ns - start.lower_ns)


def nearest_rank(values: list[int], percentile: int) -> int:
    if not values or not 1 <= percentile <= 100:
        raise ProbeError("percentile requires samples and a rank in 1..100")
    return sorted(values)[math.ceil(len(values) * percentile / 100) - 1]


def interval_statistics(samples: list[Interval], *, percentiles: bool) -> dict:
    result = {"n": len(samples), "unit": "ms", "min_bounds": None,
              "max_bounds": None, "p50_bounds": None, "p95_bounds": None}
    if not samples:
        return result
    lower = [sample.lower_ns for sample in samples]
    upper = [sample.upper_ns for sample in samples]
    result["min_bounds"] = [min(lower) / 1_000_000, min(upper) / 1_000_000]
    result["max_bounds"] = [max(lower) / 1_000_000, max(upper) / 1_000_000]
    if percentiles and len(samples) >= 20:
        for percentile in (50, 95):
            result[f"p{percentile}_bounds"] = [
                nearest_rank(lower, percentile) / 1_000_000,
                nearest_rank(upper, percentile) / 1_000_000,
            ]
    return result


def validate_dataset(data: object) -> dict:
    if not isinstance(data, dict) or set(data) != {"schema", "condition_id", "rows", *META_CHOICES}:
        raise ProbeError("dataset fields do not match the numeric schema")
    if type(data["schema"]) is not int or data["schema"] != 1:
        raise ProbeError("unsupported schema")
    require_integer(data["condition_id"], 1, 10000)
    for key, choices in META_CHOICES.items():
        if data[key] not in choices:
            raise ProbeError("unsupported condition metadata")
    if not isinstance(data["rows"], list) or len(data["rows"]) > MAX_TRIALS:
        raise ProbeError("too many trials")
    trial_ids = set()
    for row in data["rows"]:
        if not isinstance(row, dict) or set(row) != {
            "trial_id", "case_id", "outcome", "quality", "revised_confirmed", "events"
        }:
            raise ProbeError("trial fields do not match the numeric schema")
        trial_id = require_integer(row["trial_id"], 1, MAX_TRIALS)
        if trial_id in trial_ids:
            raise ProbeError("duplicate trial id")
        trial_ids.add(trial_id)
        if row["case_id"] not in CASES or row["outcome"] not in OUTCOMES:
            raise ProbeError("unknown case or outcome")
        if row["quality"] not in ("pass", "fail", "unassessed"):
            raise ProbeError("unknown quality status")
        if type(row["revised_confirmed"]) is not bool:
            raise ProbeError("revision confirmation must be boolean")
        raw_events = row["events"]
        if not isinstance(raw_events, dict) or set(raw_events) - set(EVENTS):
            raise ProbeError("unknown timestamp field")
        events = {key: Interval.from_json(value) for key, value in raw_events.items()}
        # Propagate the earliest compatible time through the complete sequence;
        # consecutive pairs can each overlap without admitting one global order.
        earliest = 0
        for event in EVENTS:
            if event in events:
                interval = events[event]
                earliest = max(earliest, interval.lower_ns)
                if earliest > interval.upper_ns:
                    raise ProbeError("event sequence is globally impossible")
        if row["outcome"] == "success" and (
            set(events) != set(EVENTS) or not row["revised_confirmed"] or row["quality"] != "pass"
        ):
            raise ProbeError("success requires all endpoints and confirmed revised content/quality")
    return data


def read_dataset(path: Path) -> dict:
    with path.open("rb") as stream:
        raw = stream.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise ProbeError("dataset exceeds size limit")
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ProbeError("dataset is not valid JSON") from exc
    return validate_dataset(data)


def write_dataset(path: Path, data: dict, *, create: bool = False) -> None:
    validate_dataset(data)
    flags = os.O_WRONLY | os.O_NOFOLLOW | (os.O_CREAT | os.O_EXCL if create else 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise ProbeError("evidence output must be an owned regular file")
        payload = (json.dumps(data, indent=2, sort_keys=True) + "\n").encode()
        os.fchmod(descriptor, 0o600)
        os.ftruncate(descriptor, 0)
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(payload)
    finally:
        os.close(descriptor)


def summarize(data: dict) -> dict:
    validate_dataset(data)
    groups = {}
    # Languages/cases stay separate even when the operator reuses a condition id.
    for case_id in sorted({row["case_id"] for row in data["rows"]}):
        rows = [row for row in data["rows"] if row["case_id"] == case_id]
        counts = Counter(row["outcome"] for row in rows)
        preview, paste = [], []
        for row in rows:
            if row["outcome"] != "success":
                continue
            events = {key: Interval.from_json(value) for key, value in row["events"].items()}
            preview.append(elapsed(events["capture_ack"], events["preview_visible"]))
            paste.append(elapsed(events["stop_dispatch"], events["paste_visible"]))
        bounded = (data["observation"] == "frame-intervals"
                   and data["ack_source"] == "native-capture-ack"
                   and data["clock"] == "host-monotonic")
        percentiles = bounded and data["temperature"] == "warm"
        groups[case_id] = {
            "attempts": len(rows), **{status: counts[status] for status in OUTCOMES},
            "quality_failed": sum(row["quality"] == "fail" for row in rows),
            "evidence": "bounded-observation" if bounded else "exploratory-only",
            "ack_to_first_visible_preview": interval_statistics(preview, percentiles=percentiles),
            "stop_command_to_first_observed_revised_paste": interval_statistics(paste, percentiles=percentiles),
        }
    return {key: data[key] for key in ("schema", "condition_id", *META_CHOICES)} | {"cases": groups}


def parse_interval(value: str) -> list[int]:
    try:
        parts = value.split(":")
        if len(parts) == 1:
            parts *= 2
        if len(parts) != 2:
            raise ProbeError("use lower_ms:upper_ms or a single millisecond timestamp")
        timestamps = []
        for part in parts:
            number = Decimal(part) * 1_000_000
            if not number.is_finite() or number != number.to_integral_value():
                raise ProbeError("timestamps must be finite with nanosecond precision")
            timestamps.append(int(number))
        return Interval(*timestamps).as_list()
    except (InvalidOperation, ValueError, OverflowError) as exc:
        raise argparse.ArgumentTypeError("invalid nonnegative timestamp interval") from exc


def parse_nanoseconds(value: str) -> list[int]:
    try:
        parts = value.split(":")
        if len(parts) == 1:
            parts *= 2
        if len(parts) != 2:
            raise ValueError
        return Interval(*(int(part) for part in parts)).as_list()
    except ValueError as exc:
        raise argparse.ArgumentTypeError("use integer lower_ns:upper_ns or one timestamp") from exc


def open_editor(case_id: str) -> int:
    """Launch only on explicit request; text lives in the GTK buffer, never a file."""
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    app = Gtk.Application(application_id="local.arca.DictaValidation")

    def activate(application) -> None:
        window = Gtk.ApplicationWindow(application=application, title="ARCA Dicta Validation")
        window.set_default_size(900, 450)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        for margin in ("top", "bottom", "start", "end"):
            getattr(box, "set_margin_" + margin)(20)
        phrase = Gtk.Label(label=f"{case_id}: {PHRASES[case_id]}")
        phrase.set_wrap(True)
        box.append(phrase)
        editor = Gtk.TextView(editable=True, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        editor.set_vexpand(True)
        box.append(editor)
        clear = Gtk.Button(label="Vaciar y enfocar")

        def reset(_button) -> None:
            editor.get_buffer().set_text("")
            editor.grab_focus()

        clear.connect("clicked", reset)
        box.append(clear)
        box.append(Gtk.Label(label="Solo frase sintética. Sin guardar, red ni control del micrófono."))
        window.set_child(box)
        window.present()
        editor.grab_focus()

    app.connect("activate", activate)
    return app.run([])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    new = commands.add_parser("new", help="create one condition/variant/temperature evidence file")
    new.add_argument("path", type=Path)
    new.add_argument("--condition-id", type=int, required=True)
    for key, choices in META_CHOICES.items():
        new.add_argument("--" + key.replace("_", "-"), choices=choices, required=True)
    add = commands.add_parser("add", help="append one visually classified attempt with explicit units")
    add.add_argument("path", type=Path)
    add.add_argument("--trial-id", type=int, required=True)
    add.add_argument("--case-id", choices=CASES, required=True)
    add.add_argument("--outcome", choices=OUTCOMES, required=True)
    add.add_argument("--quality", choices=("pass", "fail", "unassessed"), default="unassessed")
    add.add_argument("--revised-confirmed", action="store_true")
    for event in EVENTS:
        group = add.add_mutually_exclusive_group()
        option = "--" + event.replace("_", "-")
        group.add_argument(option + "-ms", type=parse_interval, dest=event)
        group.add_argument(option + "-ns", type=parse_nanoseconds, dest=event)
    summary = commands.add_parser("summary")
    summary.add_argument("path", type=Path)
    editor = commands.add_parser("editor", help="open a memory-only native GTK4 synthetic paste target")
    editor.add_argument("--case-id", choices=tuple(PHRASES), default="ES-TECH")
    args = parser.parse_args(argv)
    try:
        if args.command == "new":
            data = {"schema": 1, "condition_id": args.condition_id, "rows": []}
            data.update({key: getattr(args, key) for key in META_CHOICES})
            write_dataset(args.path, data, create=True)
        elif args.command == "add":
            data = read_dataset(args.path)
            data["rows"].append({
                "trial_id": args.trial_id, "case_id": args.case_id,
                "outcome": args.outcome, "quality": args.quality,
                "revised_confirmed": args.revised_confirmed,
                "events": {event: getattr(args, event) for event in EVENTS
                           if getattr(args, event) is not None},
            })
            write_dataset(args.path, data)
        elif args.command == "summary":
            print(json.dumps(summarize(read_dataset(args.path)), indent=2, sort_keys=True))
        else:
            return open_editor(args.case_id)
        return 0
    except (OSError, ProbeError) as exc:
        # Dataset errors never echo user-supplied contents or paths.
        print(f"physical-probe: {exc if isinstance(exc, ProbeError) else 'cannot access evidence file'}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
