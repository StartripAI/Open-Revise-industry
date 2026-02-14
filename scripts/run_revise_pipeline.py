#!/usr/bin/env python3
"""
Top-level revise pipeline:
1) source gate check
2) tracked DOCX revision
3) Q->source mapping export
"""

from __future__ import annotations

import argparse
import datetime as dt
import subprocess
import sys
from pathlib import Path
from run_artifact_utils import is_valid_run_id


def _run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    proc = subprocess.run(cmd, check=False)
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run revise pipeline with hard source gate.")
    parser.add_argument("--input-docx", required=True, type=Path)
    parser.add_argument("--output-docx", type=Path, default=None)
    parser.add_argument("--author", default="Codex")
    parser.add_argument(
        "--date",
        default=dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    )
    parser.add_argument(
        "--source-config",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "config" / "revise_sources.json",
    )
    parser.add_argument(
        "--patch-spec",
        type=Path,
        required=True,
        help="JSON spec containing generic revision patches and source footnotes.",
    )
    parser.add_argument(
        "--source-report-json",
        type=Path,
        default=None,
    )
    parser.add_argument(
        "--q-map-csv",
        type=Path,
        default=None,
    )
    parser.add_argument(
        "--run-dir",
        type=Path,
        default=None,
        help="Run directory root. If used with --run-id, defaults output artifacts to run-scoped paths.",
    )
    parser.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="Run identifier in format YYYYMMDDTHHMMSSZ_<6-char>.",
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
        help="Allow revising from an input DOCX that already contains tracked changes.",
    )
    args = parser.parse_args()

    if args.run_id is not None and not is_valid_run_id(args.run_id):
        parser.error(f"Invalid --run-id format: {args.run_id}")
    if not args.patch_spec.exists():
        parser.error(f"patch spec not found: {args.patch_spec}")

    if args.run_dir is not None and args.run_id is None:
        parser.error("--run-id is required when --run-dir is provided")

    repo_root = Path(__file__).resolve().parents[1]
    if args.run_dir is not None:
        args.output_docx = args.output_docx or (args.run_dir / "revision" / f"revised_{args.run_id}.docx")
        args.source_report_json = args.source_report_json or (
            args.run_dir / "reports" / f"source_gate_report_{args.run_id}.json"
        )
        args.q_map_csv = args.q_map_csv or (args.run_dir / "reports" / f"q_source_map_{args.run_id}.csv")
    else:
        if args.output_docx is None:
            parser.error("--output-docx is required unless --run-dir and --run-id are provided")
        args.source_report_json = args.source_report_json or (repo_root / "reports" / "source_gate_report.json")
        args.q_map_csv = args.q_map_csv or (repo_root / "reports" / "q_source_map.csv")

    base = Path(__file__).resolve().parent
    check_script = base / "check_revise_sources.py"
    revise_script = base / "revise_docx.py"
    qmap_script = base / "build_q_source_map.py"

    check_cmd = [
        sys.executable,
        str(check_script),
        "--config",
        str(args.source_config),
        "--output-json",
        str(args.source_report_json),
    ]
    if args.ca_bundle is not None:
        check_cmd += ["--ca-bundle", str(args.ca_bundle)]
    if args.allow_insecure_tls:
        check_cmd.append("--allow-insecure-tls")
    if args.run_dir is not None:
        check_cmd += ["--run-dir", str(args.run_dir), "--run-id", args.run_id]
    check_proc = subprocess.run(check_cmd, check=False)
    if check_proc.returncode != 0 and not args.allow_required_fail:
        print("Source gate failed. Revision aborted.")
        return check_proc.returncode

    revise_cmd = [
        sys.executable,
        str(revise_script),
        "--input-docx",
        str(args.input_docx),
        "--output-docx",
        str(args.output_docx),
        "--patch-spec",
        str(args.patch_spec),
        "--author",
        args.author,
        "--date",
        args.date,
    ]
    if args.run_dir is not None:
        revise_cmd += ["--run-dir", str(args.run_dir), "--run-id", args.run_id]
    if args.allow_incremental:
        revise_cmd.append("--allow-incremental")
    _run(revise_cmd)

    _run(
        [
            sys.executable,
            str(qmap_script),
            "--input-docx",
            str(args.output_docx),
            "--output-csv",
            str(args.q_map_csv),
        ]
    )

    print(f"Revision output: {args.output_docx}")
    print(f"Source gate report: {args.source_report_json}")
    print(f"Q-source map: {args.q_map_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
