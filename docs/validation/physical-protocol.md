# Physical validation protocol

These helpers are **validation-only**. They do not provide physical results by
existing or passing their offline tests. Actual trials, visual classification,
quality checks and restoration of the normal runtime remain operator actions.
No physical trial has been run by the helper's implementation tests.

## Endpoints and native evidence

Use the same observer, hashes and conditions for baseline and candidate:

1. **Capture ACK → first visible provisional text.** The external wrapper stamps
   the exact `service_log.log("Recording started")` invocation. In the inspected
   hyprwhspr 1.49.0 code, `src/app/recording.py` emits this only after audio
   callbacks have been received and playback suppression has succeeded, just
   before showing the HUD. This is a software capture acknowledgement, not the
   first physical microphone sample. The first provisional text must then be
   visually identified in the captured **HUD region**.
2. **Stop command dispatch → first observed revised paste.** The control helper
   brackets the invocation/return of the unchanged `hyprwhspr record stop` CLI.
   The command is emitted within that interval if the CLI succeeds. Its exit
   status alone does not prove dispatch or completion: the foreground target
   must visibly contain the revised synthetic dictation. This condition does
   **not** measure physical Super+Space key press → FIFO latency. Validate that
   hotkey functionally in a separate case.

The native CLI's printed “started” only acknowledges FIFO writing;
`recording_status=true` is written before microphone startup. Neither is the
capture ACK. Native `capture`/`capture_trace` changes delivery semantics and
must not be used for these paste measurements. The product's `first_preview_ms`,
provider response, review-hook exit and injection return value are not visible
endpoints. No journal, transcript or clipboard is read by the observer.

All accepted timestamps use `time.monotonic_ns()` on the **same host/boot**.
The HTML page's `performance.now()`/input/rAF values are explicitly diagnostic
only and never merged with this clock. A manual reaction mark, visual ACK proxy,
unreconciled video clock or DOM callback cannot substitute for the two metrics.

## Setup owned by the operator

Keep the verified personal runtime backup. Activate the wrapper only through
the one reversible validation service selected by the operator; do not start
a second dictation daemon. The following is the wrapper command for that
temporary service, **not a command to run alongside the existing daemon**:

```sh
/usr/bin/python -B /ABS/REPO/tools/physical_capture.py wrap \
  --entrypoint /ABS/BASELINE-OR-CANDIDATE/main.py \
  --events /ABS/PRIVATE-SCRATCH/acks.jsonl --max-events 100 --seconds 1800
```

Use the normal runtime Python/environment if different. The default
`/usr/lib/hyprwhspr/lib/src/service_log.py` is pinned to its inspected SHA-256.
The wrapper imports the unchanged entrypoint and intercepts only the exact ACK
string; all normal logging still follows its original path. It never copies
other log content. ACK write failure does not change capture, review or paste.
It stops collecting after its event/time limit while the sole product runtime
retains its normal lifecycle. A numeric `collector_end` includes write failures
and maximum measured collector write cost when the runtime exits normally.
Use a **new** event path on each activation; a restart into an existing path is
refused. Disable automatic restart for this temporary validation invocation.

Use an owned private scratch directory (0700), preferably the user runtime
tmpfs. Generated files are 0600. Record wrapper/probe and baseline/candidate
hashes in the canonical evidence before measurements. The same temporary
instrumentation must be removed before the final runtime is restored.

Open a synthetic target only when ready:

```sh
python -B tools/physical_probe.py editor --case-id ES-TECH
```

This GTK4 target is titled `ARCA Dicta Validation`, has a memory-only TextView,
and never controls recording. Alternatively open `tools/physical_probe.html`
through the authorised browser surface and focus its textarea. It has no
network, local storage, microphone access or clipboard reader. Its optional
download contains only numeric input-event diagnostics, not text. Close or
clear the targets after each trial. Their UI must be visually checked by the
operator; offline tests do not exercise the live compositor/browser.

Prepare a dedicated, unlocked test surface containing only known synthetic
phrases. Resolve the exact HUD and target geometries using the actual display
scale/monitor layout. **Never use a whole-display crop or a private window.**
Keep focus, geometry and display scaling fixed for the condition. The tool
cannot detect if another window covers a region: abort/classify invalid if it
does, and discard that trial's images.

## One bounded trial

The examples use placeholders; the operator substitutes checked paths and ROIs.
The two ROIs may be on different monitors, including negative coordinates.
The target ROI must include the complete expected synthetic text, and the HUD
ROI enough context to distinguish a draft from “Te escucho”/status placeholders.

Start capture **before** requesting recording, leaving at least one absent
frame. The `frames` process runs in the foreground for a finite time; root can
keep its tool session open while issuing the explicit start/stop commands:

```sh
python -B tools/physical_capture.py frames /ABS/PRIVATE-SCRATCH/trial-001 \
  --hud 'X,Y WIDTHxHEIGHT' --target 'X,Y WIDTHxHEIGHT' --seconds 15 --fps 10
python -B tools/physical_capture.py control start \
  --events /ABS/PRIVATE-SCRATCH/start-001.jsonl --trial-id 1
# Speak the known phrase; keep the intended target focused.
python -B tools/physical_capture.py control stop \
  --events /ABS/PRIVATE-SCRATCH/stop-001.jsonl --trial-id 1
```

Only the explicit `control` command requests microphone start/stop/cancel.
Its stdout and stderr are discarded, never retained. Each output path is new.
The CLI process has a 5 s timeout; timing out the CLI does not cancel a command
already sent. If capture remains active, the operator must explicitly cancel:

```sh
python -B tools/physical_capture.py control cancel \
  --events /ABS/PRIVATE-SCRATCH/cancel-001.jsonl --trial-id 1
```

`frames` invokes `grim -g REGION -s 1 -t png -` separately for each ROI. It never
captures the complete display. Both ROIs share the host monotonic clock but
are **sequential**, not simultaneous; each frame has its own `[before, after]`
interval. The capture scale is explicitly 1 (one image pixel per logical
coordinate); verify that the synthetic text is legible at this resolution.
Defaults: 15 s, at most 150 ticks × 2 ROIs. Hard limits: 60 s,
30 ticks/s, 3,600 frames, 128 MB total, 2 million pixels per ROI, 10 MB per image,
and 2 s per grim invocation. A blocked final invocation can extend collection
by at most its timeout. Failures, actual timestamps and stop reason are recorded
in the numeric manifest. CPU/disk cost is part of the observer condition;
do not subtract a guessed overhead. Use the same settings on both variants.

Inspect the ordered frames in the controlled scratch surface. Identify the
last captured frame with no draft and the first frame containing actual draft
text (ignore status text); do the same for revised text in the focused target.
Check every preceding frame, and confirm the expected language, negation,
`request_id`, Docker/RAG and 42; allow legitimate punctuation/filler edits.
The final text must be complete, revised and inserted once, without an
unsolicited Enter. Unexpected content, focus change or missing frames must be
classified rather than silently treated as success.

```sh
python -B tools/physical_capture.py bounds /ABS/PRIVATE-SCRATCH/trial-001 \
  --region hud --last-absent 12 --first-present 13
python -B tools/physical_capture.py bounds /ABS/PRIVATE-SCRATCH/trial-001 \
  --region target --last-absent 82 --first-present 83
```

The result uses the **start** of the absent capture and **end** of the present
capture. This conservative interval includes both screenshot durations and
the actual sampling gap, not a claimed ±50 ms from a nominal 10 Hz setting.
It measures compositor images observable through grim, not physical panel scanout.
Sampling cannot rule out a transient appearance entirely between two captures;
report “first observed” and check that text remains visible. A missing absent
or present frame makes that endpoint unmeasured. Preserve these bounds as
numbers, then delete the temporary synthetic images:

```sh
python -B tools/physical_capture.py purge-frames /ABS/PRIVATE-SCRATCH/trial-001
```

Cleanup validates every generated filename/type/owner and refuses the entire
operation if anything unexpected is present. It uses no recursive deletion.
Retain only numeric evidence; do not add temporary images to Git/release files.
If a crashed capture has no valid manifest, inspect and remove only its exact
known temporary files manually; there is no broad recovery deletion.

## Evidence and statistics

Create one file per variant, condition, target and cold/warm state. Keep the
external numeric condition ID mapped to sample rate, anonymised input-device
class, recording mode, phrase/duration band, model IDs, power/CPU contention,
network conditions, scaling/refresh rate and observer rate in canonical notes.
Do not export personal device IDs, paths, secrets or dictated content.

```sh
python -B tools/physical_probe.py new /ABS/PRIVATE-SCRATCH/baseline-warm.json \
  --condition-id 1 --variant baseline --target gtk-editor --temperature warm \
  --clock host-monotonic --observation frame-intervals --ack-source native-capture-ack
python -B tools/physical_probe.py add /ABS/PRIVATE-SCRATCH/baseline-warm.json \
  --trial-id 1 --case-id ES-TECH --outcome success --quality pass --revised-confirmed \
  --capture-ack-ns ACK_NS --preview-visible-ns ABSENT_START_NS:PRESENT_END_NS \
  --stop-dispatch-ns STOP_BEFORE_NS:STOP_AFTER_NS \
  --paste-visible-ns ABSENT_START_NS:PRESENT_END_NS
python -B tools/physical_probe.py add /ABS/PRIVATE-SCRATCH/baseline-warm.json \
  --trial-id 2 --case-id ES-TECH --outcome timeout
python -B tools/physical_probe.py summary /ABS/PRIVATE-SCRATCH/baseline-warm.json
```

Obtain `ACK_NS` from the corresponding numeric wrapper sequence and stop bounds
from that trial's command file; exclude unrelated/interrupted capture sequences.
The schema rejects arbitrary text fields and requires all four endpoints,
confirmed revised content and passed quality for a success. It counts attempts,
failures, timeouts and cancellations. Keep an aborted trial too; never replace
it with its successful retry or silently omit it.

For event intervals A=[a,b], B=[c,d], elapsed time is
`[max(0,c-b), d-a]`. For **at least 20 successful warm trials in each reported
case/condition**, p50 and p95 use nearest-rank (`ceil(p*n)`, 1-based), separately
on lower and upper bounds. Thus p95 is an interval, not a midpoint with invented
precision. Cases/languages are never pooled to reach 20. Cold starts remain
separate and this helper reports their n/min/max bounds without percentiles.
Exploratory/manual/proxy/event-only datasets never receive accepted percentiles.

Efficient sequence: one documented cold/warm-up trial, then ≥20 successful
ES-TECH warm trials with all attempts counted for the baseline; freeze the
relative comparison objective and tolerable observation width **before**
candidate trials; repeat the same condition ≥20 times for the candidate.
Use a separate 20-success condition for browser latency if reporting a browser
p50/p95. Exercise EN-TECH and MIX-TECH as quality cases without claiming their
percentiles unless they each reach 20. When baseline/candidate intervals overlap,
do not infer a speedup smaller than the observation uncertainty. No absolute
latency SLA is introduced by this helper.

Keep functional cases separate from latency runs: preview pause/resume; cancel
during preview/STT/review; review failure and retry without a new recording;
exactly-once paste; no raw fallback; native Super+Space and push-to-talk; audible
playback suppression/restoration, pre-muted output, disconnected/reused and new
sinks. Those cases require actual operator/hardware/provider evidence from
`scenarios.md`; ordinary successful dictation samples do not prove them.
