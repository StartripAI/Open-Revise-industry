#!/usr/bin/env python3
"""
Shared utilities for run-scoped artifact management.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import os
import re
import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List


DEFAULT_MARKER = "REVISE_DOCX_PURGED_20260214"
DEFAULT_POLICY_VERSION = "1.0"
RUN_ID_RE = re.compile(r"^\d{8}T\d{6}Z_[A-Za-z0-9]{6}$")

RUN_SUBDIRS: List[str] = [
    "intake",
    "sources_raw",
    "sources_parsed",
    "scope",
    "verify",
    "revision",
    "reports",
    "manifests",
    "tmp",
]


@dataclass(frozen=True)
class RunContext:
    run_id: str
    marker: str
    run_dir: Path
    started_at: str
    policy_version: str = DEFAULT_POLICY_VERSION


@dataclass(frozen=True)
class ArtifactRecord:
    artifact_type: str
    path: Path
    hash: str
    size: int
    retention_tier: str
    phase: str


@dataclass(frozen=True)
class PurgeRecord:
    path: Path
    reason: str
    pre_state: str
    post_state: str
    approved_by: str


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def to_iso_z(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def make_run_id(now: dt.datetime | None = None) -> str:
    current = now or utc_now()
    return f"{current.strftime('%Y%m%dT%H%M%SZ')}_{secrets.token_hex(3).upper()}"


def is_valid_run_id(run_id: str) -> bool:
    return RUN_ID_RE.fullmatch(run_id) is not None


def parse_run_id_time(run_id: str) -> dt.datetime:
    if not is_valid_run_id(run_id):
        raise ValueError(f"Invalid run_id format: {run_id}")
    stamp = run_id.split("_", 1)[0]
    parsed = dt.datetime.strptime(stamp, "%Y%m%dT%H%M%SZ")
    return parsed.replace(tzinfo=dt.timezone.utc)


def ensure_non_empty_marker(marker: str) -> None:
    if not marker or not marker.strip():
        raise ValueError("Marker must be non-empty")


def ensure_run_layout(run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=False)
    for name in RUN_SUBDIRS:
        (run_dir / name).mkdir(parents=True, exist_ok=True)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_tsv(path: Path, fieldnames: List[str], rows: Iterable[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t")
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row.get(k, "") for k in fieldnames})
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def read_tsv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        return list(reader)


def safe_copy2(src: Path, dst: Path) -> None:
    import shutil

    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        raise FileExistsError(f"Refusing to overwrite existing file: {dst}")
    shutil.copy2(src, dst)
