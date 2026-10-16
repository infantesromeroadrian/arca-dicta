"""Release tests use only synthetic files and never read personal runtime data."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest

from tools.build_release import ALLOWLIST, ReleaseError, build_release


class ReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="dicta-release-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source"
        (self.source / "packaging").mkdir(parents=True)
        (self.source / "VERSION").write_text("0.1.0-alpha.1\n")
        (self.source / "install").write_text("#!/bin/sh\nexit 0\n")
        (self.source / "README.md").write_text("Synthetic release fixture.\n")
        self.names = ["VERSION", "install", "README.md", ALLOWLIST]
        self.allowlist()

    def allowlist(self) -> None:
        (self.source / ALLOWLIST).write_text("\n".join(self.names) + "\n")

    def test_manifest_hashes_all_inputs_and_archive_contains_only_allowlist(self) -> None:
        (self.source / ".env").write_text("synthetic-private-fixture")
        result = build_release(self.source, self.root / "out")
        manifest = json.loads(result.manifest.read_text())
        self.assertEqual(manifest["version"], "0.1.0-alpha.1")
        self.assertEqual(set(manifest["files"]), set(self.names))
        prefix = "arca-dicta-0.1.0-alpha.1/"
        with tarfile.open(result.archive) as archive:
            actual = {entry.name.removeprefix(prefix) for entry in archive if entry.isfile()}
            self.assertEqual(actual, set(self.names) | {"release-manifest.json"})
            for name, expected in manifest["files"].items():
                content = archive.extractfile(prefix + name).read()
                self.assertEqual(hashlib.sha256(content).hexdigest(), expected)
            self.assertEqual(archive.extractfile(prefix + "release-manifest.json").read(),
                             result.manifest.read_bytes())
        self.assertEqual(result.checksum.read_text(),
                         f"{hashlib.sha256(result.archive.read_bytes()).hexdigest()}  {result.archive.name}\n")

    def test_archive_is_byte_identical_after_changing_mtimes_and_modes(self) -> None:
        first = build_release(self.source, self.root / "first")
        for name in self.names:
            path = self.source / name
            path.chmod(0o777)
            os.utime(path, (123456789, 123456789))
        second = build_release(self.source, self.root / "second")
        self.assertEqual(first.archive.read_bytes(), second.archive.read_bytes())
        self.assertEqual(first.manifest.read_bytes(), second.manifest.read_bytes())
        with tarfile.open(first.archive) as archive:
            for entry in archive:
                self.assertEqual((entry.uid, entry.gid, entry.mtime), (0, 0, 0))
                self.assertEqual((entry.uname, entry.gname), ("", ""))
                expected = 0o755 if entry.isdir() or entry.name.endswith("/install") else 0o644
                self.assertEqual(entry.mode, expected)
        self.assertEqual(first.archive.read_bytes()[4:8], b"\0\0\0\0")
        self.assertTrue(gzip.decompress(first.archive.read_bytes()))

    def test_unsafe_paths_are_rejected_before_output_is_created(self) -> None:
        for name in (".", "../outside", "/absolute", "a/../README.md", "a//b", "./README.md",
                     "a\\b", "README.md ", ".env", "models/model.bin", "release-manifest.json"):
            with self.subTest(name=name):
                self.names.append(name)
                self.allowlist()
                with self.assertRaises(ReleaseError):
                    build_release(self.source, self.root / "out")
                self.assertFalse((self.root / "out").exists())
                self.names.pop()

    def test_missing_nonregular_and_duplicate_inputs_are_rejected(self) -> None:
        for name in ("missing", "packaging", "README.md"):
            with self.subTest(name=name):
                self.names.append(name)
                self.allowlist()
                with self.assertRaises(ReleaseError):
                    build_release(self.source, self.root / "out")
                self.names.pop()

    def test_symlink_file_and_parent_are_rejected_even_inside_source(self) -> None:
        for name, target in (("linked.md", self.source / "README.md"),
                             ("linked/release-files.txt", self.source / "packaging")):
            with self.subTest(name=name):
                link = self.source / name.split("/")[0]
                link.symlink_to(target, target_is_directory=target.is_dir())
                self.names.append(name)
                self.allowlist()
                with self.assertRaisesRegex(ReleaseError, "symlinks"):
                    build_release(self.source, self.root / "out")
                self.names.pop()
                link.unlink()

    def test_version_validation_rejects_path_whitespace_and_ambiguous_versions(self) -> None:
        for value in ("../escape", "01.2.3", "1.2", "1.2.3\n\n", " 1.2.3", "1.2.3-alpha.01",
                      "v1.2.3", "1" * 65 + ".2.3"):
            with self.subTest(value=value):
                (self.source / "VERSION").write_text(value)
                with self.assertRaisesRegex(ReleaseError, "VERSION"):
                    build_release(self.source, self.root / "out")

    def test_source_outputs_are_confined_to_dist_and_never_overwrite_artifacts(self) -> None:
        with self.assertRaisesRegex(ReleaseError, "dist"):
            build_release(self.source, self.source / "release")
        result = build_release(self.source, self.source / "dist")
        original = result.archive.read_bytes()
        with self.assertRaisesRegex(ReleaseError, "already exists"):
            build_release(self.source, self.source / "dist")
        self.assertEqual(result.archive.read_bytes(), original)

    def test_output_symlinks_are_rejected(self) -> None:
        target = self.root / "target"
        target.mkdir()
        link = self.root / "linked-output"
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(ReleaseError, "symlinks"):
            build_release(self.source, link / "nested")
        self.assertEqual(list(target.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
