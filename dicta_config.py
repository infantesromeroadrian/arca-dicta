"""Explicit local paths for the extracted development runtime; no discovery."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_UPSTREAM_LIB = "/usr/lib/hyprwhspr/lib"
DEFAULT_UPSTREAM_BIN = "/usr/bin/hyprwhspr"
MODEL_FILES = ("model.bin", "config.json", "tokenizer.json")


class ConfigurationError(ValueError):
    """A required local dependency is absent or an explicit path is invalid."""


def _absolute_path(environment: Mapping[str, str], name: str,
                   default: str | None = None) -> Path:
    value = environment.get(name, default)
    if not value:
        raise ConfigurationError(f"{name} must be set to an absolute path")
    path = Path(value)
    if not path.is_absolute():
        raise ConfigurationError(f"{name} must be an absolute path")
    return path


def upstream_library(environment: Mapping[str, str] | None = None) -> Path:
    return _absolute_path(os.environ if environment is None else environment,
                          "ARCA_DICTA_UPSTREAM_LIB", DEFAULT_UPSTREAM_LIB)


def upstream_binary(environment: Mapping[str, str] | None = None) -> Path:
    return _absolute_path(os.environ if environment is None else environment,
                          "ARCA_DICTA_UPSTREAM_BIN", DEFAULT_UPSTREAM_BIN)


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    upstream_lib: Path
    worker_python: Path
    model_dir: Path


def load_runtime_config(environment: Mapping[str, str] | None = None) -> RuntimeConfig:
    """Validate dependencies before importing the desktop application."""
    environment = os.environ if environment is None else environment
    upstream = upstream_library(environment)
    worker = _absolute_path(environment, "ARCA_DICTA_WORKER_PYTHON")
    model = _absolute_path(environment, "ARCA_DICTA_MODEL_DIR")
    if not upstream.is_dir():
        raise ConfigurationError("ARCA_DICTA_UPSTREAM_LIB must be an existing directory")
    if not worker.is_file() or not os.access(worker, os.X_OK):
        raise ConfigurationError("ARCA_DICTA_WORKER_PYTHON must be an executable file")
    if not model.is_dir() or not all((model / name).is_file() for name in MODEL_FILES):
        raise ConfigurationError("ARCA_DICTA_MODEL_DIR must contain a complete local model")
    return RuntimeConfig(upstream, worker, model)
