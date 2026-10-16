"""Build a source-only installer archive from an explicit, reviewed allowlist."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import stat
import tarfile


ALLOWLIST = "packaging/release-files.txt"
MANIFEST = "release-manifest.json"
EXECUTABLES = frozenset({"install", "launch", "review-hook"})
EXCLUDED_PARTS = frozenset({
    ".git", ".env", ".venv", "venv", "__pycache__", ".ruff_cache",
    ".pytest_cache", "secrets", "models", "recordings", "transcripts",
    "runtime", "dist", "build",
})
EXCLUDED_SUFFIXES = frozenset({
    ".pyc", ".pyo", ".log", ".bin", ".safetensors", ".onnx", ".pt", ".pth",
})
VERSION_PATTERN = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-(?:alpha|beta|rc)\.(?:0|[1-9][0-9]*))?"
)
PATH_PATTERN = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*")


class ReleaseError(ValueError):
    """A release input violates the source archive contract."""


@dataclass(frozen=True, slots=True)
class ReleaseArtifacts:
    version: str
    archive: Path
    checksum: Path
    manifest: Path


def relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (not path.parts or not PATH_PATTERN.fullmatch(value) or path.as_posix() != value
            or any(part in {".", ".."} for part in path.parts)):
        raise ReleaseError("allowlist contains an invalid relative path")
    if (any(part in EXCLUDED_PARTS for part in path.parts)
            or path.suffix in EXCLUDED_SUFFIXES
            or (path.name.startswith(".env") and path.name != ".env.example")):
        raise ReleaseError("allowlist contains excluded runtime or private data")
    if value == MANIFEST:
        raise ReleaseError("release-manifest.json is generated, not a source input")
    return path


def read_source(root: Path, name: str) -> bytes:
    """Reject symlinks at every component, including links inside the source."""
    path = root
    parts = relative_path(name).parts
    try:
        for index, part in enumerate(parts):
            path = path / part
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise ReleaseError("release inputs must not contain symlinks")
            if index < len(parts) - 1 and not stat.S_ISDIR(mode):
                raise ReleaseError("release input parent is not a directory")
        if not stat.S_ISREG(mode):
            raise ReleaseError("release inputs must be regular files")
        return path.read_bytes()
    except OSError as exc:
        raise ReleaseError(f"cannot read required release file: {name}") from exc


def source_files(root: Path) -> tuple[str, dict[str, bytes]]:
    try:
        lines = read_source(root, ALLOWLIST).decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise ReleaseError("allowlist must be UTF-8") from exc
    names = [line for line in lines if line and not line.startswith("#")]
    if len(names) != len(set(names)):
        raise ReleaseError("allowlist contains duplicate paths")
    if "VERSION" not in names or ALLOWLIST not in names:
        raise ReleaseError("allowlist must include VERSION and itself")
    files = {name: read_source(root, name) for name in sorted(names)}
    try:
        version = files["VERSION"].decode("ascii")
    except UnicodeError as exc:
        raise ReleaseError("VERSION must be ASCII") from exc
    if version.endswith("\n"):
        version = version[:-1]
    if len(version) > 64 or not VERSION_PATTERN.fullmatch(version):
        raise ReleaseError("VERSION must be X.Y.Z or X.Y.Z-{alpha,beta,rc}.N")
    return version, files


def archive_bytes(version: str, files: dict[str, bytes]) -> bytes:
    prefix = f"arca-dicta-{version}"
    directories = {prefix}
    for name in files:
        directories.update(
            f"{prefix}/{parent.as_posix()}" for parent in PurePosixPath(name).parents
            if parent.as_posix() != "."
        )
    stream = io.BytesIO()
    # Fixed gzip/tar metadata makes output independent of checkout path and mtimes.
    with gzip.GzipFile(fileobj=stream, mode="wb", filename="", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for name in sorted(directories):
                entry = tarfile.TarInfo(name)
                entry.type = tarfile.DIRTYPE
                entry.mode = 0o755
                archive.addfile(entry)
            for name, content in sorted(files.items()):
                entry = tarfile.TarInfo(f"{prefix}/{name}")
                entry.size = len(content)
                entry.mode = 0o755 if name in EXECUTABLES else 0o644
                archive.addfile(entry, io.BytesIO(content))
    return stream.getvalue()


def build_release(source: Path, output: Path) -> ReleaseArtifacts:
    if source.is_symlink():
        raise ReleaseError("source root must not be a symlink")
    source = source.resolve(strict=True)
    if not source.is_dir():
        raise ReleaseError("source root must be a directory")
    version, files = source_files(source)
    manifest = (json.dumps({
        "version": version,
        "files": {name: hashlib.sha256(content).hexdigest()
                  for name, content in files.items()},
    }, sort_keys=True, indent=2) + "\n").encode("utf-8")
    payload = archive_bytes(version, {**files, MANIFEST: manifest})

    if any(path.is_symlink() for path in (output, *output.parents)):
        raise ReleaseError("output path must not contain symlinks")
    output = output.resolve()
    if output.is_relative_to(source) and not output.is_relative_to(source / "dist"):
        raise ReleaseError("in-tree output must be inside ignored dist/")
    archive = output / f"arca-dicta-{version}.tar.gz"
    checksum = archive.with_name(archive.name + ".sha256")
    manifest_path = output / MANIFEST
    if any(path.exists() or path.is_symlink() for path in (archive, checksum, manifest_path)):
        raise ReleaseError("output artifact already exists; use a fresh output directory")
    output.mkdir(parents=True, exist_ok=True)
    # Exclusive creation also prevents following an output symlink introduced later.
    for path, content in (
        (archive, payload),
        (checksum, f"{hashlib.sha256(payload).hexdigest()}  {archive.name}\n".encode("ascii")),
        (manifest_path, manifest),
    ):
        with path.open("xb") as target:
            target.write(content)
        path.chmod(0o644)
    return ReleaseArtifacts(version, archive, checksum, manifest_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = build_release(args.source, args.output)
    except (ReleaseError, OSError) as exc:
        parser.exit(1, f"Release build failed: {exc}\n")
    print(f"Built {result.archive.name}")
    print(result.checksum.read_text(encoding="ascii"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
