"""Strict review before native delivery; one cancellable draft lives in memory."""

from __future__ import annotations

import os
import shlex
import subprocess
import threading
import time
from pathlib import Path

from review import MAX_INPUT_BYTES, MAX_OUTPUT_BYTES, ReviewExit, _has_controls, valid_revision

HOOK = Path(__file__).resolve().with_name("review-hook")
HOOK_TIMEOUT = 5.0
HELD_MESSAGE = "Dictado retenido. Super+Espacio para reintentar"
REASONS = {
    "input-limit": "Dictado retenido: supera el límite de revisión. Usa record clear-last para descartarlo.",
    "invalid-input": "Dictado retenido: el texto no admite revisión. Usa record clear-last para descartarlo.",
    "missing-key": "Dictado retenido: falta la credencial de revisión. Reintenta cuando esté disponible.",
    "config-changed": "Dictado retenido: cambió la configuración de revisión. Restaura la configuración antes de reintentar.",
    "auth-error": "Dictado retenido: credencial de revisión rechazada. Corrígela antes de reintentar.",
    "timeout": "Dictado retenido: la revisión tardó demasiado. Super+Espacio para reintentar.",
    "rate-limit": "Dictado retenido: límite temporal del proveedor. Espera y usa Super+Espacio para reintentar.",
    "provider-error": "Dictado retenido: fallo de conexión o proveedor. Super+Espacio para reintentar.",
    "invalid-response": "Dictado retenido: respuesta de revisión incompleta o inválida. Super+Espacio para reintentar.",
    "fidelity-rejected": "Dictado retenido: la revisión no superó las comprobaciones de fidelidad. Super+Espacio para reintentar.",
    "internal-error": "Dictado retenido: fallo interno de revisión. Super+Espacio para reintentar.",
    "hook-failed": "Dictado retenido: no se pudo ejecutar la revisión. Super+Espacio para reintentar.",
}
BLOCKED_REASONS = frozenset({"input-limit", "invalid-input", "missing-key", "config-changed", "auth-error"})
EXIT_REASONS = {
    ReviewExit.INTERNAL_ERROR: "internal-error",
    ReviewExit.INPUT_LIMIT: "input-limit",
    ReviewExit.INVALID_INPUT: "invalid-input",
    ReviewExit.MISSING_KEY: "missing-key",
    ReviewExit.TIMEOUT: "timeout",
    ReviewExit.AUTH_ERROR: "auth-error",
    ReviewExit.RATE_LIMIT: "rate-limit",
    ReviewExit.PROVIDER_ERROR: "provider-error",
    ReviewExit.INVALID_RESPONSE: "invalid-response",
    ReviewExit.FIDELITY_REJECTED: "fidelity-rejected",
}


class ReviewFailure(Exception):
    """Carries only a stable reason code; never provider or dictation content."""


def review_config_supported(config) -> bool:
    try:
        command = config.get_setting("post_transcription_hook", None)
        return (
            isinstance(command, str)
            and shlex.split(command) == ["exec", str(HOOK)]
            and config.get_setting("transcription_backend") == "rest-api"
            and config.get_setting("recording_mode", "toggle") in {"auto", "toggle", "push_to_talk"}
            and not config.get_setting("realtime_live_typing", False)
        )
    except (AttributeError, ValueError):
        return False


def run_review(text: str, config) -> str:
    if not review_config_supported(config):
        raise ReviewFailure("config-changed")
    if len(text.encode("utf-8")) > MAX_INPUT_BYTES:
        raise ReviewFailure("input-limit")
    if not text.strip() or _has_controls(text):
        raise ReviewFailure("invalid-input")
    environment = os.environ.copy()
    environment["HYPRWHSPR_MODEL"] = str(config.get_setting("model", "") or "")
    environment["HYPRWHSPR_BACKEND"] = "rest-api"
    # Do not route through native PRESERVE semantics or execute a changed shell command.
    try:
        result = subprocess.run(
            [str(HOOK)], input=text.encode("utf-8"), capture_output=True,
            timeout=HOOK_TIMEOUT, env=environment,
        )
    except subprocess.TimeoutExpired:
        raise ReviewFailure("timeout") from None
    except (OSError, ValueError):
        raise ReviewFailure("hook-failed") from None
    if result.returncode != 0:
        raise ReviewFailure(EXIT_REASONS.get(result.returncode, "hook-failed"))
    try:
        if len(result.stdout) > MAX_OUTPUT_BYTES:
            raise ReviewFailure("invalid-response")
        candidate = result.stdout.decode("utf-8").rstrip("\r\n")
    except (AttributeError, UnicodeError):
        raise ReviewFailure("invalid-response") from None
    if not candidate.strip() or _has_controls(candidate):
        raise ReviewFailure("invalid-response")
    if not valid_revision(text, candidate):
        raise ReviewFailure("fidelity-rejected")
    return candidate


def make_review_injector(base: type, outcomes) -> type:
    class ReviewedInjector(base):
        def __init__(self, config) -> None:
            if not review_config_supported(config):
                raise RuntimeError("Strict review configuration is missing or unsupported; dictation stopped")
            super().__init__(config)
            self._review_lock = threading.RLock()
            self._review_generation = 0
            self._pending_review = None
            self._review_active = False
            self.review_feedback = lambda state, reason, generation: None

        def review_status(self) -> tuple[bool, bool]:
            with self._review_lock:
                return self._pending_review is not None, self._review_active

        def _review_notice(self, state: str, reason: str, generation: int) -> None:
            try:
                self.review_feedback(state, reason, generation)
            except Exception:
                # UI failure cannot strand a pending review or authorize delivery.
                pass

        def clear_pending_review(self) -> None:
            with self._review_lock:
                self._review_generation += 1
                self._pending_review = None
                generation = self._review_generation
            self._review_notice("cleared", "", generation)

        def begin_stream(self):
            raise RuntimeError("Strict review requires a complete dictation")

        def review_generation(self) -> int:
            with self._review_lock:
                return self._review_generation

        def inject_if_current(self, text: str, generation: int):
            with self._delivery_lock:
                return self._prepare_and_inject_text(text, generation)

        def _prepare_and_inject_text(self, text: str, generation=None):
            with self._review_lock:
                if generation is not None and generation != self._review_generation:
                    return outcomes.FAILED
                if self._pending_review is not None:
                    return outcomes.FAILED
                processed = self._preprocess_text(text).rstrip("\r\n")
                self._review_generation += 1
                self._pending_review = processed
            return self._attempt_review("paste_last")

        def _attempt_review(self, action: str):
            with self._review_lock:
                original = self._pending_review
                generation = self._review_generation
                if original is None or self._review_active:
                    return outcomes.FAILED
                self._review_active = True
            self._review_notice("reviewing", "", generation)
            try:
                candidate = run_review(original, self.config_manager)
            except Exception as error:
                reason = str(error) if isinstance(error, ReviewFailure) else "hook-failed"
                with self._review_lock:
                    current = generation == self._review_generation
                    self._review_active = False
                if current:
                    self._review_notice("review_failed", reason, generation)
                return outcomes.FAILED
            with self._review_lock:
                self._review_active = False
                if generation != self._review_generation or self._pending_review is None:
                    return outcomes.FAILED
                # Serialize clear with the delivery decision. It can invalidate network work,
                # but cannot recall clipboard delivery that has already begun.
                self._pending_review = None
                if self._should_append_trailing_space(candidate):
                    candidate += " "
                try:
                    if action == "copy_last":
                        with self._last_text_lock:
                            self._last_text = candidate
                        delivered = self._copy_text_to_clipboard(candidate)
                    else:
                        delivered = self._inject_via_clipboard_and_hotkey(
                            candidate, auto_submit=False, retain=True,
                        )
                except Exception:
                    delivered = False
                if not delivered:
                    with self._last_text_lock:
                        self._last_text = candidate
            self._review_notice("success" if delivered else "error", "", generation)
            return outcomes.INJECTED if delivered else outcomes.FAILED

        def retry_pending_review(self, action: str = "paste_last"):
            if not self._delivery_lock.acquire(blocking=False):
                return outcomes.FAILED
            try:
                return self._attempt_review(action)
            finally:
                self._delivery_lock.release()

        def recover_last(self, action):
            if action == "clear_last":
                self.clear_pending_review()
                return super().recover_last(action)
            if action not in {"copy_last", "paste_last"}:
                return False, "Unknown recovery action"
            pending, _ = self.review_status()
            if pending:
                result = self.retry_pending_review(action)
                if result == outcomes.INJECTED:
                    return True, "Dictado revisado y entregado"
                if not self.review_status()[0]:
                    return False, "No se pudo entregar el texto revisado. Usa record copy-last o paste-last."
                return False, HELD_MESSAGE
            return super().recover_last(action)

    return ReviewedInjector


def make_review_app(base: type, outcomes) -> type:
    class ReviewedApp(base):
        def __init__(self) -> None:
            self._review_ui_lock = threading.RLock()
            self._review_ui_generation = -1
            self._review_ui_epoch = 0
            self._review_ui_state = "idle"
            self._review_closing = False
            self._review_capture_cancelled = False
            self._review_process_context = threading.local()
            self._review_retry_thread = None
            self._review_finalize_thread = None
            super().__init__()
            self.text_injector.review_feedback = self._review_feedback

        def _review_feedback(self, state: str, reason: str, generation: int) -> None:
            with self._review_ui_lock:
                if generation < self._review_ui_generation or self._review_closing:
                    return
                if state == "review_failed":
                    if reason not in REASONS:
                        reason = "internal-error"
                    if reason in BLOCKED_REASONS:
                        state = "review_blocked"
                    try:
                        from service_log import log

                        log(f"[REVIEW] state={state} reason={reason}")
                    except Exception:
                        # Diagnostic output cannot suppress the retained-state UI.
                        pass
                self._review_ui_generation = generation
                self._review_ui_epoch += 1
                self._review_ui_state = state
                with self._cancel_pending_hide_lock:
                    self._cancel_pending_hide = True
                if state == "cleared":
                    super()._hide_mic_osd()
                    return
                runner = getattr(self, "_mic_osd_runner", None)
                if runner:
                    try:
                        if state in {"review_failed", "review_blocked", "success", "error"}:
                            runner.clear_preview_text()
                        visible_state = state
                        if state in {"review_failed", "review_blocked"} and os.environ.get("HYPRWHSPR_LOCAL_HUD") != "1":
                            visible_state = "error"
                        runner.set_state(visible_state)
                        if state in {"reviewing", "review_failed", "review_blocked"}:
                            runner.show()
                    except Exception:
                        pass
                if state in {"review_failed", "review_blocked"}:
                    self._notify_user("hyprwhspr", REASONS.get(reason, HELD_MESSAGE), urgency="normal")

        def _start_recording(self, language_override=None):
            with self._review_ui_lock:
                finalizing = self._review_finalize_thread is not None
            if finalizing:
                self._release_blocked_capture()
                return
            pending, busy = self.text_injector.review_status()
            with self._review_ui_lock:
                retry_running = self._review_retry_thread is not None
                if pending and not busy and not retry_running:
                    def retry():
                        try:
                            outcome = self.text_injector.retry_pending_review()
                            self._show_result_and_hide(outcome == outcomes.INJECTED)
                        finally:
                            with self._review_ui_lock:
                                self._review_retry_thread = None
                    self._review_retry_thread = threading.Thread(target=retry, daemon=True)
                    self._review_retry_thread.start()
            if pending or busy or retry_running:
                self._release_blocked_capture()
                return
            if not review_config_supported(self.config):
                self._notify_user("hyprwhspr", "La configuración de revisión ha cambiado. Dictado bloqueado.", urgency="normal")
                self._release_blocked_capture()
                return
            with self._review_ui_lock:
                self._review_ui_epoch += 1
                self._review_ui_state = "idle"
            result = super()._start_recording(language_override=language_override)
            if getattr(self, "is_recording", False):
                self._review_capture_cancelled = False
            return result

        def _process_audio(self, audio_data):
            if self._review_closing or self._review_capture_cancelled:
                return
            self._review_process_context.generation = self.text_injector.review_generation()
            try:
                return super()._process_audio(audio_data)
            finally:
                del self._review_process_context.generation

        def _handle_control_command(self, action, language=None):
            if (action in {"stop", "release"}
                    and self.config.get_setting("recording_mode", "toggle") in {"auto", "toggle"}):
                with self._review_ui_lock:
                    if self._review_finalize_thread is not None:
                        return
                    if getattr(self, "is_recording", False):
                        def finalize():
                            try:
                                super(ReviewedApp, self)._handle_control_command(action, language)
                            finally:
                                with self._review_ui_lock:
                                    self._review_finalize_thread = None
                        self._review_finalize_thread = threading.Thread(target=finalize, daemon=True)
                        self._review_finalize_thread.start()
                        return
            if action == "cancel":
                self._cancel_recording()
                return
            return super()._handle_control_command(action, language)

        def _handle_recovery(self, action):
            if action == "clear_last":
                self._review_capture_cancelled = True
            result = super()._handle_recovery(action)
            if action in {"copy_last", "paste_last"}:
                self._show_result_and_hide(result[0])
            return result

        def _inject_text(self, text):
            if self._review_closing or self._review_capture_cancelled:
                return outcomes.FAILED
            if self._recording_control_server.has_capture_subscriber():
                return super()._inject_text(text)
            generation = getattr(self._review_process_context, "generation",
                                 self.text_injector.review_generation())
            outcome = self.text_injector.inject_if_current(text, generation)
            if outcome == outcomes.INJECTED:
                self._mark_delivery_healthy()
            elif (not self.text_injector.review_status()[0]
                  and self._review_ui_state != "cleared" and not self._review_closing):
                self._notify_user("hyprwhspr", "No se pudo pegar el texto revisado. Usa record copy-last o paste-last.", urgency="normal")
            return outcome

        def _set_visualizer_state(self, state: str):
            with self._review_ui_lock:
                if self._review_ui_state in {"review_failed", "review_blocked", "reviewing"}:
                    return
                return super()._set_visualizer_state(state)

        def _show_result_and_hide(self, success: bool):
            with self._review_ui_lock:
                if self._review_ui_state in {"review_failed", "review_blocked", "reviewing", "cleared"}:
                    return
                if getattr(self, "is_recording", False):
                    return
                if self._review_ui_state in {"success", "error"}:
                    success = self._review_ui_state == "success"
                self._review_ui_epoch += 1
                epoch = self._review_ui_epoch
                super()._set_visualizer_state("success" if success else "error")

            def delayed_hide():
                time.sleep(1.25)
                with self._review_ui_lock:
                    if epoch == self._review_ui_epoch and not self._review_closing:
                        super(ReviewedApp, self)._hide_mic_osd()

            threading.Thread(target=delayed_hide, daemon=True).start()

        def _hide_mic_osd(self):
            with self._review_ui_lock:
                if self._review_ui_state in {"review_failed", "review_blocked", "reviewing"} and not self._review_closing:
                    return
                return super()._hide_mic_osd()

        def _cancel_recording(self):
            self._review_capture_cancelled = True
            self.text_injector.clear_pending_review()
            return super()._cancel_recording()

        def _cleanup(self):
            with self._review_ui_lock:
                self._review_closing = True
                self._review_ui_epoch += 1
            self.text_injector.clear_pending_review()
            return super()._cleanup()

    return ReviewedApp


def install_review(upstream) -> None:
    from text_injector import InjectionOutcome

    upstream.TextInjector = make_review_injector(upstream.TextInjector, InjectionOutcome)
    upstream.hyprwhsprApp = make_review_app(upstream.hyprwhsprApp, InjectionOutcome)
    upstream.log("[REVIEW] strict gate enabled")
