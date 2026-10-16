"""Strict review checks use synthetic text and fake desktop/provider boundaries."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
import urllib.error
from unittest.mock import patch

import review_gate as gate
import review
from dicta_config import upstream_library

sys.path.insert(0, str(upstream_library() / "src"))
import text_injector as native
from app.shortcuts import ShortcutsMixin
from app.recording import RecordingMixin
from app.control import ControlMixin
from recording_control_server import RecordingControlServer
sys.path.pop(0)

ORIGINAL = "eh revisa revisa el RAG pipeline with Docker no cambies request_id"
REVISED = "Revisa el RAG pipeline with Docker; no cambies request_id."


def config(**changes):
    values = {"post_transcription_hook": shlex.join(["exec", str(gate.HOOK)]),
              "transcription_backend": "rest-api", "recording_mode": "toggle",
              "append_trailing_space": False, **changes}
    return SimpleNamespace(get_setting=lambda key, default=None: values.get(key, default))


def make_injector():
    cls = gate.make_review_injector(native.TextInjector, native.InjectionOutcome)
    injector = object.__new__(cls)
    injector.config_manager = config()
    injector._review_lock = threading.RLock()
    injector._review_generation = 0
    injector._pending_review = None
    injector._review_active = False
    injector._delivery_lock = threading.RLock()
    injector._last_text_lock = threading.Lock()
    injector._last_text = None
    injector._stream_lock = threading.Lock()
    injector._stream = None
    injector._preprocess_text = lambda text: text
    injector.review_feedback = lambda *args: None
    injected, copied = [], []
    def deliver(text, auto_submit=True, retain=False):
        injected.append((text, auto_submit, retain))
        if retain:
            injector._last_text = text
        return True
    injector._inject_via_clipboard_and_hotkey = deliver
    injector._copy_text_to_clipboard = lambda text: copied.append(text) or True
    return injector, injected, copied


class TransportTests(unittest.TestCase):
    def test_provider_failures_keep_reason_and_draft_until_one_successful_retry(self):
        def envelope(text=REVISED, **changes):
            return json.dumps({"done": True, "done_reason": "stop",
                               "message": {"role": "assistant", "content": json.dumps({"text": text})},
                               **changes}).encode()

        failures = (
            (review.DeadlineExpired(), "timeout"),
            (urllib.error.HTTPError(review.ENDPOINT, 401, "private-sentinel", {}, None), "auth-error"),
            (urllib.error.HTTPError(review.ENDPOINT, 429, "private-sentinel", {}, None), "rate-limit"),
            (urllib.error.HTTPError(review.ENDPOINT, 500, "private-sentinel", {}, None), "provider-error"),
            (urllib.error.URLError("private-sentinel"), "provider-error"),
            (urllib.error.URLError(TimeoutError("private-sentinel")), "timeout"),
            (b"invalid-json-private-sentinel", "invalid-response"),
            (envelope(done_reason="length"), "invalid-response"),
            (envelope("Respuesta ajena al dictado."), "fidelity-rejected"),
            (RuntimeError("private-sentinel"), "internal-error"),
        )
        for failure, expected_reason in failures:
            with self.subTest(reason=expected_reason, kind=type(failure).__name__):
                injector, injected, copied = make_injector()
                notices = []
                injector.review_feedback = lambda *args: notices.append(args)
                output = io.StringIO()

                def run_hook_process(*args, input, **kwargs):
                    destination = io.BytesIO()
                    code = review.run_hook(io.BytesIO(input), destination, {"OLLAMA_API_KEY": "synthetic"})
                    return SimpleNamespace(returncode=code, stdout=destination.getvalue())

                def open_response(*args, **kwargs):
                    if isinstance(failure, BaseException):
                        raise failure
                    return io.BytesIO(failure)

                opener = SimpleNamespace(open=open_response)
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output), \
                        patch.object(gate.subprocess, "run", side_effect=run_hook_process), \
                        patch.object(review.urllib.request, "build_opener", return_value=opener):
                    self.assertEqual(injector.inject_text(ORIGINAL), native.InjectionOutcome.FAILED)
                    self.assertEqual(notices[-1][0:2], ("review_failed", expected_reason))
                    self.assertEqual((injected, copied, injector._pending_review), ([], [], ORIGINAL))
                    opener.open = lambda *args, **kwargs: io.BytesIO(envelope())
                    self.assertEqual(injector.retry_pending_review(), native.InjectionOutcome.INJECTED)
                    self.assertEqual(injector.retry_pending_review(), native.InjectionOutcome.FAILED)
                self.assertEqual(injected, [(REVISED, False, True)])
                self.assertEqual(copied, [])
                self.assertEqual(output.getvalue(), "")

    def test_failures_never_deliver_or_write_clipboard(self):
        failures = [
            OSError("synthetic failure"),
            subprocess.TimeoutExpired("synthetic", 5, output=b"Revisa el RAG"),
            SimpleNamespace(returncode=1, stdout=REVISED.encode()),
            SimpleNamespace(returncode=77, stdout=REVISED.encode()),
            SimpleNamespace(returncode=0, stdout=b""),
            SimpleNamespace(returncode=0, stdout=b"\xff"),
            SimpleNamespace(returncode=0, stdout=b"Approved."),
            SimpleNamespace(returncode=0, stdout=b"a" * (gate.MAX_OUTPUT_BYTES + 1)),
        ]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                injector, injected, copied = make_injector()
                kwargs = {"side_effect": failure} if isinstance(failure, Exception) else {"return_value": failure}
                with patch.object(gate.subprocess, "run", **kwargs):
                    self.assertEqual(injector.inject_text(ORIGINAL), native.InjectionOutcome.FAILED)
                self.assertEqual((injected, copied, injector._last_text), ([], [], None))
                self.assertTrue(injector.review_status()[0])

    def test_real_process_timeout_kills_partial_output_before_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "hook"
            script.write_text('#!/usr/bin/python3\nimport sys,time\nsys.stdout.write("Revisa el RAG")\nsys.stdout.flush()\ntime.sleep(30)\n')
            script.chmod(0o700)
            with patch.object(gate, "HOOK", script):
                injector, injected, copied = make_injector()
                self.assertEqual(injector.inject_text(ORIGINAL), native.InjectionOutcome.FAILED)
                self.assertEqual((injected, copied), ([], []))
                self.assertTrue(injector.review_status()[0])

    def test_identical_success_is_delivered_once_without_enter(self):
        injector, injected, copied = make_injector()
        with patch.object(gate.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=ORIGINAL.encode())) as run:
            self.assertEqual(injector.inject_text(ORIGINAL), native.InjectionOutcome.INJECTED)
        self.assertEqual(injected, [(ORIGINAL, False, True)])
        self.assertEqual(copied, [])
        self.assertEqual(run.call_args.kwargs["timeout"], 5.0)
        self.assertEqual(run.call_args.args[0], [str(gate.HOOK)])

    def test_missing_changed_hook_and_oversized_input_are_fail_closed(self):
        for settings, text in (({"post_transcription_hook": None}, ORIGINAL),
                               ({"post_transcription_hook": "exec /different/hook"}, ORIGINAL),
                               ({}, "a" * (gate.MAX_INPUT_BYTES + 1))):
            injector, injected, copied = make_injector()
            injector.config_manager = config(**settings)
            with patch.object(gate.subprocess, "run") as run:
                self.assertEqual(injector.inject_text(text), native.InjectionOutcome.FAILED)
                run.assert_not_called()
            self.assertEqual((injected, copied), ([], []))


class PendingTests(unittest.TestCase):
    def test_retry_failure_keeps_draft_then_success_delivers_once(self):
        injector, injected, copied = make_injector()
        with patch.object(gate, "run_review", side_effect=[gate.ReviewFailure("timeout"), gate.ReviewFailure("timeout"), REVISED]) as review:
            self.assertEqual(injector.inject_text(ORIGINAL), native.InjectionOutcome.FAILED)
            self.assertEqual(injector.retry_pending_review(), native.InjectionOutcome.FAILED)
            self.assertTrue(injector.review_status()[0])
            self.assertEqual(injector.retry_pending_review(), native.InjectionOutcome.INJECTED)
            self.assertEqual(injector.retry_pending_review(), native.InjectionOutcome.FAILED)
        self.assertEqual(review.call_count, 3)
        self.assertEqual((injected, copied), ([(REVISED, False, True)], []))

    def test_new_dictation_does_not_overwrite_pending(self):
        injector, injected, copied = make_injector()
        with patch.object(gate, "run_review", side_effect=gate.ReviewFailure("timeout")):
            injector.inject_text(ORIGINAL)
        with patch.object(gate, "run_review") as review:
            self.assertEqual(injector.inject_text("new dictation"), native.InjectionOutcome.FAILED)
            review.assert_not_called()
        self.assertEqual(injector._pending_review, ORIGINAL)

    def test_copy_and_paste_recovery_require_pending_review(self):
        for action in ("copy_last", "paste_last"):
            injector, injected, copied = make_injector()
            injector._last_text = "Previously reviewed text."
            with patch.object(gate, "run_review", side_effect=gate.ReviewFailure("timeout")):
                injector.inject_text(ORIGINAL)
                self.assertFalse(injector.recover_last(action)[0])
            self.assertEqual((injected, copied), ([], []))
            with patch.object(gate, "run_review", return_value=REVISED):
                self.assertTrue(injector.recover_last(action)[0])
            self.assertEqual(len(injected) + len(copied), 1)
            self.assertEqual(injector._last_text, REVISED)

    def test_clear_cancels_inflight_and_prevents_late_delivery(self):
        injector, injected, copied = make_injector()
        entered, release = threading.Event(), threading.Event()
        def review(*args):
            entered.set()
            self.assertTrue(release.wait(2))
            return REVISED
        with patch.object(gate, "run_review", side_effect=review):
            thread = threading.Thread(target=injector.inject_text, args=(ORIGINAL,))
            thread.start()
            self.assertTrue(entered.wait(1))
            self.assertTrue(injector.recover_last("clear_last")[0])
            release.set()
            thread.join(2)
            self.assertFalse(thread.is_alive())
        self.assertEqual((injected, copied, injector._last_text, injector._pending_review), ([], [], None, None))

    def test_delivery_failure_keeps_only_reviewed_recovery(self):
        injector, injected, copied = make_injector()
        injector._inject_via_clipboard_and_hotkey = lambda *args, **kwargs: False
        with patch.object(gate, "run_review", return_value=REVISED):
            self.assertEqual(injector.inject_text(ORIGINAL), native.InjectionOutcome.FAILED)
        self.assertFalse(injector.review_status()[0])
        with patch.object(gate, "run_review", side_effect=AssertionError("already reviewed")):
            self.assertTrue(injector.recover_last("copy_last")[0])
        self.assertEqual(copied, [REVISED])


class AppTests(unittest.TestCase):
    def test_transient_diagnostics_remain_retryable_and_logs_are_fixed_codes(self):
        for reason in ("timeout", "rate-limit", "provider-error", "invalid-response",
                       "fidelity-rejected", "internal-error", "private-sentinel"):
            with self.subTest(reason=reason), patch.dict(os.environ, {"HYPRWHSPR_LOCAL_HUD": "1"}):
                app, _, _ = self.make_app()
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    app._review_feedback("review_failed", reason, 1)
                self.assertEqual(app._review_ui_state, "review_failed")
                messages = [event[1] for event in app.events if event[0] == "notify"]
                self.assertIn("Super+Espacio", messages[-1])
                self.assertNotIn("private-sentinel", messages[-1] + output.getvalue())
                expected = "internal-error" if reason == "private-sentinel" else reason
                self.assertEqual(output.getvalue(), f"[REVIEW] state=review_failed reason={expected}\n")

    def make_app(self):
        injector, injected, copied = make_injector()
        class Base:
            def __init__(self):
                self.text_injector = injector
                self.config = injector.config_manager
                self._cancel_pending_hide_lock = threading.Lock()
                self._cancel_pending_hide = False
                self.events = []
                self._mic_osd_runner = SimpleNamespace(clear_preview_text=lambda: None,
                    set_state=lambda state: self.events.append(("state", state)), show=lambda: self.events.append(("show",)))
                self._recording_control_server = SimpleNamespace(has_capture_subscriber=lambda: False)
            def _notify_user(self, title, message, **kwargs): self.events.append(("notify", message))
            def _start_recording(self, language_override=None):
                self.is_recording = True
                self.events.append(("capture",))
            def _hide_mic_osd(self): self.events.append(("hide",))
            def _set_visualizer_state(self, state): self.events.append(("state", state))
            def _release_blocked_capture(self): pass
            def _mark_delivery_healthy(self): pass
            def _handle_recovery(self, action): return self.text_injector.recover_last(action)
            def _handle_control_command(self, *args): pass
            def _cancel_recording(self): pass
            def _cleanup(self): pass
        return gate.make_review_app(Base, native.InjectionOutcome)(), injected, copied

    def test_held_feedback_cannot_be_hidden_or_marked_success(self):
        app, injected, copied = self.make_app()
        with patch.dict(os.environ, {"HYPRWHSPR_LOCAL_HUD": "1"}):
            with patch.object(gate, "run_review", side_effect=gate.ReviewFailure("timeout")):
                app._inject_text(ORIGINAL)
            app._show_result_and_hide(True)
            app._set_visualizer_state("success")
            app._hide_mic_osd()
        self.assertIn(("state", "review_failed"), app.events)
        self.assertNotIn(("state", "success"), app.events)
        self.assertNotIn(("hide",), app.events)
        self.assertIn(("notify", gate.REASONS["timeout"]), app.events)

    def test_start_retries_instead_of_capturing_and_next_start_can_record(self):
        app, injected, copied = self.make_app()
        with patch.object(gate, "run_review", side_effect=gate.ReviewFailure("timeout")):
            app._inject_text(ORIGINAL)
        with patch.object(gate, "run_review", return_value=REVISED):
            app._start_recording()
            thread = app._review_retry_thread
            if thread is not None:
                thread.join(2)
        self.assertNotIn(("capture",), app.events)
        self.assertEqual(injected, [(REVISED, False, True)])
        app._start_recording()
        self.assertIn(("capture",), app.events)

    def test_delayed_hide_cannot_hide_new_held_state(self):
        app, _, _ = self.make_app()
        callbacks = []
        def thread(*, target, daemon):
            return SimpleNamespace(start=lambda: callbacks.append(target))
        with patch.object(gate.threading, "Thread", side_effect=thread):
            app._show_result_and_hide(True)
        app._review_feedback("review_failed", "timeout", 1)
        with patch.object(gate.time, "sleep", return_value=None):
            callbacks[0]()
        self.assertNotIn(("hide",), app.events)

    def test_config_drift_blocks_new_capture(self):
        app, _, _ = self.make_app()
        app.config = config(post_transcription_hook=None)
        app._start_recording()
        self.assertNotIn(("capture",), app.events)
        self.assertTrue(any(event[0] == "notify" for event in app.events))

    def test_contract_drift_prevents_native_startup(self):
        import main as launcher
        with patch.object(launcher.sys, "argv", ["main.py"]), patch.object(launcher, "upstream_supported", return_value=False):
            with patch.object(launcher.importlib.util, "spec_from_file_location") as load:
                with self.assertRaises(SystemExit):
                    launcher.main()
                load.assert_not_called()

    def test_cancel_shortcut_invalidates_review_when_recording_is_false(self):
        app, injected, copied = self.make_app()
        app.is_recording = False
        entered, release = threading.Event(), threading.Event()
        def review(*args):
            entered.set()
            release.wait(2)
            return REVISED
        with patch.object(gate, "run_review", side_effect=review):
            thread = threading.Thread(target=app._inject_text, args=(ORIGINAL,))
            thread.start()
            self.assertTrue(entered.wait(1))
            ShortcutsMixin._on_cancel_shortcut_triggered(app)
            release.set()
            thread.join(2)
            self.assertFalse(thread.is_alive())
        self.assertEqual((injected, copied, app.text_injector._pending_review), ([], [], None))
        self.assertEqual(app._review_ui_state, "cleared")

    def test_native_fifo_cancel_remains_responsive_during_retry(self):
        app, injected, copied = self.make_app()
        app.is_recording = False
        type(app).__mro__[1]._handle_control_command = ControlMixin._handle_control_command
        app._autostop_start_silence_monitor = lambda: None
        with patch.object(gate, "run_review", side_effect=gate.ReviewFailure("timeout")):
            app._inject_text(ORIGINAL)
        entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()
        def review(*args):
            entered.set()
            release.wait(2)
            return REVISED
        def command(action, language=None):
            app._handle_control_command(action, language)
            if action == "cancel":
                cancelled.set()
        with tempfile.TemporaryDirectory() as directory:
            fifo = Path(directory) / "control"
            os.mkfifo(fifo)
            # Keep one writer open so the fixture does not test native FIFO reopen races.
            keep_open = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)
            server = RecordingControlServer(fifo, Path(directory) / "unused", command, lambda: False)
            stop = threading.Event()
            server._stop_event = stop
            listener = threading.Thread(target=server._fifo_listener, args=(stop,))
            listener.start()
            try:
                with patch.object(gate, "run_review", side_effect=review):
                    self.assertTrue(server._write_fifo("start\n"))
                    self.assertTrue(entered.wait(1))
                    self.assertTrue(server._write_fifo("cancel\n"))
                    self.assertTrue(cancelled.wait(1), "FIFO cancel was blocked behind review")
                    release.set()
                    retry = app._review_retry_thread
                    if retry is not None:
                        retry.join(2)
            finally:
                release.set()
                stop.set()
                server._wake_fifo()
                listener.join(2)
                os.close(keep_open)
        self.assertEqual((injected, copied), ([], []))
        self.assertFalse(app.text_injector.review_status()[0])

    def test_native_fifo_stop_stt_cancel_blocks_delivery_and_new_capture(self):
        for mode, stop_action in (("auto", "stop"), ("auto", "release"),
                                  ("toggle", "stop"), ("toggle", "release")):
            with self.subTest(mode=mode, stop_action=stop_action):
                app, injected, copied = self.make_app()
                app.config = app.text_injector.config_manager = config(recording_mode=mode)
                base = type(app).__mro__[1]
                base._handle_control_command = ControlMixin._handle_control_command
                base._stop_recording = RecordingMixin._stop_recording
                base._process_audio = RecordingMixin._process_audio
                base._cancel_recording = RecordingMixin._cancel_recording
                app._recording_lock = threading.Lock()
                app._recording_session = object()
                app._recording_finalizing = threading.Event()
                app.is_recording, app.is_processing = True, False
                app._current_language_override = None
                stopped, restored = [], []
                app.audio_capture = SimpleNamespace(sample_rate=16000,
                    stop_recording=lambda: stopped.append(1) or object())
                app.audio_manager = SimpleNamespace(play_error_sound=lambda: None, play_stop_sound=lambda: None)
                app.config.get_hallucination_markers = lambda: []
                app._finish_live_typing = lambda text: None
                app._notify_capture = lambda *args, **kwargs: None
                app._clear_mic_osd_preview_text = lambda: None
                app._ptt_reset = lambda: None
                app._autostop_stop_silence_monitor = lambda: None
                app._autostop_start_silence_monitor = lambda: None
                app._stop_audio_level_monitoring = lambda: None
                app._write_recording_status = lambda value: None
                app._restore_recording_playback = lambda token: restored.append(token)
                app._wait_for_start_settled = lambda: None
                app._save_debug_recording = lambda audio: None
                app._is_zero_volume = lambda audio: False
                app._cancel_live_typing = lambda: None
                entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()
                processed = threading.Condition()
                commands = []
                def transcribe(*args, **kwargs):
                    entered.set()
                    release.wait(3)
                    return ORIGINAL
                app.whisper_manager = SimpleNamespace(transcribe_audio=transcribe)
                def command(action, language=None):
                    app._handle_control_command(action, language)
                    with processed:
                        commands.append(action)
                        processed.notify_all()
                    if action == "cancel":
                        cancelled.set()
                with tempfile.TemporaryDirectory() as directory:
                    fifo = Path(directory) / "control"
                    os.mkfifo(fifo)
                    keep_open = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)
                    server = RecordingControlServer(fifo, Path(directory) / "unused", command, lambda: app.is_recording)
                    stop = threading.Event()
                    server._stop_event = stop
                    listener = threading.Thread(target=server._fifo_listener, args=(stop,))
                    listener.start()
                    try:
                        with patch.object(gate, "run_review", return_value=REVISED) as review:
                            self.assertTrue(server._write_fifo(stop_action + "\n"))
                            self.assertTrue(entered.wait(1))
                            for action in ("start", "stop", "release", "cancel"):
                                with processed:
                                    previous = len(commands)
                                    self.assertTrue(server._write_fifo(action + "\n"))
                                    self.assertTrue(processed.wait_for(lambda: len(commands) > previous, 1), "FIFO blocked during native STT")
                            self.assertTrue(cancelled.is_set())
                            release.set()
                            worker = app._review_finalize_thread
                            if worker is not None:
                                worker.join(2)
                            review.assert_not_called()
                    finally:
                        release.set()
                        stop.set()
                        server._wake_fifo()
                        listener.join(2)
                        os.close(keep_open)
                self.assertEqual((injected, copied, stopped), ([], [], [1]))
                self.assertEqual(restored, [app._recording_session])
                self.assertNotIn(("capture",), app.events)
                self.assertNotIn(("state", "success"), app.events)
                self.assertFalse(app._recording_finalizing.is_set())
                self.assertFalse(app.is_processing)

    def test_ptt_latched_release_followed_by_stop_keeps_native_dispatch(self):
        app, _, _ = self.make_app()
        type(app).__mro__[1]._handle_control_command = ControlMixin._handle_control_command
        app.config = config(recording_mode="push_to_talk")
        app.is_recording = True
        entered, release, controls_done = threading.Event(), threading.Event(), threading.Event()
        stopped = []
        def latch():
            entered.set()
            release.wait(2)
            return True
        def stop():
            stopped.append(1)
            app.is_recording = False
        app._ptt_release_latches = latch
        app._stop_recording = stop
        def controls():
            app._handle_control_command("release")
            app._handle_control_command("stop")
            controls_done.set()
        thread = threading.Thread(target=controls)
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            # V3 dispatched and dropped stop while its release worker was latched.
            controls_done.wait(0.1)
        finally:
            release.set()
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertFalse(app.is_recording)
        self.assertEqual(stopped, [1])

    def test_native_stt_cancel_and_shutdown_block_late_result(self):
        for action in ("cancel", "cleanup"):
            with self.subTest(action=action):
                app, injected, copied = self.make_app()
                type(app).__mro__[1]._process_audio = RecordingMixin._process_audio
                app._recording_lock = threading.Lock()
                app.is_recording = app.is_processing = False
                app._current_language_override = None
                app.audio_capture = SimpleNamespace(sample_rate=16000)
                app.audio_manager = SimpleNamespace(play_error_sound=lambda: None)
                app.config.get_hallucination_markers = lambda: []
                app._finish_live_typing = lambda text: None
                app._notify_capture = lambda *args, **kwargs: None
                app._clear_mic_osd_preview_text = lambda: None
                entered, release = threading.Event(), threading.Event()
                def transcribe(*args, **kwargs):
                    entered.set()
                    release.wait(2)
                    return ORIGINAL
                app.whisper_manager = SimpleNamespace(transcribe_audio=transcribe)
                with patch.object(gate, "run_review", return_value=REVISED) as review:
                    process = threading.Thread(target=app._process_audio, args=(object(),))
                    process.start()
                    self.assertTrue(entered.wait(1))
                    if action == "cancel":
                        ShortcutsMixin._on_cancel_shortcut_triggered(app)
                    else:
                        app._cleanup()
                    release.set()
                    process.join(2)
                    self.assertFalse(process.is_alive())
                    review.assert_not_called()
                    self.assertEqual((injected, copied), ([], []))
                    self.assertNotIn(("state", "success"), app.events)
                    if action == "cancel":
                        app._start_recording()
                        app.is_recording = False
                        app._process_audio(object())
                        self.assertEqual(injected, [(REVISED, False, True)])

    def test_shutdown_invalidates_inflight_result(self):
        app, injected, copied = self.make_app()
        entered, release = threading.Event(), threading.Event()
        def review(*args):
            entered.set()
            release.wait(2)
            return REVISED
        with patch.object(gate, "run_review", side_effect=review):
            thread = threading.Thread(target=app._inject_text, args=(ORIGINAL,))
            thread.start()
            self.assertTrue(entered.wait(1))
            app._cleanup()
            release.set()
            thread.join(2)
        self.assertEqual((injected, copied), ([], []))

    def test_nontransient_limit_message_is_honest(self):
        app, _, _ = self.make_app()
        app._review_feedback("review_failed", "input-limit", 1)
        messages = [event[1] for event in app.events if event[0] == "notify"]
        self.assertIn("supera el límite", messages[-1])
        self.assertNotIn("Super+Espacio", messages[-1])


if __name__ == "__main__":
    unittest.main()
