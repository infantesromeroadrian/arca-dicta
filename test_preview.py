"""Offline behavioral checks using synthetic PCM and anonymous pipes only."""

from __future__ import annotations

import io
import os
import queue
import struct
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

import main as launcher
from preview import (
    AudioSnapshot,
    LocalDecoder,
    PreviewController,
    PreviewResult,
    capture_tail,
    worker_environment,
)
from protocol import (
    MAX_JSON_BYTES,
    PreviewFailure,
    decode_json,
    read_frame,
    validate_request,
    write_json,
)


class ControlledDecoder:
    def __init__(self) -> None:
        self.calls = queue.Queue()
        self.closed = threading.Event()
        self.pending = []

    def start(self) -> None:
        pass

    def transcribe(self, snapshot, language):
        call = SimpleNamespace(
            snapshot=snapshot,
            language=language,
            release=threading.Event(),
            text="texto sintético",
            failure=False,
        )
        self.pending.append(call)
        self.calls.put(call)
        if not call.release.wait(3):
            raise AssertionError("test did not release inference")
        if call.failure:
            raise PreviewFailure("synthetic-worker-failure")
        return PreviewResult(call.text, 20)

    def close(self) -> None:
        self.closed.set()
        for call in self.pending:
            call.release.set()


class PreviewLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.decoder = ControlledDecoder()
        self.active = None
        self.outputs = queue.Queue()
        self.logs = queue.Queue()
        self.statuses = queue.Queue()
        self.clears = []
        self.controller = PreviewController(
            self.decoder,
            snapshot=lambda token: AudioSnapshot(
                np.ones(16000, dtype=np.float32), 16000
            ),
            is_active=lambda token: token is self.active,
            publish=self.outputs.put,
            clear=lambda: self.clears.append(True),
            log=self.logs.put,
            status=self.statuses.put,
            first_delay=0,
            interval=0.01,
        )

    def tearDown(self) -> None:
        self.controller.close()
        self.controller._thread.join(1)
        self.assertFalse(self.controller._thread.is_alive())

    def begin(self, language=None):
        self.active = object()
        self.controller.begin(self.active, language, time.monotonic())
        return self.active

    def test_cancellation_clears_immediately_and_discards_late_text(self) -> None:
        token = self.begin("es")
        call = self.decoder.calls.get(timeout=1)
        started = time.monotonic()
        self.controller.invalidate(token)
        self.active = None
        self.assertLess(time.monotonic() - started, 0.1)
        self.assertTrue(self.clears)
        call.release.set()
        self.controller.close()
        self.controller._thread.join(1)
        self.assertTrue(self.outputs.empty())

    def test_fast_restart_discards_old_session_and_keeps_language(self) -> None:
        first = self.begin("es")
        old = self.decoder.calls.get(timeout=1)
        self.controller.invalidate(first)
        second = self.begin(None)
        self.assertTrue(self.decoder.calls.empty())
        old.text = "old session must never display"
        old.release.set()
        new = self.decoder.calls.get(timeout=1)
        self.assertTrue(self.outputs.empty())
        self.assertIsNone(new.language)
        self.controller.invalidate(first)
        new.text = "nueva sesión"
        new.release.set()
        self.assertEqual(self.outputs.get(timeout=1), "nueva sesión")
        self.controller.invalidate(second)

    def test_publish_and_clear_are_serialized(self) -> None:
        entered, release = threading.Event(), threading.Event()
        output = []

        def publish(text):
            entered.set()
            release.wait(1)
            output.append(text)

        self.controller._publish = publish
        self.controller._clear = lambda: output.append("cleared")
        token = self.begin()
        call = self.decoder.calls.get(timeout=1)
        call.release.set()
        self.assertTrue(entered.wait(1))
        stopper = threading.Thread(target=self.controller.invalidate, args=(token,))
        stopper.start()
        release.set()
        stopper.join(1)
        self.assertFalse(stopper.is_alive())
        self.assertEqual(output, ["texto sintético", "cleared"])

    def test_many_starts_never_queue_inferences(self) -> None:
        self.begin()
        old = self.decoder.calls.get(timeout=1)
        for _ in range(20):
            self.begin("es")
        self.assertTrue(self.decoder.calls.empty())
        old.release.set()
        new = self.decoder.calls.get(timeout=1)
        self.assertEqual(new.language, "es")
        self.assertTrue(self.decoder.calls.empty())
        self.assertTrue(self.outputs.empty())

    def test_worker_failure_disables_only_preview_and_clears(self) -> None:
        token = self.begin()
        call = self.decoder.calls.get(timeout=1)
        call.failure = True
        call.release.set()
        self.assertTrue(self.decoder.closed.wait(1))
        self.assertIs(self.active, token)
        self.assertTrue(self.outputs.empty())
        self.assertTrue(self.clears)
        self.begin()
        self.assertTrue(self.decoder.calls.empty())
        logs = []
        while not self.logs.empty():
            logs.append(self.logs.get_nowait())
        self.assertTrue(any("disabled reason=worker-failure" in item for item in logs))
        self.assertFalse(any("texto sintético" in item for item in logs))

    def test_duplicate_begin_does_not_change_current_language(self) -> None:
        token = self.begin("es")
        call = self.decoder.calls.get(timeout=1)
        self.controller.begin(token, None, time.monotonic())
        self.assertEqual(call.language, "es")
        call.release.set()
        self.assertEqual(self.outputs.get(timeout=1), "texto sintético")

    def test_status_follows_real_capture_and_inference(self) -> None:
        self.begin()
        call = self.decoder.calls.get(timeout=1)
        self.assertEqual(self.statuses.get(timeout=1), "listening")
        self.assertEqual(self.statuses.get(timeout=1), "decoding")
        call.release.set()
        self.outputs.get(timeout=1)
        self.assertEqual(self.statuses.get(timeout=1), "listening")

    def test_stopped_inference_cannot_overwrite_processing_status(self) -> None:
        token = self.begin()
        call = self.decoder.calls.get(timeout=1)
        self.statuses.get(timeout=1)
        self.statuses.get(timeout=1)
        self.controller.invalidate(token)
        self.active = None
        call.release.set()
        self.controller.close()
        self.controller._thread.join(1)
        self.assertTrue(self.statuses.empty())

    def test_old_generation_does_not_signal_new_session(self) -> None:
        self.begin()
        old = self.decoder.calls.get(timeout=1)
        self.statuses.get(timeout=1)
        self.statuses.get(timeout=1)
        self.begin()
        self.assertEqual(self.statuses.get(timeout=1), "listening")
        old.release.set()
        self.decoder.calls.get(timeout=1)
        self.assertEqual(self.statuses.get(timeout=1), "decoding")
        self.assertTrue(self.statuses.empty())

    def test_unavailable_is_truthful_for_current_and_future_sessions(self) -> None:
        self.begin()
        call = self.decoder.calls.get(timeout=1)
        self.statuses.get(timeout=1)
        self.statuses.get(timeout=1)
        call.failure = True
        call.release.set()
        self.assertTrue(self.decoder.closed.wait(1))
        self.assertEqual(self.statuses.get(timeout=1), "preview_unavailable")
        self.begin()
        self.assertEqual(self.statuses.get(timeout=1), "preview_unavailable")
        self.assertTrue(self.decoder.calls.empty())

    def test_status_callback_failure_does_not_disable_preview(self) -> None:
        def broken_status(state):
            raise RuntimeError("synthetic status failure")

        self.controller._status = broken_status
        self.begin()
        call = self.decoder.calls.get(timeout=1)
        call.release.set()
        self.assertEqual(self.outputs.get(timeout=1), "texto sintético")
        self.assertFalse(self.controller._disabled)


class CaptureTests(unittest.TestCase):
    def make_app(self):
        token = object()
        capture = SimpleNamespace(
            lock=threading.Lock(),
            is_recording=True,
            sample_rate=16000,
            audio_data=[
                np.full(1600, number, dtype=np.float32) for number in range(120)
            ],
        )
        return SimpleNamespace(
            audio_capture=capture, is_recording=True, _recording_session=token
        )

    def test_snapshot_is_bounded_tail_and_never_consumes_final_audio(self) -> None:
        app = self.make_app()
        original = tuple(app.audio_capture.audio_data)
        concatenate = np.concatenate

        def outside_lock(chunks):
            self.assertFalse(app.audio_capture.lock.locked())
            return concatenate(chunks)

        with patch("preview.np.concatenate", side_effect=outside_lock):
            result = capture_tail(app, app._recording_session)
        self.assertEqual(len(result.audio), 8 * 16000)
        self.assertEqual(result.audio[0], 40)
        self.assertEqual(result.audio[-1], 119)
        self.assertEqual(len(app.audio_capture.audio_data), len(original))
        self.assertTrue(
            all(a is b for a, b in zip(app.audio_capture.audio_data, original))
        )
        app.audio_capture.audio_data.clear()
        self.assertEqual(result.audio[-1], 119)

    def test_stale_or_stopped_capture_is_not_copied(self) -> None:
        app = self.make_app()
        self.assertIsNone(capture_tail(app, object()))
        app.is_recording = False
        self.assertIsNone(capture_tail(app, app._recording_session))

    def test_invalid_audio_is_rejected(self) -> None:
        app = self.make_app()
        app.audio_capture.audio_data[-1][0] = np.nan
        with self.assertRaises(PreviewFailure):
            capture_tail(app, app._recording_session)


class ProtocolTests(unittest.TestCase):
    def test_oversized_or_truncated_frames_are_rejected(self) -> None:
        with self.assertRaises(PreviewFailure):
            read_frame(
                io.BytesIO(struct.pack("!I", MAX_JSON_BYTES + 1)), MAX_JSON_BYTES
            )
        with self.assertRaises(EOFError):
            read_frame(io.BytesIO(struct.pack("!I", 100) + b"{}"), MAX_JSON_BYTES)
        for payload in (b"[]", b"not json", b"null"):
            with self.subTest(payload=payload), self.assertRaises(PreviewFailure):
                decode_json(payload)

    def test_request_bounds_and_language_are_validated_before_audio(self) -> None:
        valid = {
            "request": 1,
            "samples": 128000,
            "sample_rate": 16000,
            "language": "es",
        }
        self.assertEqual(validate_request(valid), (1, 128000, 16000, "es"))
        for change in (
            {"samples": 128001},
            {"samples": -1},
            {"sample_rate": 1000000},
            {"language": "es; secret"},
            {"request": True},
            {"extra": 1},
        ):
            with self.subTest(change=change), self.assertRaises(PreviewFailure):
                validate_request(valid | change)

    def test_worker_does_not_inherit_credentials_or_network_configuration(self) -> None:
        with patch.dict(
            os.environ,
            {
                "GROQ_API_KEY": "synthetic",
                "HTTPS_PROXY": "synthetic",
                "HF_TOKEN": "synthetic",
            },
        ):
            environment = worker_environment()
        self.assertFalse(
            {"GROQ_API_KEY", "HTTPS_PROXY", "HF_TOKEN", "PYTHONPATH"}
            & environment.keys()
        )
        self.assertEqual(environment["HF_HUB_OFFLINE"], "1")

    def test_wrong_request_or_malformed_result_is_rejected_over_real_pipes(
        self,
    ) -> None:
        for result in (
            {"kind": "result", "request": 2, "text": "x", "decode_ms": 1},
            {"kind": "result", "request": 1, "text": ["x"], "decode_ms": 1},
            {"kind": "error", "code": "decode-failed"},
        ):
            with self.subTest(result=result):
                request_read, request_write = os.pipe()
                response_read, response_write = os.pipe()
                streams = [
                    os.fdopen(fd, mode, buffering=0)
                    for fd, mode in (
                        (request_read, "rb"),
                        (request_write, "wb"),
                        (response_read, "rb"),
                        (response_write, "wb"),
                    )
                ]
                decoder = LocalDecoder(Path("unused"), Path("unused"), Path("unused"))
                decoder._process = SimpleNamespace(stdin=streams[1], stdout=streams[2])
                os.set_blocking(request_write, False)
                os.set_blocking(response_read, False)

                def peer():
                    read_frame(streams[0], MAX_JSON_BYTES)
                    read_frame(streams[0], 100000)
                    write_json(streams[3], result)

                thread = threading.Thread(target=peer)
                thread.start()
                try:
                    with self.assertRaises(PreviewFailure):
                        decoder.transcribe(
                            AudioSnapshot(np.zeros(16000, dtype=np.float32), 16000),
                            None,
                        )
                finally:
                    thread.join(1)
                    for stream in streams:
                        stream.close()


class ExtensionTests(unittest.TestCase):
    def test_hash_drift_disables_extension(self) -> None:
        with patch.object(Path, "read_bytes", return_value=b"changed upstream"):
            self.assertFalse(launcher.upstream_supported())

    def test_original_stop_and_cancel_keep_ownership_of_final_actions(self) -> None:
        controller = SimpleNamespace(
            begin=lambda *args: calls.append(("begin", args[1])),
            invalidate=lambda *args: calls.append(("clear",)),
            close=lambda: None,
        )
        calls = []

        class Base:
            def __init__(self):
                self.config = SimpleNamespace(
                    get_setting=lambda key, default=None: {
                        "transcription_backend": "rest-api",
                        "recording_mode": "auto",
                        "language": None,
                    }.get(key, default)
                )
                self._mic_osd_runner = object()
                self._recording_lock = threading.Lock()
                self._recording_session = None
                self._ptt_press_time = None
                self.is_recording = False

            def _start_recording(self, language_override=None):
                self._recording_session = object()
                self._ptt_press_time = time.monotonic()
                self.is_recording = True

            def _stop_recording(self, expected_session=None):
                calls.append(("original-final",))
                self.is_recording = False

            def _cancel_recording(self):
                calls.append(("original-cancel",))
                self.is_recording = False

        app = launcher.make_preview_app(Base, lambda app: controller)()
        app._start_recording("es")
        self.assertEqual(calls, [("begin", "es")])
        app._stop_recording()
        self.assertEqual(calls[-2:], [("clear",), ("original-final",)])
        app._start_recording()
        app._cancel_recording()
        self.assertEqual(
            calls[-3:], [("begin", None), ("clear",), ("original-cancel",)]
        )


if __name__ == "__main__":
    unittest.main()
