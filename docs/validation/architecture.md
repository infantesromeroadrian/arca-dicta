# Architecture view verification

Verified on 2026-10-16 with the installed Archify 2.16 renderer. This checks
the documentation artifact, not a running dictation session.

- Specification: [`pipeline.json`](../architecture/pipeline.json), SHA-256
  `2ba82c345f0fb485b97c4ab84fe7c66e32506701afeb7abd427716f8b2e9e75c`.
- Delivered viewer: [`pipeline.html`](../architecture/pipeline.html), SHA-256
  `1214b28b8d48d8a1a9fdf15038cae48401074d48befe4254a8c424ef64ebab3e`.
- Pinned code evidence: eight references verified against
  `1cf0e6432c460ad4f1603df6392e796a10879b52`.
- Showcase validation and atomic delivery: **9/9 checks**, zero errors and
  zero warnings. One focused layout correction moved an overlapping label.
- Browser containment passed at 1440×900, 1600×1000, 1920×1080 and 2048×1320.
  Four rendered screenshots were inspected, covering light/dark at the
  smallest and largest sizes. No clipping, label overlap or crossing was
  observed. Visual review: **passed**.
- Generated HTML contains no local home/tmp path or `file://` reference.
  The retained [MIT notice](../../LICENSES/archify-MIT.txt) is byte-identical
  to the generator's license.

The screenshots and detailed browser receipt are local validation evidence,
excluded from the distribution. Authored content is Spanish; fixed viewer
controls and the HTML language attribute default to English. See the
[bilingual explanation and deployment limits](../architecture/README.md).
