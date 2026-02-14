#!/usr/bin/env python3
"""
Maintain global run index.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, List

from run_artifact_utils import read_tsv, write_tsv


RUN_INDEX_FIELDS: List[str] = [
    "marker",
    "run_id",
    "status",
    "run_dir",
    "started_at",
    "finished_at",
    "retention_policy",
    "manifest_sync",
    "manifest_deleted",
    "manifest_artifact",
    "source_gate_report",
    "revised_docx",
    "q_source_map",
    "revision_change_audit",
    "archive_path",
    "notes",
]


def upsert_run_record(index_path: Path, record: Dict[str, str]) -> None:
    rows = read_tsv(index_path)
    index: Dict[str, Dict[str, str]] = {}
    order: List[str] = []
    for row in rows:
        rid = row.get("run_id", "")
        if not rid:
            continue
        index[rid] = row
        order.append(rid)

    rid = record.get("run_id", "")
    if not rid:
        raise ValueError("run_id is required in run index record")
    if rid in index:
        merged = dict(index[rid])
        merged.update(record)
        index[rid] = merged
    else:
        index[rid] = {k: record.get(k, "") for k in RUN_INDEX_FIELDS}
        order.append(rid)

    output: List[Dict[str, str]] = []
    seen = set()
    for key in order:
        if key in seen or key not in index:
            continue
        seen.add(key)
        output.append(index[key])

    write_tsv(index_path, RUN_INDEX_FIELDS, output)


def main() -> int:
    parser = argparse.ArgumentParser(description="Upsert one run record into reports/run_index.tsv")
    parser.add_argument(
        "--index",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "reports" / "run_index.tsv",
    )
    parser.add_argument("--marker", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--status", required=True)
    parser.add_argument("--run-dir", default="")
    parser.add_argument("--started-at", default="")
    parser.add_argument("--finished-at", default="")
    parser.add_argument("--retention-policy", default="")
    parser.add_argument("--manifest-sync", default="")
    parser.add_argument("--manifest-deleted", default="")
    parser.add_argument("--manifest-artifact", default="")
    parser.add_argument("--source-gate-report", default="")
    parser.add_argument("--revised-docx", default="")
    parser.add_argument("--q-source-map", default="")
    parser.add_argument("--revision-change-audit", default="")
    parser.add_argument("--archive-path", default="")
    parser.add_argument("--notes", default="")
    args = parser.parse_args()

    upsert_run_record(
        args.index,
        {
            "marker": args.marker,
            "run_id": args.run_id,
            "status": args.status,
            "run_dir": args.run_dir,
            "started_at": args.started_at,
            "finished_at": args.finished_at,
            "retention_policy": args.retention_policy,
            "manifest_sync": args.manifest_sync,
            "manifest_deleted": args.manifest_deleted,
            "manifest_artifact": args.manifest_artifact,
            "source_gate_report": args.source_gate_report,
            "revised_docx": args.revised_docx,
            "q_source_map": args.q_source_map,
            "revision_change_audit": args.revision_change_audit,
            "archive_path": args.archive_path,
            "notes": args.notes,
        },
    )
    print(f"Updated run index: {args.index}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

