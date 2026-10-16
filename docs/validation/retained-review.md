# F1.3.3 — Retained review diagnosis and candidate

Date: 2026-10-16. Status: the two review modules were activated and verified
with synthetic provider probes. Local physical dictation was subsequently
confirmed by the user, without independent observation. The existing service was stopped and started for the atomic
two-file replacement; config, dependencies, model and the other 20 source files
were unchanged. No Git mutation, build, release or publication was performed.

## Observed baseline

The deployed source was the preserved strict snapshot identified by
[source-inventory.json](../provenance/source-inventory.json), not this checkout.
The user observed a retained dictation. Bounded journal extraction emitted only
timestamps, status and lengths: the relevant STT response was HTTP 200 in
0.53 s, with 347 characters after 27.52 s of audio. The HUD matched the fixed
`review_failed` state. Its later modification time could include a manual retry
and cannot establish the duration of the original review attempt.

No real dictation, credentials, raw journal, provider response or process memory
was exported. The original historical failure cannot be attributed to one cause:
the old hook collapsed provider, parsing and fidelity failures to exit 1, and
the gate collapsed both exit 1 and the internal timeout exit 5 to `hook-failed`.

The coordinator ran two bounded synthetic probes using the deployed hook and
the service's existing credential without any clipboard or keyboard action:

- A short Spanish example was parsed and accepted in 0.796 s. This demonstrates
  that the current endpoint, credential, model and schema request can work.
- `eh Docker funciona correctamente` returned a completed answer in 0.406 s,
  with the exact literal `Docker` at the beginning, but `valid_revision=False`.
  The answer was not pasted or saved. This reproduces the defect with a real
  provider for a synthetic case; it does not prove the cause of the historical
  347-character dictation.

[Ollama's structured-output documentation](https://docs.ollama.com/capabilities/structured-outputs)
states that cloud structured outputs are unsupported. The successful current
probe contradicts using that statement as the cause of this failure. The
candidate therefore retains `format`, the same model and endpoint, `think=false`,
`stream=false`, temperature 0, 512 tokens, and the 4 s / 5 s deadlines.

## Focused change

`review.py` now separates literal token extraction from protection selection.
A protected source literal is checked against every candidate token with the
same spelling and boundary normalization, independently of its new position.
This allows removing a filler before `Docker` or `María` without allowing a
rename, deletion or case change.

For joining sentences, an explicit ES/EN connector list permits only the
lowercase spelling of the same word when it was Titlecase at a sentence start.
It does not permit deletion of the connector. Acronyms, digits, paths and
identifiers cannot enter this exception. This remains a heuristic; a name
identical to a connector is ambiguous. The pre-existing failure to protect some
Titlecase names at the very start of the original is an incidental finding
outside this repair, not a guarantee of semantic equivalence.

The hook distinguishes timeout, authentication, rate limit, provider/network,
invalid JSON/envelope and fidelity rejection from internal failure. The gate
preserves the reason, retains the pending text and presents a fixed message.
Transient reasons remain `review_failed`, with one manual retry path and no
automatic retry. Only allowlisted state/reason codes reach the existing logger.
No provider message, text, credential or exception representation is logged.

## Validation

Four new tests were run before the implementation: 30 failing subcases reproduced
the position/capitalization defects and the collapsed or absent diagnostics.
The new provider-boundary test runs fake HTTP through the real `_call_provider`,
`run_hook`, gate and injector, checks zero failed delivery and a retained draft,
then permits exactly one successful manual retry. UI tests check retryable
states, fixed messages and sanitization of an unknown reason.

The first affected-suite run found one old assertion expecting the generic
timeout message; it was updated to the new required reason-specific message.
A subsequent test indentation mistake was corrected before freezing the
candidate. Existing number, negation, identifier, cancellation, timeout and
native-injection cases remain part of the full suite.

| Final check | Result |
| --- | --- |
| `python -B -m unittest discover -q` | 120 tests passed in 12.002 s. |
| `ruff check --no-cache .` | Passed. |
| In-memory Python compilation | All four changed Python files compiled; no bytecode written. |
| `review.py` SHA-256 | `3fde9b3491bb745c31ece18ff24028bf74c01287d7fd994504115d3d4914e75a` |
| `review_gate.py` SHA-256 | `b67f8ac4d968e0e66b139ab108c701eebd461184b36b39b02b1aa77168b40221` |
| `test_review.py` SHA-256 | `eedbaf6721c66b4e83f78b392a2147d8e47dbe58a8c9cfa22be4e874229ba66b` |
| `test_review_gate.py` SHA-256 | `7c32e1b6f6a27cfd12bd8a9e16413d300b22f280d36a5505311b6d706d7cedc6` |

The complete fixed artifact, including documentation and provenance, is listed
in [candidate-sha256.json](../provenance/candidate-sha256.json). The older
[offline extraction evidence](offline.md) describes the earlier 116-test
candidate; its no-review-change claim is historical and does not describe this
subsequent repair.

## Independent review and live probes

The coordinator's protected independent review completed with exit 0, no
demonstrated defects and 26 focused tests passing in 0.091 s. The reviewed fixed
manifest was `c8be3bb3033cc2e3a28e7c4c00958722719e9a6cedf1657fafc9fa7e82efbd88`.
Its private process evidence remains outside this repository. Subsequent
manifest changes document activation; the reviewed Python bytes did not change.

The coordinator exercised the real provider through the hook subprocess, gate
and a simulated delivery boundary, using synthetic text only:

| Target | Docker moved to start | Sentence connector | Observed delivery |
| --- | --- | --- | --- |
| Isolated candidate | PASS, 0.514 s | PASS, 0.702 s | One simulated delivery per case; no pending draft. |
| Activated runtime, checked at 14:04:53 UTC | PASS, 0.715 s | PASS, 0.553 s | One simulated delivery per case; no pending draft. |

Both probe sets produced zero real clipboard operations and no auto-submit.
These are individual synthetic timings, not microphone-to-paste latency
distributions or proof of general language quality. They demonstrate correction
of the reproduced pattern, not the cause of the historical retained dictation.

## Activation boundary and rollback

Only `review.py` and `review_gate.py` need to be copied into the existing live
adapter for this repair. This does not activate the unrelated F1 portability
extraction or change the service target. Tests, documentation, manifests and
other checkout files are not runtime deployment files.

Before activation, all 22 live and rollback-source hashes matched the strict
baseline, the config hash matched, and the HUD was not recording. The user
confirmed that the old pending dictation could be discarded. The implementer
stopped the service, atomically replaced only the two authorized modules while
preserving modes, and started the same service. Activation checks completed
in 5.215 s: a new process was active at the original target, the strict-review
startup marker was present and both module hashes matched the table above.
The other 20 files and the configuration remained unchanged; no rollback was
needed. The coordinator's post-deployment check confirmed active/running state,
zero automatic restarts, the exact imported live modules and the intact
22-file rollback source.

At 14:09 UTC the user answered **«Sí, se pega correctamente»** to the post-activation
request to try dictation with Super+Space and check whether the text pasted and
the retained-dictation notice disappeared. Status: **local physical check confirmed
by the user**, not independently observed. This supports closing repair F1.3.3;
it does not establish both editor/browser destinations, p50/p95 latency, a second
machine or alpha readiness. F1.3.2 physical measurements remain pending. The
historical retained dictation's cause remains unknown.

Rollback restores both files from the preserved strict snapshot and restarts
the same service. It restores the earlier strict behavior, including its known
false rejection, rather than bypassing mandatory review. Config and native
library files remain unchanged. See [baseline-operativo.md](baseline-operativo.md).
