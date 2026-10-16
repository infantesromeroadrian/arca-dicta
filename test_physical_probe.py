"""Synthetic evidence tests; never invoke GUI, capture, microphone or providers."""

from __future__ import annotations

from contextlib import redirect_stdout
import copy
import hashlib
import io
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools.physical_capture import (
    AckObserver, CaptureError, capture_frames, control, observation_bounds,
    parse_region, purge_frames, read_manifest, wrap,
)
from tools.physical_probe import (
    Interval, ProbeError, elapsed, main, nearest_rank, parse_interval,
    read_dataset, summarize, validate_dataset, write_dataset,
)


def dataset() -> dict:
    return {"schema": 1, "condition_id": 1, "variant": "baseline", "target": "gtk-editor",
            "temperature": "warm", "clock": "host-monotonic",
            "observation": "frame-intervals", "ack_source": "native-capture-ack", "rows": []}


def successful_trial(number: int) -> dict:
    offset = number * 1_000_000
    return {"trial_id": number, "case_id": "ES-TECH", "outcome": "success",
            "quality": "pass", "revised_confirmed": True,
            "events": {"capture_ack": [0, 0], "preview_visible": [offset, offset + 100_000],
                       "stop_dispatch": [100_000_000, 101_000_000],
                       "paste_visible": [102_000_000 + offset, 103_000_000 + offset]}}


class NumericProbeTests(unittest.TestCase):
    def test_interval_subtraction_keeps_both_observation_errors(self) -> None:
        self.assertEqual(elapsed(Interval(10, 13), Interval(20, 25)), Interval(7, 15))
        self.assertEqual(elapsed(Interval(10, 20), Interval(15, 25)), Interval(0, 15))
        with self.assertRaises(ProbeError):
            elapsed(Interval(30, 30), Interval(10, 20))

    def test_impossible_global_chronology_cannot_publish_twenty_success_percentiles(self) -> None:
        data = dataset()
        for number in range(1, 21):
            row = successful_trial(number)
            row["events"] = {"capture_ack": [100_000_000, 100_000_000],
                             "preview_visible": [0, 200_000_000],
                             "stop_dispatch": [50_000_000, 50_000_000],
                             "paste_visible": [60_000_000, 60_000_000]}
            data["rows"].append(row)
        with self.assertRaises(ProbeError):
            summarize(data)

    def test_global_chronology_rejects_nonadjacent_and_partial_event_inversions(self) -> None:
        for events in (
            {"capture_ack": [100, 100], "preview_visible": [0, 200],
             "stop_dispatch": [0, 200], "paste_visible": [60, 60]},
            {"capture_ack": [0, 200], "preview_visible": [100, 100],
             "stop_dispatch": [0, 200], "paste_visible": [60, 60]},
            {"capture_ack": [100, 100], "paste_visible": [60, 60]},
        ):
            data = dataset()
            row = successful_trial(1)
            row.update(outcome="failure", events=events)
            data["rows"] = [row]
            with self.subTest(events=events), self.assertRaises(ProbeError):
                validate_dataset(data)

    def test_overlapping_global_sequence_preserves_original_uncertainty(self) -> None:
        data = dataset()
        for number in range(1, 21):
            row = successful_trial(number)
            # Input dictionary order is not the event order. One valid sequence
            # is ACK=100, preview=100, stop=120, paste=120 milliseconds.
            row["events"] = {"paste_visible": [100_000_000, 300_000_000],
                             "stop_dispatch": [120_000_000, 180_000_000],
                             "preview_visible": [0, 200_000_000],
                             "capture_ack": [100_000_000, 150_000_000]}
            data["rows"].append(row)
        original = copy.deepcopy(data)

        result = summarize(data)["cases"]["ES-TECH"]

        self.assertEqual(data, original)
        self.assertEqual(result["ack_to_first_visible_preview"]["p95_bounds"], [0, 100])
        self.assertEqual(result["stop_command_to_first_observed_revised_paste"]["p95_bounds"], [0, 180])

    def test_global_sequence_allows_touching_intervals_and_maximum_timestamp(self) -> None:
        for intervals in (([100, 200], [0, 200], [0, 200], [0, 100]),
                          ([2**63 - 1, 2**63 - 1],) * 4):
            data = dataset()
            row = successful_trial(1)
            row["events"] = dict(zip(row["events"], intervals))
            data["rows"] = [row]
            with self.subTest(intervals=intervals):
                self.assertIs(validate_dataset(data), data)

    def test_nearest_rank_does_not_interpolate_twenty_samples(self) -> None:
        self.assertEqual(nearest_rank(list(range(1, 21)), 50), 10)
        self.assertEqual(nearest_rank(list(range(1, 21)), 95), 19)

    def test_summary_counts_failures_cancels_and_timeouts_without_hiding_attempts(self) -> None:
        data = dataset()
        data["rows"] = [successful_trial(number) for number in range(1, 21)]
        for number, outcome in enumerate(("failure", "timeout", "cancelled"), start=21):
            data["rows"].append({"trial_id": number, "case_id": "ES-TECH", "outcome": outcome,
                                 "quality": "unassessed", "revised_confirmed": False, "events": {}})
        result = summarize(data)["cases"]["ES-TECH"]
        self.assertEqual((result["attempts"], result["success"], result["failure"],
                          result["timeout"], result["cancelled"]), (23, 20, 1, 1, 1))
        self.assertEqual(result["ack_to_first_visible_preview"]["p50_bounds"], [10, 10.1])
        self.assertEqual(result["ack_to_first_visible_preview"]["p95_bounds"], [19, 19.1])

    def test_small_cold_manual_proxy_and_different_clocks_do_not_claim_percentiles(self) -> None:
        data = dataset()
        data["rows"] = [successful_trial(number) for number in range(1, 21)]
        for key, value in (("temperature", "cold"), ("observation", "manual-unbounded"),
                           ("observation", "events-only"), ("ack_source", "visual-proxy"),
                           ("clock", "video-relative")):
            with self.subTest(key=key, value=value):
                altered = copy.deepcopy(data)
                altered[key] = value
                metric = summarize(altered)["cases"]["ES-TECH"]["ack_to_first_visible_preview"]
                self.assertIsNone(metric["p95_bounds"])
        data["rows"].pop()
        self.assertIsNone(summarize(data)["cases"]["ES-TECH"]["ack_to_first_visible_preview"]["p50_bounds"])

    def test_cases_are_not_pooled_to_reach_twenty(self) -> None:
        data = dataset()
        data["rows"] = [successful_trial(number) for number in range(1, 21)]
        data["rows"][-1]["case_id"] = "EN-TECH"
        result = summarize(data)["cases"]
        self.assertEqual(result["ES-TECH"]["success"], 19)
        self.assertEqual(result["EN-TECH"]["success"], 1)
        self.assertIsNone(result["ES-TECH"]["ack_to_first_visible_preview"]["p95_bounds"])

    def test_success_requires_observed_revision_quality_and_four_endpoints(self) -> None:
        for field, value in (("revised_confirmed", False), ("quality", "fail"), ("events", {})):
            data = dataset()
            row = successful_trial(1)
            row[field] = value
            data["rows"] = [row]
            with self.subTest(field=field), self.assertRaises(ProbeError):
                validate_dataset(data)

    def test_schema_rejects_content_fields_duplicates_and_nonfinite_times(self) -> None:
        data = dataset()
        data["rows"] = [successful_trial(1)]
        data["rows"][0]["transcript"] = "synthetic must never be evidence"
        with self.assertRaises(ProbeError):
            validate_dataset(data)
        data["rows"] = [successful_trial(1), successful_trial(1)]
        with self.assertRaises(ProbeError):
            validate_dataset(data)
        for invalid in ([True, 1], [-1, 0], [0, float("inf")], [5, 4]):
            with self.subTest(value=invalid), self.assertRaises(ProbeError):
                Interval.from_json(invalid)
        self.assertEqual(parse_interval("1.000001:2.5"), [1_000_001, 2_500_000])

    def test_evidence_is_private_and_existing_or_symlink_outputs_are_not_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "run.json"
            write_dataset(output, dataset(), create=True)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertEqual(read_dataset(output), dataset())
            with self.assertRaises(FileExistsError):
                write_dataset(output, dataset(), create=True)
            linked = root / "link.json"
            linked.symlink_to(output)
            with self.assertRaises(OSError):
                write_dataset(linked, dataset())

    def test_editor_is_only_opened_by_explicit_subcommand(self) -> None:
        with patch("tools.physical_probe.open_editor", return_value=0) as editor:
            self.assertEqual(main(["editor", "--case-id", "MIX-TECH"]), 0)
        editor.assert_called_once_with("MIX-TECH")


class CaptureHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="dicta-numeric-fixture-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_ack_observer_does_not_export_other_logs_or_change_logging_on_io_failure(self) -> None:
        original, events = [], []
        observer = AckObserver(original.append, events.append, max_events=2,
                               duration_seconds=1, clock=lambda: 42)
        observer("SYNTHETIC PRIVATE SENTINEL")
        observer("Recording started")
        observer("Recording started")
        observer("Recording started")
        self.assertEqual(len(original), 4)
        self.assertEqual(events, [{"event": "capture_ack", "sequence": 1, "monotonic_ns": 42},
                                  {"event": "capture_ack", "sequence": 2, "monotonic_ns": 42}])
        with patch.object(observer, "emit", side_effect=OSError):
            observer.count = 0
            observer("Recording started")
        self.assertEqual(observer.errors, 1)
        self.assertEqual(original[-1], "Recording started")
        self.assertNotIn("PRIVATE", json.dumps(events))

    def test_ack_collection_deadline_stops_observing_without_stopping_runtime_logs(self) -> None:
        original, events = [], []
        clock = iter((0, 2_000_000_000))
        observer = AckObserver(original.append, events.append, max_events=1,
                               duration_seconds=1, clock=lambda: next(clock))
        observer("Recording started")
        self.assertEqual(original, ["Recording started"])
        self.assertEqual(events, [])

    def test_wrapper_only_runs_explicit_entrypoint_and_restores_import_state(self) -> None:
        native = self.root / "service_log.py"
        native.write_text("def log(message=''):\n    print(message)\n")
        entrypoint = self.root / "main.py"
        entrypoint.write_text("from service_log import log\nlog('Recording started')\nlog('PRIVATE FIXTURE')\n")
        original_module, original_path, original_argv = sys.modules.get("service_log"), sys.path[:], sys.argv
        events = self.root / "acks.jsonl"
        with patch("tools.physical_capture.SERVICE_LOG_SHA256", hashlib.sha256(native.read_bytes()).hexdigest()), redirect_stdout(io.StringIO()):
            wrap(entrypoint, events, native, max_events=2, duration_seconds=1)
        rows = [json.loads(line) for line in events.read_text().splitlines()]
        self.assertEqual([row["event"] for row in rows], ["capture_ack", "collector_end"])
        self.assertNotIn("PRIVATE", events.read_text())
        self.assertIs(sys.modules.get("service_log"), original_module)
        self.assertEqual(sys.path, original_path)
        self.assertIs(sys.argv, original_argv)

    def test_control_records_interval_and_discards_cli_output(self) -> None:
        executable = self.root / "cli"
        executable.write_text("fixture; never executed")
        executable.chmod(0o700)
        with patch("tools.physical_capture.subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as run, patch("tools.physical_capture.time.monotonic_ns", side_effect=(100, 250)):
            result = control(executable, "stop", self.root / "stop.jsonl", 1)
        self.assertEqual(result["interval_ns"], [100, 250])
        self.assertEqual(run.call_args.args[0], [str(executable), "record", "stop"])
        self.assertEqual(run.call_args.kwargs["stdout"], subprocess.DEVNULL)

    def make_frames(self) -> Path:
        directory = self.root / "frames"
        fake_grim = self.root / "grim"
        fake_grim.write_text("fixture; never executed")
        clock = iter(range(0, 2_000_000_000, 10_000_000))
        result = subprocess.CompletedProcess([], 0, b"\x89PNG\r\n\x1a\nsynthetic-image")
        with patch("tools.physical_capture.subprocess.run", return_value=result) as run, patch("tools.physical_capture.time.monotonic_ns", side_effect=lambda: next(clock)):
            capture_frames(directory, {"hud": "1,2 300x100", "target": "4,5 400x200"},
                           seconds=1, fps=1, grim=fake_grim)
        self.assertEqual(len(run.call_args_list), 2)
        self.assertEqual([call.args[0][1:3] for call in run.call_args_list],
                         [["-g", "1,2 300x100"], ["-g", "4,5 400x200"]])
        return directory

    def test_frames_use_two_rois_private_permissions_and_cleanup_only_generated_files(self) -> None:
        directory = self.make_frames()
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        self.assertTrue(all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in directory.iterdir()))
        self.assertEqual(read_manifest(directory)["bytes"], 46)
        unrelated = directory / "keep.txt"
        unrelated.write_text("unrelated")
        with self.assertRaises(CaptureError):
            purge_frames(directory)
        self.assertTrue((directory / "hud-0001.png").exists())
        unrelated.unlink()
        self.assertEqual(purge_frames(directory), {"status": "removed", "frames": 2})
        self.assertFalse(directory.exists())

    def test_observation_interval_includes_capture_duration_and_sampling_gap(self) -> None:
        data = {"frames": [{"region": "hud", "index": 1, "status": "captured", "interval_ns": [100, 120]},
                            {"region": "hud", "index": 2, "status": "captured", "interval_ns": [200, 230]}]}
        self.assertEqual(observation_bounds(data, "hud", 1, 2), [100, 230])
        data["frames"][1]["status"] = "failed"
        with self.assertRaises(CaptureError):
            observation_bounds(data, "hud", 1, 2)

    def test_screen_capture_requires_explicit_bounded_geometry(self) -> None:
        import argparse

        self.assertEqual(parse_region("-1920,0 600x200"), "-1920,0 600x200")
        for value in ("", "0,0 0x100", "0,0 4096x4096", "all", "0,0 100x100; command"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                parse_region(value)


if __name__ == "__main__":
    unittest.main()
