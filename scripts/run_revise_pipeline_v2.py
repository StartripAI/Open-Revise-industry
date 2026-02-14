#!/usr/bin/env python3
"""
Run-scoped revise pipeline with artifact governance.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

from run_artifact_utils import (
    ArtifactRecord,
    DEFAULT_MARKER,
    RunContext,
    ensure_non_empty_marker,
    ensure_run_layout,
    is_valid_run_id,
    make_run_id,
    safe_copy2,
    sha256_file,
    to_iso_z,
    utc_now,
    write_tsv,
)
from update_run_index import upsert_run_record


SYNC_FIELDS = [
    "marker",
    "run_id",
    "phase",
    "file",
    "role",
    "status",
    "sha256",
    "size_bytes",
    "created_at",
]

DELETED_FIELDS = ["marker", "run_id", "reason", "status_before", "status_after", "path", "deleted_at"]
ARTIFACT_FIELDS = [
    "marker",
    "run_id",
    "artifact_type",
    "path",
    "producer_script",
    "upstream_sources",
    "retention_tier",
]

POLICY_NAME = "hot30_cold180"


def _run(cmd: List[str]) -> int:
    print("+", " ".join(cmd))
    proc = subprocess.run(cmd, check=False)
    return proc.returncode


def _acquire_single_run_lock(lock_path: Path) -> int | None:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return None
    os.write(fd, f"{os.getpid()}\n".encode("utf-8"))
    return fd


def _release_single_run_lock(lock_path: Path, fd: int | None) -> None:
    if fd is None:
        return
    try:
        os.close(fd)
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def _must_not_exist(path: Path) -> None:
    if path.exists():
        raise SystemExit(f"Refusing to overwrite existing artifact: {path}")


def _file_meta(path: Path) -> Dict[str, str]:
    return {"sha256": sha256_file(path), "size": str(path.stat().st_size)}


def _append_sync_row(
    rows: List[Dict[str, str]],
    marker: str,
    run_id: str,
    phase: str,
    file_path: Path,
    role: str,
    status: str,
    created_at: str,
) -> None:
    meta = _file_meta(file_path)
    rows.append(
        {
            "marker": marker,
            "run_id": run_id,
            "phase": phase,
            "file": str(file_path),
            "role": role,
            "status": status,
            "sha256": meta["sha256"],
            "size_bytes": meta["size"],
            "created_at": created_at,
        }
    )


def _parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Run revise pipeline with run-scoped governance.")
    parser.add_argument("--input-docx", required=True, type=Path)
    parser.add_argument("--run-id", type=str, default=None)
    parser.add_argument("--run-dir", type=Path, default=None)
    parser.add_argument("--marker", default=DEFAULT_MARKER)
    parser.add_argument("--manifest-dir", type=Path, default=repo_root / "reports")
    parser.add_argument("--retention-policy", default=POLICY_NAME)
    parser.add_argument("--purge-expired", action="store_true")
    parser.add_argument(
        "--source-config",
        type=Path,
        default=repo_root / "config" / "revise_sources.json",
    )
    parser.add_argument(
        "--patch-spec",
        type=Path,
        required=True,
        help="JSON spec containing generic revision patches and source footnote texts.",
    )
    parser.add_argument("--author", default="Codex")
    parser.add_argument(
        "--date",
        default=dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    )
    parser.add_argument(
        "--allow-required-fail",
        action="store_true",
        help="Allow revision even when required source gate fails (not recommended).",
    )
    parser.add_argument(
        "--ca-bundle",
        type=Path,
        default=None,
        help="Optional CA bundle PEM path for source-gate HTTP/TLS validation.",
    )
    parser.add_argument(
        "--allow-insecure-tls",
        action="store_true",
        help="Disable TLS certificate verification for source checks (diagnostic use only).",
    )
    parser.add_argument(
        "--allow-incremental",
        action="store_true",
        help="Allow using an input DOCX that already contains tracked revisions.",
    )
    parser.add_argument(
        "--output-docx",
        type=Path,
        default=None,
        help="Optional extra copy destination for revised DOCX.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    ensure_non_empty_marker(args.marker)

    repo_root = Path(__file__).resolve().parents[1]
    scripts_dir = Path(__file__).resolve().parent
    runs_root = repo_root / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)
    (repo_root / "archive").mkdir(parents=True, exist_ok=True)
    args.manifest_dir.mkdir(parents=True, exist_ok=True)

    run_id = args.run_id or make_run_id()
    if not is_valid_run_id(run_id):
        print(f"Invalid run_id format: {run_id}", file=sys.stderr)
        return 2

    expected_run_dir = runs_root / run_id
    run_dir = args.run_dir.resolve() if args.run_dir else expected_run_dir
    if run_dir != expected_run_dir.resolve():
        print(f"run_dir must be exactly: {expected_run_dir}", file=sys.stderr)
        return 2
    if run_dir.exists():
        print(f"Run directory already exists (run_id reuse not allowed): {run_dir}", file=sys.stderr)
        return 2
    if not args.patch_spec.exists():
        print(f"Patch spec not found: {args.patch_spec}", file=sys.stderr)
        return 2

    lock_path = repo_root / ".pipeline.lock"
    lock_fd = _acquire_single_run_lock(lock_path)
    if lock_fd is None:
        print(
            f"Another pipeline process appears to be running. Lock file exists: {lock_path}",
            file=sys.stderr,
        )
        return 2

    started = utc_now()
    started_at = to_iso_z(started)
    context = RunContext(run_id=run_id, marker=args.marker, run_dir=run_dir, started_at=started_at)
    run_index = repo_root / "reports" / "run_index.tsv"
    started_record_written = False
    finished_status = "FAILED_INTERNAL"
    finished_notes = ""
    artifacts: List[ArtifactRecord] = []

    intake_copy = run_dir / "intake" / f"input_{run_id}.docx"
    source_report = run_dir / "reports" / f"source_gate_report_{run_id}.json"
    run_context_file = run_dir / "reports" / f"run_context_{run_id}.json"
    revised_docx = run_dir / "revision" / f"revised_{run_id}.docx"
    revision_audit = run_dir / "revision" / f"revision_change_audit_{run_id}.csv"
    q_source_map = run_dir / "reports" / f"q_source_map_{run_id}.csv"
    claim_verdicts = run_dir / "verify" / f"claim_verdicts_{run_id}.jsonl"
    patch_spec_copy = run_dir / "scope" / f"patch_spec_{run_id}.json"
    sync_manifest = run_dir / "manifests" / f"revise_sync_manifest_{run_id}.tsv"
    deleted_manifest = run_dir / "manifests" / f"deleted_docx_manifest_{run_id}.tsv"
    artifact_manifest = run_dir / "manifests" / f"artifact_manifest_{run_id}.tsv"

    try:
        ensure_run_layout(run_dir)
        safe_copy2(args.input_docx, intake_copy)
        safe_copy2(args.patch_spec, patch_spec_copy)

        for target in [source_report, run_context_file, revised_docx, revision_audit, q_source_map, claim_verdicts]:
            _must_not_exist(target)

        for target in [sync_manifest, deleted_manifest, artifact_manifest]:
            _must_not_exist(target)

        upsert_run_record(
            run_index,
            {
                "marker": args.marker,
                "run_id": run_id,
                "status": "RUNNING",
                "run_dir": str(run_dir),
                "started_at": context.started_at,
                "finished_at": "",
                "retention_policy": args.retention_policy,
                "manifest_sync": str(sync_manifest),
                "manifest_deleted": str(deleted_manifest),
                "manifest_artifact": str(artifact_manifest),
                "source_gate_report": str(source_report),
                "revised_docx": str(revised_docx),
                "q_source_map": str(q_source_map),
                "revision_change_audit": str(revision_audit),
                "archive_path": "",
                "notes": "",
            },
        )
        started_record_written = True

        run_context_file.write_text(
            json.dumps(
                {
                    "run_id": context.run_id,
                    "marker": context.marker,
                    "run_dir": str(context.run_dir),
                    "started_at": context.started_at,
                    "policy_version": context.policy_version,
                    "retention_policy": args.retention_policy,
                    "patch_spec": str(patch_spec_copy),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        finished_status = "SUCCEEDED"
        source_check_rc = _run(
            [
                sys.executable,
                str(scripts_dir / "check_revise_sources.py"),
                "--config",
                str(args.source_config),
                "--output-json",
                str(source_report),
                "--run-dir",
                str(run_dir),
                "--run-id",
                run_id,
            ]
            + (["--ca-bundle", str(args.ca_bundle)] if args.ca_bundle is not None else [])
            + (["--allow-insecure-tls"] if args.allow_insecure_tls else [])
        )
        if source_check_rc != 0 and not args.allow_required_fail:
            finished_status = "FAILED_GATE"
            finished_notes = "required source gate failed"
        else:
            revise_cmd = [
                sys.executable,
                str(scripts_dir / "revise_docx.py"),
                "--input-docx",
                str(intake_copy),
                "--output-docx",
                str(revised_docx),
                "--audit-csv",
                str(revision_audit),
                "--patch-spec",
                str(patch_spec_copy),
                "--author",
                args.author,
                "--date",
                args.date,
                "--run-dir",
                str(run_dir),
                "--run-id",
                run_id,
            ]
            if args.allow_incremental:
                revise_cmd.append("--allow-incremental")
            revise_rc = _run(revise_cmd)
            if revise_rc != 0:
                finished_status = "FAILED_REVISE"
                finished_notes = f"revise_docx failed with code {revise_rc}"
            else:
                qmap_rc = _run(
                    [
                        sys.executable,
                        str(scripts_dir / "build_q_source_map.py"),
                        "--input-docx",
                        str(revised_docx),
                        "--output-csv",
                        str(q_source_map),
                        "--run-dir",
                        str(run_dir),
                        "--run-id",
                        run_id,
                    ]
                )
                if qmap_rc != 0:
                    finished_status = "FAILED_QMAP"
                    finished_notes = f"build_q_source_map failed with code {qmap_rc}"

        now_iso = to_iso_z(utc_now())

        claim_verdicts.write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "status": "placeholder",
                    "message": "claim verifier not yet integrated; reserved artifact path",
                    "created_at": now_iso,
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

        artifact_rows: List[Dict[str, str]] = []
        sync_rows: List[Dict[str, str]] = []

        def add_artifact(
            artifact_type: str,
            path: Path,
            phase: str,
            producer: str,
            upstream_sources: str,
            retention_tier: str,
            role: str,
        ) -> None:
            if not path.exists():
                return
            meta = _file_meta(path)
            artifacts.append(
                ArtifactRecord(
                    artifact_type=artifact_type,
                    path=path,
                    hash=meta["sha256"],
                    size=int(meta["size"]),
                    retention_tier=retention_tier,
                    phase=phase,
                )
            )
            artifact_rows.append(
                {
                    "marker": args.marker,
                    "run_id": run_id,
                    "artifact_type": artifact_type,
                    "path": str(path),
                    "producer_script": producer,
                    "upstream_sources": upstream_sources,
                    "retention_tier": retention_tier,
                }
            )
            _append_sync_row(
                sync_rows,
                marker=args.marker,
                run_id=run_id,
                phase=phase,
                file_path=path,
                role=role,
                status="created",
                created_at=now_iso,
            )

        add_artifact(
            "input_docx_copy",
            intake_copy,
            "intake",
            "run_revise_pipeline_v2.py",
            str(args.input_docx),
            "HOT",
            "input",
        )
        add_artifact(
            "patch_spec_copy",
            patch_spec_copy,
            "scope",
            "run_revise_pipeline_v2.py",
            str(args.patch_spec),
            "PERMANENT",
            "patch_spec",
        )
        add_artifact(
            "run_context",
            run_context_file,
            "reports",
            "run_revise_pipeline_v2.py",
            "",
            "PERMANENT",
            "run_context",
        )
        add_artifact(
            "source_gate_report",
            source_report,
            "gate",
            "check_revise_sources.py",
            str(args.source_config),
            "HOT",
            "source_gate_report",
        )
        add_artifact(
            "revised_docx",
            revised_docx,
            "revise",
            "revise_docx.py",
            str(patch_spec_copy),
            "PERMANENT",
            "revised_docx",
        )
        add_artifact(
            "revision_change_audit",
            revision_audit,
            "revise",
            "revise_docx.py",
            str(revised_docx),
            "PERMANENT",
            "change_audit",
        )
        add_artifact(
            "q_source_map",
            q_source_map,
            "reports",
            "build_q_source_map.py",
            str(revised_docx),
            "PERMANENT",
            "q_source_map",
        )
        add_artifact(
            "claim_verdicts",
            claim_verdicts,
            "verify",
            "run_revise_pipeline_v2.py",
            "",
            "HOT",
            "claim_verdicts",
        )

        write_tsv(
            deleted_manifest,
            DELETED_FIELDS,
            [
                {
                    "marker": args.marker,
                    "run_id": run_id,
                    "reason": "no_deletions",
                    "status_before": "n/a",
                    "status_after": "n/a",
                    "path": "n/a",
                    "deleted_at": now_iso,
                }
            ],
        )
        write_tsv(artifact_manifest, ARTIFACT_FIELDS, artifact_rows)

        _append_sync_row(
            sync_rows,
            marker=args.marker,
            run_id=run_id,
            phase="manifest",
            file_path=deleted_manifest,
            role="deleted_manifest",
            status="created",
            created_at=now_iso,
        )
        _append_sync_row(
            sync_rows,
            marker=args.marker,
            run_id=run_id,
            phase="manifest",
            file_path=artifact_manifest,
            role="artifact_manifest",
            status="created",
            created_at=now_iso,
        )
        write_tsv(sync_manifest, SYNC_FIELDS, sync_rows)

        if args.manifest_dir.resolve() != (run_dir / "manifests").resolve():
            for src in [sync_manifest, deleted_manifest, artifact_manifest]:
                dst = args.manifest_dir / src.name
                safe_copy2(src, dst)

        if args.output_docx is not None and revised_docx.exists():
            safe_copy2(revised_docx, args.output_docx)

        if args.purge_expired:
            hk_rc = _run(
                [
                    sys.executable,
                    str(scripts_dir / "housekeeping.py"),
                    "--marker",
                    args.marker,
                    "--retention-policy",
                    args.retention_policy,
                ]
            )
            if hk_rc != 0 and finished_status == "SUCCEEDED":
                finished_status = "FAILED_HOUSEKEEPING"
                finished_notes = f"housekeeping failed with code {hk_rc}"

    except Exception as exc:
        finished_status = "FAILED_INTERNAL"
        if not finished_notes:
            finished_notes = f"internal error: {exc.__class__.__name__}: {exc}"
    finally:
        if started_record_written:
            finished_at = to_iso_z(utc_now())
            upsert_run_record(
                run_index,
                {
                    "marker": args.marker,
                    "run_id": run_id,
                    "status": finished_status,
                    "run_dir": str(run_dir),
                    "started_at": started_at,
                    "finished_at": finished_at,
                    "retention_policy": args.retention_policy,
                    "manifest_sync": str(sync_manifest),
                    "manifest_deleted": str(deleted_manifest),
                    "manifest_artifact": str(artifact_manifest),
                    "source_gate_report": str(source_report),
                    "revised_docx": str(revised_docx),
                    "q_source_map": str(q_source_map),
                    "revision_change_audit": str(revision_audit),
                    "archive_path": "",
                    "notes": finished_notes,
                },
            )
        _release_single_run_lock(lock_path, lock_fd)

    print(f"Run ID: {run_id}")
    print(f"Run dir: {run_dir}")
    print(f"Status: {finished_status}")
    print(f"Artifacts indexed: {len(artifacts)}")
    print(f"Sync manifest: {sync_manifest}")
    print(f"Deleted manifest: {deleted_manifest}")
    print(f"Artifact manifest: {artifact_manifest}")
    print(f"Run index: {run_index}")
    return 0 if finished_status == "SUCCEEDED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
