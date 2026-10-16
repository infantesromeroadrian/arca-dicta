"""Offline Pulse fakes and the installed recording methods; never open audio."""

from __future__ import annotations

import ast
import importlib.util
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import main as launcher
from playback import SinkMuteGuard, install_playback, make_playback_suppressor


from dicta_config import upstream_library

UPSTREAM = upstream_library()


def sink(index=1, name="output-a", mute=False, serial="device-a"):
    return SimpleNamespace(
        index=index, name=name, driver="synthetic-driver", mute=mute,
        proplist={"device.serial": serial},
        volume=SimpleNamespace(values=[0.75, 0.43]),
    )


def stream(index=1, volume=1.0):
    return SimpleNamespace(
        index=index, corked=False, volume=SimpleNamespace(values=[volume, volume]),
        proplist={"application.name": "Synthetic browser",
                  "application.process.binary": "browser",
                  "application.process.id": "101"},
    )


class FakePulse:
    def __init__(self):
        self.sinks = [sink()]
        self.streams = [stream()]
        self.calls = []
        self.failures = {}
        self.on_mute = lambda: None

    def __call__(self, name):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def sink_list(self):
        return list(self.sinks)

    def sink_input_list(self):
        return list(self.streams)

    def sink_mute(self, index, mute):
        failure = self.failures.pop((index, mute), None)
        self.calls.append((index, mute))
        if failure == "before":
            raise OSError("synthetic transport failure")
        next(item for item in self.sinks if item.index == index).mute = mute
        self.on_mute()
        if failure == "after":
            raise OSError("synthetic response lost after mutation")

    def volume_set_all_chans(self, item, value):
        raise AssertionError("the candidate must never write any volume")


class NativeStub:
    def __init__(self, reduction_percent=50):
        self.calls = []
        self.active = False

    def suppress(self, mode="duck", reduction_percent=None):
        self.calls.append((mode, reduction_percent))
        self.active = True
        return True

    def restore(self):
        self.active = False
        return True

    @property
    def is_active(self):
        return self.active


class PlaybackTests(unittest.TestCase):
    def setUp(self):
        self.pulse = FakePulse()
        self.logs = []
        self.guard = SinkMuteGuard(self.pulse, self.logs.append)

    def test_native_vanished_stream_expires_then_replacement_stays_zero(self):
        spec = importlib.util.spec_from_file_location(
            "native_ducker_regression", UPSTREAM / "src/audio_ducker.py")
        native = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(native)

        def write_volume(item, value):
            item.volume.values[:] = [value, value]

        with patch.object(native, "pulsectl", SimpleNamespace(Pulse=self.pulse)), \
                patch.object(native, "PULSECTL_AVAILABLE", True), \
                patch.object(self.pulse, "volume_set_all_chans", write_volume):
            ducker = native.AudioDucker(100)
            original = self.pulse.streams[0]
            ducker.duck()
            self.pulse.streams.clear()
            ducker.restore()
            ducker.duck()
            ducker.restore()
            replacement = stream(2, original.volume.values[0])
            self.pulse.streams.append(replacement)
            ducker.duck()
            ducker.restore()
            self.assertEqual(replacement.volume.values, [0.0, 0.0])

    def test_recreated_stream_keeps_volume_and_output_is_restored(self):
        original = self.pulse.streams[0]
        self.assertTrue(self.guard.suppress())
        self.assertTrue(self.pulse.sinks[0].mute)
        self.pulse.streams.clear()
        self.assertTrue(self.guard.restore())
        for _ in range(3):
            self.guard.suppress()
            self.guard.restore()
        replacement = stream(2, original.volume.values[0])
        self.pulse.streams.append(replacement)
        self.assertEqual(replacement.volume.values, [1.0, 1.0])
        self.assertFalse(self.pulse.sinks[0].mute)

    def test_preserves_channel_volumes_and_preexisting_mute(self):
        muted = sink(2, "output-b", True, "device-b")
        self.pulse.sinks.append(muted)
        self.pulse.streams[0].volume.values = [0.29, 1.21]
        self.guard.suppress()
        self.guard.restore()
        self.assertEqual(self.pulse.sinks[0].volume.values, [0.75, 0.43])
        self.assertEqual(self.pulse.streams[0].volume.values, [0.29, 1.21])
        self.assertTrue(muted.mute)
        self.assertEqual(self.pulse.calls, [(1, True), (1, False)])

    def test_suppress_and_restore_are_idempotent(self):
        for _ in range(2):
            self.assertTrue(self.guard.suppress())
        for _ in range(2):
            self.assertTrue(self.guard.restore())
        self.assertEqual(self.pulse.calls, [(1, True), (1, False)])
        self.assertFalse(self.guard.is_active)

    def test_volume_change_during_recording_survives_restore(self):
        self.guard.suppress()
        self.pulse.sinks[0].volume.values = [0.91, 0.38]
        self.guard.restore()
        self.assertEqual(self.pulse.sinks[0].volume.values, [0.91, 0.38])

    def test_partial_suppress_failure_before_or_after_write_remains_restorable(self):
        for failure in ("before", "after"):
            with self.subTest(failure=failure):
                self.setUp()
                self.pulse.sinks.append(sink(2, "output-b", serial="device-b"))
                self.pulse.failures[(1, True)] = failure
                self.assertFalse(self.guard.suppress())
                self.assertTrue(self.pulse.sinks[1].mute)
                self.assertTrue(self.guard.restore())
                self.assertTrue(all(not item.mute for item in self.pulse.sinks))
                self.assertTrue(self.logs)

    def test_partial_restore_failure_does_not_strand_other_output_and_retries(self):
        self.pulse.sinks.append(sink(2, "output-b", serial="device-b"))
        self.guard.suppress()
        self.pulse.failures[(1, False)] = "before"
        self.assertFalse(self.guard.restore())
        self.assertTrue(self.pulse.sinks[0].mute)
        self.assertFalse(self.pulse.sinks[1].mute)
        self.assertTrue(self.guard.is_active)
        self.assertTrue(self.guard.restore())
        self.assertFalse(self.pulse.sinks[0].mute)

    def test_restore_connection_failure_retains_snapshot_for_retry(self):
        self.guard.suppress()
        with patch.object(self.pulse, "sink_list", side_effect=OSError("synthetic disconnect")):
            self.assertFalse(self.guard.restore())
        self.assertTrue(self.guard.is_active)
        self.assertTrue(self.guard.restore())
        self.assertFalse(self.pulse.sinks[0].mute)

    def test_reused_index_never_unmutes_another_device_and_reconnect_can_restore(self):
        self.guard.suppress()
        original = self.pulse.sinks[0]
        other = sink(1, "output-a", True, "different-device")
        self.pulse.sinks = [other]
        self.assertFalse(self.guard.restore())
        self.assertTrue(other.mute)
        original.index = 7
        self.pulse.sinks.append(original)
        self.assertTrue(self.guard.restore())
        self.assertFalse(original.mute)
        self.assertTrue(other.mute)
        self.assertEqual(self.pulse.calls, [(1, True), (7, False)])

    def test_hotplugged_output_is_not_owned_or_changed_by_restore(self):
        self.guard.suppress()
        plugged = sink(5, "new-output", True, "new-device")
        self.pulse.sinks.append(plugged)
        self.assertTrue(self.guard.restore())
        self.assertTrue(plugged.mute)

    def test_user_unmute_does_not_cause_a_second_write(self):
        self.guard.suppress()
        self.pulse.sinks[0].mute = False
        self.assertTrue(self.guard.restore())
        self.assertEqual(self.pulse.calls, [(1, True)])

    def test_partial_duck_and_pause_are_delegated_to_native(self):
        suppressor = make_playback_suppressor(NativeStub, self.pulse, self.logs.append)()
        for mode, percent in (("duck", 60), ("pause", 100)):
            suppressor.suppress(mode, percent)
            self.assertTrue(suppressor.is_active)
            suppressor.restore()
        self.assertEqual(suppressor.calls, [("duck", 60), ("pause", 100)])
        self.assertEqual(self.pulse.calls, [])
        suppressor.suppress("duck", 100)
        suppressor.restore()
        self.assertEqual(self.pulse.calls, [(1, True), (1, False)])

    def test_installed_private_contract_hashes_match(self):
        self.assertTrue(launcher.upstream_supported())
        self.assertIn("src/audio_ducker.py", launcher.SUPPORTED_FILES)
        self.assertIn("src/playback_suppressor.py", launcher.SUPPORTED_FILES)

    def test_installation_and_clamping_preserve_native_default_mode_contract(self):
        upstream = SimpleNamespace(PlaybackSuppressor=NativeStub, log=self.logs.append)
        with patch("pulsectl.Pulse", self.pulse):
            install_playback(upstream)
        suppressor = upstream.PlaybackSuppressor(150)
        self.assertTrue(suppressor.suppress("unknown-mode"))
        self.assertTrue(self.pulse.sinks[0].mute)
        self.assertEqual(suppressor.calls, [])
        self.assertTrue(suppressor.restore())


def native_recording_class():
    # Execute the installed methods themselves with inert collaborators. No
    # upstream imports, app startup, microphones, sockets or filesystem writes.
    tree = ast.parse((UPSTREAM / "src/app/recording.py").read_text())
    native_class = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    wanted = {"_suppress_recording_playback", "_restore_recording_playback",
              "_stop_recording", "_cancel_recording", "_cleanup_recording_state"}
    native_class.body = [node for node in native_class.body
                         if isinstance(node, ast.FunctionDef) and node.name in wanted]
    module = ast.Module(body=[native_class], type_ignores=[])
    namespace = {"log": lambda message: None, "normalize_backend": lambda value: value}
    exec(compile(module, "installed-recording-methods", "exec"), namespace)
    return namespace["RecordingMixin"]


class NativeLifecycleTests(unittest.TestCase):
    def app(self):
        pulse = FakePulse()
        app = native_recording_class()()
        app.playback_suppressor = make_playback_suppressor(NativeStub, pulse, lambda _: None)()
        app._playback_lock = threading.Lock()
        app._recording_lock = threading.Lock()
        app._recording_finalizing = threading.Event()
        app._recording_session = object()
        app._playback_session = None
        app._playback_shutdown = False
        app.is_recording = True
        settings = {"audio_ducking": True, "audio_ducking_percent": 100}
        app.config = SimpleNamespace(get_setting=lambda key, default=None: settings.get(key, default))
        app.audio_capture = SimpleNamespace(stop_recording=Mock(return_value=None),
                                            lock=threading.Lock(), frames_since_start=0)
        app.audio_manager = SimpleNamespace(play_error_sound=Mock())
        app.whisper_manager = SimpleNamespace(discard_realtime_audio=Mock())
        for name in ("_ptt_reset", "_autostop_stop_silence_monitor", "_clear_mic_osd_preview_text",
                     "_set_visualizer_state", "_stop_audio_level_monitoring", "_write_recording_status",
                     "_wait_for_start_settled", "_save_debug_recording", "_notify_zero_volume",
                     "_show_result_and_hide", "_notify_capture", "_cancel_live_typing", "_hide_mic_osd"):
            setattr(app, name, Mock())
        return app, pulse

    def test_native_stop_cancel_and_shutdown_restore_idempotently(self):
        for action in ("stop", "cancel", "shutdown"):
            with self.subTest(action=action):
                app, pulse = self.app()
                self.assertTrue(app._suppress_recording_playback(app._recording_session))
                self.assertTrue(pulse.sinks[0].mute)
                for _ in range(2):
                    if action == "stop":
                        app._stop_recording()
                    elif action == "cancel":
                        app._cancel_recording()
                    else:
                        app._restore_recording_playback(shutdown=True)
                self.assertFalse(pulse.sinks[0].mute)
                self.assertEqual(pulse.calls, [(1, True), (1, False)])

    def test_stale_session_cannot_restore_new_recording(self):
        app, pulse = self.app()
        app._suppress_recording_playback(app._recording_session)
        app._restore_recording_playback(object())
        self.assertTrue(pulse.sinks[0].mute)
        app._restore_recording_playback(app._recording_session)
        self.assertFalse(pulse.sinks[0].mute)

    def test_cancel_during_suppress_restores_in_native_finally(self):
        app, pulse = self.app()
        pulse.on_mute = lambda: setattr(app, "is_recording", False)
        self.assertFalse(app._suppress_recording_playback(app._recording_session))
        self.assertFalse(pulse.sinks[0].mute)


if __name__ == "__main__":
    unittest.main()
