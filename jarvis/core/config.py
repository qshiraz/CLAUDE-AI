"""Configuration loader — merges config.yaml with an optional config.local.yaml override."""

import os
from pathlib import Path
from typing import Any

import yaml


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def load_config(root: Path | None = None) -> dict[str, Any]:
    if root is None:
        root = Path(__file__).parent.parent.parent

    base_path = root / "config.yaml"
    local_path = root / "config.local.yaml"

    if not base_path.exists():
        raise FileNotFoundError(f"config.yaml not found at {base_path}")

    with open(base_path) as f:
        cfg = yaml.safe_load(f) or {}

    if local_path.exists():
        with open(local_path) as f:
            local = yaml.safe_load(f) or {}
        cfg = _deep_merge(cfg, local)

    return cfg
