# Alpha dependency provenance

Recorded 2026-10-16. These pins describe preparation of the source-installer
candidate, not publication or physical acceptance.

| Component | Fixed source or observed boundary |
| --- | --- |
| hyprwhspr | Native package 1.49.0-1. Upstream v1.49.0 commit `88ef652a974854fefdd1ef2bfc9c79ae31c52008`; the annotated tag is unsigned. |
| Upstream tarball | `https://github.com/goodroot/hyprwhspr/archive/refs/tags/v1.49.0.tar.gz`, SHA256 `b1e552bb3be43438d3080a4dc1c6d550acb8860582f45f8ff353b35d29b5263c`. |
| AUR recipe | Repository `https://aur.archlinux.org/hyprwhspr.git`, commit `7ea4a07c09656c4c84e6cecc9636e969b3fbe553`. Installer requires the installed package; it does not execute AUR build scripts. |
| Python | Native CPython 3.14; prepared on 3.14.8. No interpreter is bundled. |
| Resolver | `uv pip compile`, uv 0.11.14, Linux x86_64/Python 3.14, `--generate-hashes --no-header --no-annotate`; uv 0.13.1 also exercised for installation in clean Arch. |
| Python dependencies | Full transitive version/hash resolutions in `requirements/daemon.lock` and `requirements/worker.lock`; installation uses `uv pip sync --require-hashes --no-build`. |
| Native ABI | GI/PyGObject, Cairo, GTK4/Gtk4LayerShell, evdev, PortAudio, libsndfile and libpulse remain host dependencies. Rolling Arch package resolution is not frozen by the pip locks. |
| Local model | `Systran/faster-whisper-small` at `536b0662742c02347bc0e980a01041f333bce120`; four exact hashes in `packaging/model-small.json`. |

The native contract is checked independently of the package label: 12 core
and 14 HUD file hashes. The installer never broadens those allowlists.

The model's four files are downloaded directly from revision-specific HTTPS
URLs. `model.bin` is 483,546,902 bytes. The model API's LFS SHA256 was reconciled
with the downloaded bytes. `config.json`, `tokenizer.json` and `vocabulary.txt`
were also hash checked. No weights enter Git or the release artifact.

An isolated real worker load with all four files returned protocol-1 `ready`
and exited cleanly on EOF. A separate directory containing identical
`model.bin`, `config.json` and `tokenizer.json`, but no `vocabulary.txt`, returned
`model-unavailable`. Therefore the install/doctor contract requires four files;
the preserved runtime's older three-file presence guard is not a sufficient
model-readiness check. Neither smoke test accessed microphone or providers.

The real protocol was also exercised with 16,000 float32 zeros at 16 kHz and
language autodetection: model load and one inference completed, yielding empty
text, with a worker-reported decode time of 1,288 ms. This synthetic silence
result checks the inference path; it is not a microphone, speech-quality or
physical latency measurement.

The lock files are reproducible dependency inputs, not a claim that the entire
Arch desktop is reproducible or that future provider model aliases are stable.
Groq and Ollama remain external providers; their credentials, API availability,
quality and physical latency require separate validation.
