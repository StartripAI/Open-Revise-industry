#!/usr/bin/env python3
"""
Source gate for revise workflows.

Gate rule:
- Required sources must be reachable and contain required evidence tokens.
- Optional sources are checked and reported, but do not block the run.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from pypdf import PdfReader
from run_artifact_utils import is_valid_run_id


@dataclass
class CheckResult:
    source_id: str
    tier: str
    ok: bool
    reachable: bool
    matched_tokens: int
    total_tokens: int
    detail: str


def _fetch_url_text(
    url: str,
    timeout: int = 25,
    ca_bundle: str | None = None,
    allow_insecure_tls: bool = False,
) -> str:
    return _fetch_url_bytes(
        url,
        timeout=timeout,
        ca_bundle=ca_bundle,
        allow_insecure_tls=allow_insecure_tls,
    ).decode("utf-8", errors="ignore")


def _fetch_url_bytes(
    url: str,
    timeout: int = 25,
    ca_bundle: str | None = None,
    allow_insecure_tls: bool = False,
) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "revise-source-check/1.0"})
    if allow_insecure_tls:
        context = ssl._create_unverified_context()
    elif ca_bundle:
        context = ssl.create_default_context(cafile=ca_bundle)
    else:
        context = None
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
            return resp.read()
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        # Fallback for environments where Python's trust store is out of sync
        # with system certificates while curl can still validate TLS properly.
        curl_cmd = ["curl", "-fsSL", "--max-time", str(timeout), "--retry", "1"]
        if ca_bundle:
            curl_cmd.extend(["--cacert", ca_bundle])
        if allow_insecure_tls:
            curl_cmd.append("-k")
        curl_cmd.append(url)
        proc = subprocess.run(curl_cmd, check=False, capture_output=True)
        if proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", errors="ignore").strip()
            raise urllib.error.URLError(stderr or f"curl failed with exit code {proc.returncode}")
        return proc.stdout


def _fetch_remote_pdf_text(
    url: str,
    timeout: int = 30,
    ca_bundle: str | None = None,
    allow_insecure_tls: bool = False,
) -> str:
    payload = _fetch_url_bytes(
        url,
        timeout=timeout,
        ca_bundle=ca_bundle,
        allow_insecure_tls=allow_insecure_tls,
    )

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as tmp:
        tmp.write(payload)
        tmp.flush()
        reader = PdfReader(tmp.name)
        return "\n".join((page.extract_text() or "") for page in reader.pages)


def _load_local_pdf_text(path: str) -> str:
    reader = PdfReader(path)
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _normalize_for_match(text: str) -> str:
    # Join words split by line-wrap hyphenation in PDF text extraction,
    # e.g. "inde- pendent" -> "independent".
    merged = re.sub(r"([A-Za-z])-\s+([A-Za-z])", r"\1\2", text)
    return re.sub(r"\s+", " ", merged).strip().lower()


def _check_one(
    source_id: str,
    spec: Dict[str, object],
    tier: str,
    ca_bundle: str | None = None,
    allow_insecure_tls: bool = False,
) -> CheckResult:
    must_include = [str(x) for x in spec.get("must_include", [])]
    source_type = str(spec.get("type", "")).strip()
    body = ""

    try:
        if source_type == "url_text":
            body = _fetch_url_text(
                str(spec["url"]),
                ca_bundle=ca_bundle,
                allow_insecure_tls=allow_insecure_tls,
            )
        elif source_type == "remote_pdf":
            body = _fetch_remote_pdf_text(
                str(spec["url"]),
                ca_bundle=ca_bundle,
                allow_insecure_tls=allow_insecure_tls,
            )
        elif source_type == "local_pdf":
            path = str(spec["path"])
            if not Path(path).exists():
                return CheckResult(
                    source_id=source_id,
                    tier=tier,
                    ok=False,
                    reachable=False,
                    matched_tokens=0,
                    total_tokens=len(must_include),
                    detail=f"Local file not found: {path}",
                )
            body = _load_local_pdf_text(path)
        else:
            return CheckResult(
                source_id=source_id,
                tier=tier,
                ok=False,
                reachable=False,
                matched_tokens=0,
                total_tokens=len(must_include),
                detail=f"Unsupported source type: {source_type}",
            )
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return CheckResult(
            source_id=source_id,
            tier=tier,
            ok=False,
            reachable=False,
            matched_tokens=0,
            total_tokens=len(must_include),
            detail=f"Fetch/parse failed: {exc}",
        )

    normalized_body = _normalize_for_match(body)
    missing_tokens = [tok for tok in must_include if _normalize_for_match(tok) not in normalized_body]
    matched = len(must_include) - len(missing_tokens)
    ok = matched == len(must_include)
    return CheckResult(
        source_id=source_id,
        tier=tier,
        ok=ok,
        reachable=True,
        matched_tokens=matched,
        total_tokens=len(must_include),
        detail=(
            "all tokens matched"
            if ok
            else "missing evidence tokens: " + "; ".join(missing_tokens[:3])
        ),
    )


def run_check(
    config_path: Path,
    ca_bundle: str | None = None,
    allow_insecure_tls: bool = False,
) -> Dict[str, object]:
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    required = cfg.get("required_sources", {})
    optional = cfg.get("optional_sources", {})

    results: List[CheckResult] = []
    for source_id, spec in required.items():
        results.append(
            _check_one(
                source_id,
                spec,
                "required",
                ca_bundle=ca_bundle,
                allow_insecure_tls=allow_insecure_tls,
            )
        )
    for source_id, spec in optional.items():
        results.append(
            _check_one(
                source_id,
                spec,
                "optional",
                ca_bundle=ca_bundle,
                allow_insecure_tls=allow_insecure_tls,
            )
        )

    required_failed = [r for r in results if r.tier == "required" and not r.ok]
    payload = {
        "all_required_passed": len(required_failed) == 0,
        "required_failed_count": len(required_failed),
        "results": [
            {
                "source_id": r.source_id,
                "tier": r.tier,
                "ok": r.ok,
                "reachable": r.reachable,
                "matched_tokens": r.matched_tokens,
                "total_tokens": r.total_tokens,
                "detail": r.detail,
            }
            for r in results
        ],
    }
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Run source gate checks for revise workflow.")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "config" / "revise_sources.json",
    )
    parser.add_argument("--output-json", type=Path, default=None)
    parser.add_argument(
        "--ca-bundle",
        type=Path,
        default=Path(os.environ["REVISE_CA_BUNDLE"]) if os.environ.get("REVISE_CA_BUNDLE") else None,
        help="Optional CA bundle PEM path for enterprise/private trust chains. "
        "Can also be set via REVISE_CA_BUNDLE env var.",
    )
    parser.add_argument(
        "--allow-insecure-tls",
        action="store_true",
        help="Disable TLS certificate verification for diagnostic use only.",
    )
    parser.add_argument(
        "--run-dir",
        type=Path,
        default=None,
        help="Run directory root. If set with --run-id and --output-json omitted, "
        "defaults to <run-dir>/reports/source_gate_report_<run_id>.json",
    )
    parser.add_argument("--run-id", type=str, default=None)
    args = parser.parse_args()

    if args.run_dir is not None and args.output_json is None:
        if not args.run_id:
            parser.error("--run-id is required when --run-dir is used without --output-json")
        if not is_valid_run_id(args.run_id):
            parser.error(f"Invalid --run-id format: {args.run_id}")
        args.output_json = args.run_dir / "reports" / f"source_gate_report_{args.run_id}.json"

    ca_bundle = str(args.ca_bundle) if args.ca_bundle else None
    payload = run_check(
        args.config,
        ca_bundle=ca_bundle,
        allow_insecure_tls=args.allow_insecure_tls,
    )
    out = json.dumps(payload, ensure_ascii=False, indent=2)
    print(out)
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(out + "\n", encoding="utf-8")

    return 0 if payload["all_required_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
