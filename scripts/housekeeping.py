#!/usr/bin/env python3
"""
Retention housekeeping for run artifacts.
"""

from __future__ import annotations

import argparse
import shutil
import tarfile
from pathlib import Path
from typing import Dict, List, Set

from run_artifact_utils import (
    DEFAULT_MARKER,
    PurgeRecord,
    is_valid_run_id,
    parse_run_id_time,
    read_tsv,
    safe_copy2,
    to_iso_z,
    utc_now,
    write_tsv,
)
from update_run_index import upsert_run_record


DELETED_FIELDS = ["marker", "run_id", "reason", "status_before", "status_after", "path", "deleted_at"]


def _collect_run_ids(
    runs_root: Path,
    archive_dir: Path,
    run_index_path: Path,
    only_run_id: str | None,
) -> List[str]:
    ids: Set[str] = set()

    if only_run_id:
        if is_valid_run_id(only_run_id):
            ids.add(only_run_id)
        return sorted(ids)

    if runs_root.exists():
        for p in runs_root.iterdir():
            if p.is_dir() and is_valid_run_id(p.name):
                ids.add(p.name)

    if archive_dir.exists():
        for p in archive_dir.glob("*.tar.gz"):
            run_id = p.name[: -len(".tar.gz")]
            if is_valid_run_id(run_id):
                ids.add(run_id)

    for row in read_tsv(run_index_path):
        run_id = row.get("run_id", "")
        if is_valid_run_id(run_id):
            ids.add(run_id)

    return sorted(ids)


def _archive_run(run_dir: Path, archive_path: Path, dry_run: bool) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    if archive_path.exists() or dry_run:
        return
    with tarfile.open(archive_path, "w:gz") as tf:
        tf.add(run_dir, arcname=run_dir.name)


def _permanent_files(run_dir: Path) -> List[Path]:
    out: List[Path] = []
    out.extend((run_dir / "manifests").glob("*.tsv"))
    out.extend((run_dir / "reports").glob("q_source_map_*.csv"))
    out.extend((run_dir / "revision").glob("revision_change_audit_*.csv"))
    out.extend((run_dir / "revision").glob("revised_*.docx"))
    return [p for p in out if p.exists() and p.is_file()]


def _copy_permanent(run_dir: Path, reports_dir: Path, dry_run: bool) -> Path:
    dest_root = reports_dir / "permanent" / run_dir.name
    if dry_run:
        return dest_root
    dest_root.mkdir(parents=True, exist_ok=True)
    for src in _permanent_files(run_dir):
        dst = dest_root / src.name
        if dst.exists():
            continue
        safe_copy2(src, dst)
    return dest_root


def _purge_non_key_dirs(run_dir: Path, dry_run: bool) -> List[Path]:
    removed: List[Path] = []
    for name in ["intake", "sources_raw", "sources_parsed", "scope", "verify", "tmp"]:
        target = run_dir / name
        if not target.exists():
            continue
        removed.append(target)
        if not dry_run:
            shutil.rmtree(target)
    return removed


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Retention housekeeping for revise run artifacts.")
    parser.add_argument("--runs-root", type=Path, default=repo_root / "runs")
    parser.add_argument("--archive-dir", type=Path, default=repo_root / "archive")
    parser.add_argument("--reports-dir", type=Path, default=repo_root / "reports")
    parser.add_argument("--marker", default=DEFAULT_MARKER)
    parser.add_argument("--retention-policy", default="hot30_cold180")
    parser.add_argument("--hot-days", type=int, default=30)
    parser.add_argument("--cold-days", type=int, default=180)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--approved-by", default="housekeeping.py")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.run_id is not None and not is_valid_run_id(args.run_id):
        print(f"Invalid --run-id: {args.run_id}")
        return 2

    now = utc_now()
    now_iso = to_iso_z(now)
    run_index = args.reports_dir / "run_index.tsv"
    run_ids = _collect_run_ids(args.runs_root, args.archive_dir, run_index, args.run_id)

    deleted_rows: List[Dict[str, str]] = []
    purge_records: List[PurgeRecord] = []

    for run_id in run_ids:
        run_dir = args.runs_root / run_id
        archive_path = args.archive_dir / f"{run_id}.tar.gz"
        started = parse_run_id_time(run_id)
        age_days = (now - started).days

        if args.hot_days < age_days <= args.cold_days:
            if run_dir.exists():
                _archive_run(run_dir, archive_path, args.dry_run)
                _copy_permanent(run_dir, args.reports_dir, args.dry_run)
                purge_records.append(
                    PurgeRecord(
                        path=run_dir,
                        reason="cold_archive_migration",
                        pre_state="exists",
                        post_state="archived_or_removed",
                        approved_by=args.approved_by,
                    )
                )
                deleted_rows.append(
                    {
                        "marker": args.marker,
                        "run_id": run_id,
                        "reason": "cold_archive_migration",
                        "status_before": "exists",
                        "status_after": "archived_or_removed",
                        "path": str(run_dir),
                        "deleted_at": now_iso,
                    }
                )
                if not args.dry_run:
                    shutil.rmtree(run_dir)
                upsert_run_record(
                    run_index,
                    {
                        "marker": args.marker,
                        "run_id": run_id,
                        "status": "COLD_ARCHIVED",
                        "archive_path": str(archive_path),
                        "notes": f"migrated to archive at {now_iso}",
                        "retention_policy": args.retention_policy,
                    },
                )
            continue

        if age_days > args.cold_days:
            run_has_purge = False

            if run_dir.exists():
                removed = _purge_non_key_dirs(run_dir, args.dry_run)
                for item in removed:
                    run_has_purge = True
                    purge_records.append(
                        PurgeRecord(
                            path=item,
                            reason="expired_non_key_purge",
                            pre_state="exists",
                            post_state="deleted_or_missing",
                            approved_by=args.approved_by,
                        )
                    )
                    deleted_rows.append(
                        {
                            "marker": args.marker,
                            "run_id": run_id,
                            "reason": "expired_non_key_purge",
                            "status_before": "exists",
                            "status_after": "deleted_or_missing",
                            "path": str(item),
                            "deleted_at": now_iso,
                        }
                    )

            if archive_path.exists():
                run_has_purge = True
                purge_records.append(
                    PurgeRecord(
                        path=archive_path,
                        reason="expired_archive_purge",
                        pre_state="exists",
                        post_state="deleted_or_missing",
                        approved_by=args.approved_by,
                    )
                )
                deleted_rows.append(
                    {
                        "marker": args.marker,
                        "run_id": run_id,
                        "reason": "expired_archive_purge",
                        "status_before": "exists",
                        "status_after": "deleted_or_missing",
                        "path": str(archive_path),
                        "deleted_at": now_iso,
                    }
                )
                if not args.dry_run:
                    archive_path.unlink()

            if run_has_purge:
                upsert_run_record(
                    run_index,
                    {
                        "marker": args.marker,
                        "run_id": run_id,
                        "status": "EXPIRED_NONKEY_PURGED",
                        "notes": f"expired non-key artifacts purged at {now_iso}",
                        "retention_policy": args.retention_policy,
                    },
                )

    deleted_manifest = args.reports_dir / f"deleted_docx_manifest_{now.strftime('%Y%m%dT%H%M%SZ')}.tsv"
    if not deleted_rows:
        deleted_rows.append(
            {
                "marker": args.marker,
                "run_id": args.run_id or "ALL",
                "reason": "no_deletions",
                "status_before": "n/a",
                "status_after": "n/a",
                "path": "n/a",
                "deleted_at": now_iso,
            }
        )
    if not args.dry_run:
        write_tsv(deleted_manifest, DELETED_FIELDS, deleted_rows)

    print(f"Processed runs: {len(run_ids)}")
    print(f"Purge actions: {len(purge_records)}")
    if args.dry_run:
        print("Dry-run mode: no file changes were made")
    else:
        print(f"Deleted manifest: {deleted_manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
