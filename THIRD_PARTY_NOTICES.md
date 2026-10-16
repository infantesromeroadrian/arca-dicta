# Third-party notices

ARCA Dicta is an extracted integration with hyprwhspr. No assertion of
exclusive human authorship or independent creation is made. ARCA Dicta's
contribution is MIT licensed under [LICENSE](LICENSE), Copyright (c) 2026
ARCA Dicta contributors. That notice does not replace third-party ownership.

## hyprwhspr

- Project: <https://github.com/goodroot/hyprwhspr>
- Compatibility baseline: Arch package 1.49.0-1; upstream release v1.49.0,
  commit prefix `88ef652` recorded during provenance review.
- Copyright (c) 2025 goodroot. MIT License.
- Full installed license text retained in
  [LICENSES/hyprwhspr-MIT.txt](LICENSES/hyprwhspr-MIT.txt).
- Primary reference: <https://github.com/goodroot/hyprwhspr/blob/main/LICENSE>.

The 22 source files were not byte-identical to upstream files in the
provenance comparison. That comparison does not rule out adapted fragments;
the MIT notice is retained conservatively. Native modules remain external and
are imported or read for contract tests rather than vendored wholesale.

## External runtime components

| Component | Observed baseline | License provenance and boundary |
| --- | --- | --- |
| faster-whisper | 1.2.1 | MIT; [official license](https://github.com/SYSTRAN/faster-whisper/blob/master/LICENSE). External, not bundled. |
| CTranslate2 | 4.8.0 | MIT; [official license](https://github.com/OpenNMT/CTranslate2/blob/master/LICENSE). External, not bundled. |
| pulsectl | 24.12.0 | MIT; [official repository](https://github.com/mk-fg/python-pulse-control). External, not bundled. |
| numpy, PyAV, GTK, Cairo, PyGObject and native transitive dependencies | Provided by the prepared host runtimes | Exact versions and notices have not all been audited for redistribution. Nothing is bundled here; complete the audit before distributing a runtime. |

Links describe upstream provenance; they are not a substitute for checking
the licenses of a future built artifact and its exact dependency versions.

The source installer now downloads dependencies into user-owned environments
using the exact version/hash resolutions in `requirements/*.lock`. Those
environments and their wheels are not redistributed in the source archive.
Installed distribution metadata retains each package's license notices.
Native GTK/GI/Cairo/evdev and audio libraries remain host-provided. See
[dependency provenance](docs/provenance/dependencies.md).

## Architecture viewer

The generated `docs/architecture/pipeline.html` incorporates the Archify 2.16
viewer under the MIT License: Copyright (c) 2026 tt-a1i (Archify), and
Copyright (c) 2025 Cocoon AI (original architecture-diagram-generator).
Its full notice is retained in [LICENSES/archify-MIT.txt](LICENSES/archify-MIT.txt).
The ARCA Dicta diagram JSON describes this project; the upstream viewer is not
claimed as ARCA Dicta's original implementation.

## External model weights

The preserved local preview baseline references `Systran/faster-whisper-small`,
snapshot `536b0662742c02347bc0e980a01041f333bce120`. No weights or model card are
included. The conversion model card tags MIT; the source Hugging Face model
card tags Apache-2.0. Reconcile the exact weight artifacts and their notices
before redistribution; F1 does not approve such redistribution.
Primary references: [conversion model card](https://huggingface.co/Systran/faster-whisper-small),
[source Hugging Face model card](https://huggingface.co/openai/whisper-small).

Groq and Ollama Cloud remain external services. This repository contains no
service credentials, subscription terms or grant to redistribute their models.
