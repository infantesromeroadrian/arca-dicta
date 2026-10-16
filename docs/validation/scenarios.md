# F1.3.1 validation scenarios

Prepared on 2026-10-16. Offline checks are executed separately in
[offline.md](offline.md). Every physical scenario below is **PENDING** for this
extracted candidate. There are no microphone, desktop-paste or provider
latency measurements in F1, and no synthetic timing is labelled physical.

## Quality and interaction matrix

| ID | Scenario / synthetic utterance | Required observable result | F1 evidence / remaining work |
| --- | --- | --- | --- |
| ES-TECH | «No cambies request_id. Revisa el RAG pipeline con Docker y conserva 42.» | Spanish, negation, identifier and number survive; filler/repetition may be edited. | Mocked bilingual review guards covered; actual microphone/STT/review/paste pending. |
| EN-TECH | “Do not rename request_id. Check the RAG pipeline with Docker and keep 42.” | English remains English; no translation or answered instruction. | Mocked English contract covered; actual voice path pending. |
| MIX-TECH | «Revisa el deployment, keep request_id unchanged y no cambies 42.» | Preserve each language and technical literals. | Mocked mixed-language contract covered; real model quality pending. |
| PREVIEW | Speak continuously, then pause and resume. | Provisional recent text, bounded two-line HUD, no second microphone, final audio unchanged. | Capture-tail, lifecycle and offscreen layout covered; visible compositor/audio check pending. |
| CANCEL-PREVIEW | Cancel while the local worker has an in-flight result. | Clear preview; old generation cannot overwrite next capture. | Controlled worker/cancellation tests covered; desktop check pending. |
| CANCEL-STT | Cancel before final STT returns. | No late review or paste; later generation remains valid. | Fake late STT/generation checks covered; real network case pending. |
| CANCEL-REVIEW | Cancel/clear while review is in flight. | Pending text discarded, no later delivery. Cancellation cannot undo delivery already started. | Threaded strict-gate tests covered; real UI case pending. |
| RETRY | Make review fail once; retry with Super+Space. | One pending in memory; retry does not record; valid revision delivered exactly once, without Enter. | Fake provider/injector tests covered; keyboard/desktop path pending. |
| BLOCK | Missing key, wrong hook config, invalid JSON, input/output cap or timeout. | Error/retained state, no raw clipboard/paste fallback. | Synthetic failures and real isolated hook timeout covered; service-level UI pending. |
| FIFO | Auto/toggle stop or release; cancel during finalization. Repeat with native push-to-talk. | Auto/toggle finalization in one thread; capture blocked until completion; push-to-talk retains synchronous native behavior. | Native methods/FIFO with fake capture checked; physical bindings pending. |
| PLAYBACK | Play browser/media audio, dictate with full suppression, stop/cancel/shutdown. | Existing sinks muted then restored; no application/sink volume writes; pre-muted outputs preserved. | Fake Pulse and native lifecycle covered; audible test pending. |
| DEVICES | Disconnect/reconnect a muted sink; reuse its numeric index for another device. | Identity guard prevents restoring a different device; unresolved restoration remains pending. | Fake device identities covered; hardware sequence pending. |
| NEW-SINK | Attach a new output during recording. | Preserved limit: no automatic muting of newly attached sinks. | Known code boundary; physical observation pending. |
| COMPAT | Alter one core hash, and separately one HUD-only hash in an isolated fixture. | Core drift stops adapted daemon; HUD drift may use native renderer, without relaxing review. | Offline hash guards covered; no live package mutation permitted by F1. |
| PATHS | Change working directory; use spaces in checkout/runtime paths; omit required configuration. | Correct adjacent entrypoint; CLI arguments preserved; invalid daemon paths stop before GUI import. | Executed in `test_config`; no desktop started. |

## Future latency protocol

Record two separate monotonic measurements for each successful physical trial:

1. **First visible provisional text:** capture-start acknowledgement → first
   non-empty provisional text actually presented by the compositor. A worker
   result timestamp alone does not prove visibility.
2. **Stop → paste:** user stop event → revised text observed in the intended
   foreground editor. A provider response, hook exit or clipboard change alone
   does not prove a successful paste.

Keep local model cold-start and warm-run samples separate; record language,
utterance duration, capture sample rate/device, mode, display scaling,
power profile, CPU contention, network conditions and provider model IDs.
Do not retain voice, credentials or dictated content in shared evidence.
Use repeatable synthetic phrases and at least 20 successful trials per reported
condition; also count attempts, failures, timeouts and cancellations so success
latency does not hide failure rate. Report `n`, min/max, p50 and p95 using the
nearest-rank definition, and retain only anonymized timing rows. These choices
define a future measurement protocol, not an existing acceptance threshold.

| Metric | Samples in F1 | p50 | p95 |
| --- | --- | --- | --- |
| First visible provisional text, cold | 0 | Not measured | Not measured |
| First visible provisional text, warm | 0 | Not measured | Not measured |
| Stop → observed revised paste | 0 | Not measured | Not measured |

The configured 1.2 s first snapshot, 2 s inference spacing, 8 s audio window,
4 s review budget and 5 s outer timeout are limits/settings, not these metrics.

## Host conditions supplied for the offline run

x86_64; Intel Core Ultra 9 185H; 22 logical CPUs; MemTotal 65,254,584 KiB;
kernel 7.2.9-2-cachyos; Python 3.14.8-1; hyprwhspr 1.49.0-1;
PipeWire 1:1.6.9-1; WirePlumber 0.5.18-1.1; Hyprland 0.56.2-4.1;
GTK4 1:4.24.1-1.1. These are the current host conditions, not a validated
multi-host compatibility matrix. No PID, user name, host identity or personal
device identifier is exported.
