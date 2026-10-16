"""Legible two-line dictation HUD, using the existing capture level feed."""

from __future__ import annotations

from enum import Enum
import math
import unicodedata

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Pango, PangoCairo

from .theme import theme
from .visualizations.base import BaseVisualization
from .visualizations.waveform import WaveformVisualization
from .window import OSDWindow

WIDTH = 560
HEIGHT = 124
TEXT_SIZE = 17
TEXT_WIDTH = WIDTH - 40
TEXT_Y = 67


class HudState(str, Enum):
    RECORDING = "recording"
    LISTENING = "listening"
    DECODING = "decoding"
    UNAVAILABLE = "preview_unavailable"
    PAUSED = "paused"
    PROCESSING = "processing"
    REVIEWING = "reviewing"
    REVIEW_FAILED = "review_failed"
    REVIEW_BLOCKED = "review_blocked"
    SUCCESS = "success"
    ERROR = "error"


LABELS = {
    HudState.RECORDING: "Escuchando",
    HudState.LISTENING: "Escuchando",
    HudState.DECODING: "Escuchando",
    HudState.UNAVAILABLE: "Escuchando",
    HudState.PAUSED: "En pausa",
    HudState.PROCESSING: "Transcribiendo",
    HudState.REVIEWING: "Revisando",
    HudState.REVIEW_FAILED: "Dictado retenido",
    HudState.REVIEW_BLOCKED: "Dictado retenido",
    HudState.SUCCESS: "Listo",
    HudState.ERROR: "Error",
}
MESSAGES = {
    HudState.RECORDING: "Te escucho.",
    HudState.LISTENING: "Te escucho.",
    HudState.DECODING: "Preparando primeras palabras…",
    HudState.UNAVAILABLE: "Vista previa no disponible. Tu dictado sigue grabándose.",
    HudState.PAUSED: "Dictado en pausa.",
    HudState.PROCESSING: "Preparando tu texto…",
    HudState.REVIEWING: "Revisando la redacción…",
    HudState.REVIEW_FAILED: "Dictado retenido. Super+Espacio para reintentar",
    HudState.REVIEW_BLOCKED: "Dictado retenido. Consulta el aviso para continuar.",
    HudState.SUCCESS: "Listo para continuar.",
    HudState.ERROR: "No se pudo completar el dictado. Vuelve a intentarlo.",
}
CAPTURE_STATES = frozenset({
    HudState.RECORDING, HudState.LISTENING, HudState.DECODING,
    HudState.UNAVAILABLE,
})
BUSY_STATES = frozenset({HudState.PROCESSING, HudState.REVIEWING})


def normalize_state(value: str) -> HudState:
    try:
        return HudState(value.lower())
    except (ValueError, AttributeError):
        return HudState.PROCESSING


def clean_text(text: str) -> str:
    """Bound rendering work and keep untrusted control characters out of Pango."""
    text = str(text)[-2048:]
    return " ".join(
        "".join(
            " " if unicodedata.category(char) in {"Cc", "Cs"} else char
            for char in text
        ).split()
    )


class PreviewDisplay:
    """Memory-only display state; final delivery never reuses a draft."""

    def __init__(self) -> None:
        self.state = HudState.RECORDING
        self.text = ""
        self.last_draft = ""

    def reset(self) -> None:
        self.state = HudState.RECORDING
        self.text = self.last_draft = ""

    def set_state(self, value: str) -> None:
        state = normalize_state(value)
        if state in {HudState.SUCCESS, HudState.ERROR, HudState.REVIEW_FAILED, HudState.REVIEW_BLOCKED} or (
            state in CAPTURE_STATES
            and self.state not in CAPTURE_STATES | {HudState.PAUSED}
        ):
            self.text = self.last_draft = ""
        self.state = state

    def set_text(self, text: str) -> None:
        self.text = clean_text(text)
        if self.text and self.state in CAPTURE_STATES:
            self.last_draft = self.text

    def visible_text(self) -> tuple[str, bool]:
        if self.state in BUSY_STATES and self.last_draft:
            return self.last_draft, True
        if self.state in CAPTURE_STATES - {HudState.UNAVAILABLE}:
            return self.text, False
        return "", False


def text_layout(cr: cairo.Context, text: str, size: int, bold: bool = False):
    layout = PangoCairo.create_layout(cr)
    font = Pango.FontDescription()
    font.set_family("Noto Sans")
    font.set_absolute_size(size * Pango.SCALE)
    font.set_weight(Pango.Weight.SEMIBOLD if bold else Pango.Weight.NORMAL)
    layout.set_font_description(font)
    layout.set_text(clean_text(text), -1)
    return layout


def preview_layout(cr: cairo.Context, text: str, width: int = TEXT_WIDTH):
    """Fit the newest words into two shaped lines, rather than shrinking text."""
    layout = text_layout(cr, text, TEXT_SIZE)
    layout.set_width(width * Pango.SCALE)
    layout.set_wrap(Pango.WrapMode.WORD_CHAR)
    words = clean_text(text).split()
    if layout.get_line_count() > 2:
        low, high = 1, max(1, len(words) - 1)
        while low < high:
            middle = (low + high) // 2
            layout.set_text("… " + " ".join(words[middle:]), -1)
            if layout.get_line_count() > 2:
                low = middle + 1
            else:
                high = middle
        layout.set_text("… " + " ".join(words[low:]), -1)
        # A single enormous token has no word boundary to drop.
        if not words[low:]:
            layout.set_text(clean_text(text), -1)
    layout.set_height(-2)
    layout.set_ellipsize(Pango.EllipsizeMode.START)
    return layout


class HudVisualization(WaveformVisualization):
    show_preview = False

    def __init__(self) -> None:
        super().__init__()
        self.hud_state = HudState.RECORDING
        self.animate = True
        self.provisional = False

    def set_state(self, state_str: str) -> None:
        self.hud_state = normalize_state(state_str)
        native_state = (
            "processing" if self.hud_state == HudState.REVIEWING
            else "error" if self.hud_state in {HudState.REVIEW_FAILED, HudState.REVIEW_BLOCKED}
            else "recording" if self.hud_state in CAPTURE_STATES
            else self.hud_state.value
        )
        super().set_state(native_state)

    def state_color(self) -> tuple:
        if self.hud_state in {HudState.PROCESSING, HudState.REVIEWING}:
            return theme.processing_dot
        if self.hud_state == HudState.SUCCESS:
            return theme.success_dot
        if self.hud_state in {HudState.ERROR, HudState.REVIEW_FAILED, HudState.REVIEW_BLOCKED}:
            return theme.error_dot
        if self.hud_state == HudState.PAUSED:
            return theme.paused_dot
        return theme.recording_dot

    @staticmethod
    def _frame(cr: cairo.Context, width: int, height: int) -> None:
        cut, edge = 10, 1
        cr.move_to(cut, edge)
        for x, y in (
            (width - cut, edge), (width - edge, cut),
            (width - edge, height - cut), (width - cut, height - edge),
            (cut, height - edge), (edge, height - cut), (edge, cut),
        ):
            cr.line_to(x, y)
        cr.close_path()

    def draw_background(self, cr: cairo.Context, width: int, height: int) -> None:
        cr.save()
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        self._frame(cr, width, height)
        cr.set_source_rgba(*theme.background[:3], 0.98)
        cr.fill_preserve()
        cr.set_source_rgba(*theme.border[:3], 0.45)
        cr.set_line_width(1)
        cr.stroke()
        color = self.state_color()
        cr.set_source_rgba(*color[:3], 0.75)
        cr.set_line_width(1.5)
        for x, direction in ((11, 1), (width - 11, -1)):
            cr.move_to(x, 1.5)
            cr.line_to(x + direction * 24, 1.5)
            cr.move_to(x, height - 1.5)
            cr.line_to(x + direction * 24, height - 1.5)
        cr.stroke()
        cr.restore()

    def draw(self, cr: cairo.Context, width: int, height: int) -> None:
        color = self.state_color()
        cr.set_source_rgba(*color[:3], 1)
        cr.arc(24, 21, 3, 0, 2 * math.pi)
        cr.fill()
        label = text_layout(cr, LABELS[self.hud_state], 13, bold=True)
        cr.move_to(35, 11)
        PangoCairo.show_layout(cr, label)
        if self.provisional and self.hud_state in BUSY_STATES:
            marker = text_layout(cr, "Provisional", 12)
            cr.set_source_rgba(*theme.text[:3], 0.85)
            cr.move_to(width - 20 - marker.get_pixel_size().width, 11)
            PangoCairo.show_layout(cr, marker)
        elif self.hud_state in CAPTURE_STATES | {HudState.PAUSED}:
            elapsed = self._format_elapsed_time(self._get_elapsed_seconds())
            timer = text_layout(cr, elapsed, 12)
            font = timer.get_font_description()
            font.set_family("Noto Sans Mono")
            timer.set_font_description(font)
            cr.set_source_rgba(*theme.text[:3], 0.85)
            cr.move_to(width - 20 - timer.get_pixel_size().width, 11)
            PangoCairo.show_layout(cr, timer)

        available = width - 40
        gap = 4
        bar_width = (available - (self.num_bars - 1) * gap) / self.num_bars
        busy = self.hud_state in {HudState.PROCESSING, HudState.REVIEWING}
        for index in range(self.num_bars):
            level = float(self.bar_heights[index])
            if busy:
                phase = self.state_manager.animation_phase if self.animate else 0
                level = 0.18 + 0.38 * (0.5 + 0.5 * math.sin(index * 0.32 - phase))
            elif self.hud_state not in CAPTURE_STATES | {HudState.PAUSED}:
                level = 0.05
            bar_height = max(2, min(22, level * 22))
            fraction = index / (self.num_bars - 1)
            shade = tuple(
                theme.bar_left[i] * (1 - fraction) + theme.bar_right[i] * fraction
                for i in range(3)
            ) if not busy else color[:3]
            cr.set_source_rgba(*shade, 0.9 if not busy else 0.6)
            cr.rectangle(20 + index * (bar_width + gap), 46 - bar_height / 2,
                         bar_width, bar_height)
            cr.fill()
        cr.set_source_rgba(*theme.border[:3], 0.16)
        cr.set_line_width(1)
        cr.move_to(20, 61.5)
        cr.line_to(width - 20, 61.5)
        cr.stroke()


def body_layout(cr: cairo.Context, state: HudState, preview: str,
                width: int = WIDTH, provisional: bool = False):
    show_draft = state in CAPTURE_STATES - {HudState.UNAVAILABLE} or (
        provisional and state in BUSY_STATES
    )
    content = clean_text(preview) if show_draft else ""
    content = content or MESSAGES[state]
    return preview_layout(cr, content, width - 40)


def draw_body(cr: cairo.Context, state: HudState, preview: str,
              width: int = WIDTH, layout=None, provisional: bool = False):
    layout = layout if layout is not None else body_layout(
        cr, state, preview, width, provisional
    )
    color = theme.text if provisional else theme.bar_right
    cr.set_source_rgba(*color[:3], 0.85 if provisional else 1)
    cr.move_to(20, TEXT_Y)
    PangoCairo.show_layout(cr, layout)
    return layout


class HudWindow(OSDWindow):
    def __init__(self, visualization, width=WIDTH, height=HEIGHT, **kwargs) -> None:
        self._render_failed = False
        self._body_key = None
        self._body_layout = None
        self._display = PreviewDisplay()
        self._hud_visible = False
        super().__init__(visualization, width, height, **kwargs)
        settings = Gtk.Settings.get_default()
        if settings is not None:
            self.visualization.animate = bool(settings.get_property("gtk-enable-animations"))

    def set_preview_text(self, text: str) -> None:
        self._display.set_text(text)
        super().set_preview_text(text)

    def set_visualizer_state(self, state: str) -> None:
        self._display.set_state(state)
        if self._display.state in {HudState.SUCCESS, HudState.ERROR, HudState.REVIEW_FAILED, HudState.REVIEW_BLOCKED}:
            self._preview_text = ""
            self._body_key = self._body_layout = None
        super().set_visualizer_state(state)

    def set_visible(self, visible: bool) -> None:
        if not visible or not self._hud_visible:
            self._display.reset()
            self._preview_text = ""
            self._body_key = self._body_layout = None
            self.visualization.provisional = False
            if visible:
                self.visualization.set_state("recording")
                self._visualizer_state = "recording"
        self._hud_visible = visible
        super().set_visible(visible)

    def _on_draw(self, area, cr, width, height) -> None:
        if not self._render_failed:
            cr.save()
            try:
                state = self._display.state
                preview, provisional = self._display.visible_text()
                self.visualization.provisional = provisional
                self.visualization.draw_background(cr, width, height)
                self.visualization.draw(cr, width, height)
                key = (state, preview, width, provisional)
                if key != self._body_key:
                    self._body_layout = body_layout(cr, state, preview, width, provisional)
                    self._body_key = key
                draw_body(cr, state, preview, width, self._body_layout, provisional)
                return
            except Exception:
                # A drawing callback must remain safe even if shaping fails.
                # Drop the custom decoration once, without logging user text.
                self._render_failed = True
            finally:
                cr.restore()
        BaseVisualization.draw_background(self.visualization, cr, width, height)
        WaveformVisualization.draw(self.visualization, cr, width, height)
        OSDWindow._draw_preview_text(self, cr, width, height)
