"""Offscreen HUD contract checks; no GTK window, device or service is opened."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cairo

import hud_bridge
from hud_runtime.mic_osd.hud import (
    HEIGHT, LABELS, TEXT_SIZE, TEXT_WIDTH, TEXT_Y, WIDTH,
    CAPTURE_STATES, HudState, HudVisualization, HudWindow, PreviewDisplay,
    clean_text, draw_body, preview_layout,
)
from hud_runtime.mic_osd.main import (
    native_visualizations_with_review_state, refresh_state_on_show,
    use_capture_feed_only,
)


def context():
    return cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, WIDTH, HEIGHT))


class LayoutTests(unittest.TestCase):
    def test_real_spanish_uses_two_legible_lines(self):
        layout = preview_layout(context(),
            "Esta es una prueba sintética del dictado. Ahora el texto se puede "
            "leer con claridad en dos líneas completas.")
        self.assertEqual(layout.get_line_count(), 2)
        self.assertLessEqual(layout.get_pixel_size().width, TEXT_WIDTH)
        self.assertLessEqual(TEXT_Y + layout.get_pixel_size().height, HEIGHT - 4)
        self.assertEqual(layout.get_font_description().get_size(), TEXT_SIZE * 1024)
        self.assertTrue(layout.get_font_description().get_size_is_absolute())

    def test_long_dictation_keeps_latest_words(self):
        text = "Inicio antiguo. " * 100 + "Este es el final más reciente."
        layout = preview_layout(context(), text)
        self.assertLessEqual(layout.get_line_count(), 2)
        self.assertTrue(layout.get_text().endswith("Este es el final más reciente."))
        self.assertTrue(layout.get_text().startswith("… "))

    def test_single_enormous_word_is_bounded(self):
        layout = preview_layout(context(), "supercalifragilistico" * 100)
        self.assertLessEqual(layout.get_line_count(), 2)
        self.assertLessEqual(layout.get_pixel_size().width, TEXT_WIDTH)

    def test_control_characters_and_surrogates_do_not_reach_renderer(self):
        result = clean_text("hola\x00\ud800\n mundo\tcorrecto")
        self.assertEqual(result, "hola mundo correcto")
        preview_layout(context(), result)

    def test_partial_text_is_never_displayed_as_final(self):
        for state in HudState:
            with self.subTest(state=state):
                layout = draw_body(context(), state, "Borrador sintético único")
                self.assertEqual("Borrador sintético único" in layout.get_text(),
                                 state in CAPTURE_STATES - {HudState.UNAVAILABLE})

    def test_required_states_render_without_device_or_window(self):
        for state in HudState:
            with self.subTest(state=state):
                visual = HudVisualization()
                visual.set_state(state.value)
                visual.draw_background(context(), WIDTH, HEIGHT)
                visual.draw(context(), WIDTH, HEIGHT)
                self.assertEqual(visual.hud_state, state)
                self.assertTrue(LABELS[state])

    def test_reviewing_uses_processing_animation_not_recording(self):
        visual = HudVisualization()
        visual.set_state("reviewing")
        self.assertEqual(visual.state_manager.current_state.value, "processing")
        self.assertFalse(visual._show_elapsed_time)

    def test_review_failure_erases_draft_and_shows_retention_instruction(self):
        for state in (HudState.REVIEW_FAILED, HudState.REVIEW_BLOCKED):
            display = PreviewDisplay()
            display.set_text("Private synthetic draft")
            display.set_state(state.value)
            self.assertEqual(display.visible_text(), ("", False))
            visual = HudVisualization()
            visual.set_state(state.value)
            self.assertEqual(visual.state_manager.current_state.value, "error")
            layout = draw_body(context(), state, "Private synthetic draft")
            self.assertIn("Dictado retenido", layout.get_text())
            self.assertNotIn("Private", layout.get_text())

    def test_drawing_failure_falls_back_without_retrying_broken_decoration(self):
        window = SimpleNamespace(
            _render_failed=False, _visualizer_state="recording", _preview_text="",
            visualization=HudVisualization(), _body_key=None, _body_layout=None,
            _display=PreviewDisplay(),
        )
        with patch("hud_runtime.mic_osd.hud.draw_body", side_effect=RuntimeError) as draw:
            HudWindow._on_draw(window, None, context(), WIDTH, HEIGHT)
            HudWindow._on_draw(window, None, context(), WIDTH, HEIGHT)
        self.assertTrue(window._render_failed)
        self.assertEqual(draw.call_count, 1)

    def test_initial_copy_reflects_actual_preview_phase(self):
        for state, expected in (
            (HudState.LISTENING, "Te escucho."),
            (HudState.DECODING, "Preparando primeras palabras…"),
            (HudState.UNAVAILABLE, "Vista previa no disponible."),
        ):
            self.assertTrue(draw_body(context(), state, "").get_text().startswith(expected))

    def test_provisional_keeps_two_lines_at_seventeen_pixels(self):
        text = ("Esta es una prueba sintética del dictado. Ahora el texto se puede "
                "leer con claridad en dos líneas completas.")
        for state in (HudState.PROCESSING, HudState.REVIEWING):
            layout = draw_body(context(), state, text, provisional=True)
            self.assertEqual(layout.get_line_count(), 2)
            self.assertEqual(layout.get_font_description().get_size(), 17 * 1024)
            self.assertLessEqual(TEXT_Y + layout.get_pixel_size().height, HEIGHT - 4)


class DisplayLifecycleTests(unittest.TestCase):
    def test_native_clear_before_processing_keeps_only_provisional_copy(self):
        display = PreviewDisplay()
        display.set_state("listening")
        display.set_text("Borrador sintético de esta sesión")
        display.set_text("")
        self.assertEqual(display.visible_text(), ("", False))
        for state in ("processing", "reviewing"):
            display.set_state(state)
            self.assertEqual(display.visible_text(),
                             ("Borrador sintético de esta sesión", True))

    def test_success_error_and_new_session_erase_the_draft(self):
        for state in ("success", "error", "listening", "recording"):
            display = PreviewDisplay()
            display.set_text("Texto privado sintético")
            display.set_state("processing")
            display.set_state(state)
            self.assertEqual(display.last_draft, "")
            self.assertEqual(display.text, "")

    def test_hide_or_cancel_reset_erases_all_session_text(self):
        display = PreviewDisplay()
        display.set_text("Texto privado sintético")
        display.set_state("reviewing")
        display.reset()
        self.assertEqual(display.visible_text(), ("", False))
        self.assertEqual(display.last_draft, "")

    def test_late_text_cannot_replace_the_busy_draft(self):
        display = PreviewDisplay()
        display.set_text("Último borrador válido")
        display.set_state("processing")
        display.set_text("Tardío")
        self.assertEqual(display.visible_text(), ("Último borrador válido", True))


class SafetyTests(unittest.TestCase):
    def test_new_show_repolls_identical_unavailable_state(self):
        class NativeApp:
            visible = False
            _last_visualizer_state = "preview_unavailable"

            def _show(self):
                self.visible = True

        app = refresh_state_on_show(NativeApp)()
        app._show()
        self.assertIsNone(app._last_visualizer_state)
        self.assertTrue(app.visible)

    def test_native_fallback_maps_reviewing_to_processing(self):
        from hud_runtime.mic_osd.visualizations.waveform import WaveformVisualization

        original = {"waveform": WaveformVisualization, "other": object()}
        fallback = native_visualizations_with_review_state(original)
        visual = fallback["waveform"]()
        visual.set_state("reviewing")
        self.assertEqual(visual.state_manager.current_state.value, "processing")
        self.assertIs(fallback["other"], original["other"])
        self.assertIs(original["waveform"], WaveformVisualization)

    def test_feed_fallback_never_constructs_original_audio_monitor(self):
        original_audio = Mock(side_effect=AssertionError("device opened"))
        feed = Mock(return_value=object())
        native = SimpleNamespace(AudioMonitor=original_audio, FeedLevelSource=feed,
                                 MIC_OSD_LEVEL_FEED_FILE=Path("synthetic-feed"))
        use_capture_feed_only(native)
        source = native.AudioMonitor(samplerate=44100, blocksize=1024)
        self.assertIs(source, feed.return_value)
        feed.assert_called_once_with(Path("synthetic-feed"))
        original_audio.assert_not_called()

    def test_current_hashes_and_local_sources_pass(self):
        self.assertTrue(hud_bridge.hud_supported())

    def test_upstream_drift_or_missing_file_disables_hud(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertFalse(hud_bridge.hud_supported(root=Path(folder)))

    def test_broken_local_source_disables_hud(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder)
            for name in hud_bridge.LOCAL_FILES:
                path = runtime / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("invalid python!", encoding="utf-8")
            self.assertFalse(hud_bridge.hud_supported(runtime=runtime))

    def test_failed_guard_clears_hook_capability(self):
        with patch.dict(os.environ, {hud_bridge.HUD_ENV: "1"}):
            with patch.object(hud_bridge, "hud_supported", return_value=False):
                self.assertFalse(hud_bridge.install_hud())
                self.assertNotIn(hud_bridge.HUD_ENV, os.environ)

    def test_runner_keeps_original_lifecycle_and_falls_back_once(self):
        class NativeRunner:
            def __init__(self, level_source=None, style="waveform"):
                self._style = style
                self._mic_osd_dir = Path("native/mic_osd")
                self.level_source = level_source
                self.attempts = []

            def _ensure_daemon(self):
                self.attempts.append(self._mic_osd_dir)
                return self._mic_osd_dir == Path("native/mic_osd")

            def stop(self):
                return "native stop"

        runner = hud_bridge.make_hud_runner(NativeRunner)(level_source="same-feed")
        with patch.dict(os.environ, {hud_bridge.HUD_ENV: "1"}):
            with patch.object(hud_bridge, "hud_supported", return_value=True):
                self.assertTrue(runner._ensure_daemon())
            self.assertNotIn(hud_bridge.HUD_ENV, os.environ)
        self.assertEqual(len(runner.attempts), 2)
        self.assertEqual(runner.level_source, "same-feed")
        self.assertEqual(runner.stop(), "native stop")


if __name__ == "__main__":
    unittest.main()
