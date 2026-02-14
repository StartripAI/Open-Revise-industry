#!/usr/bin/env python3
"""
Build FAQ Q-to-source mapping from revised DOCX.

Q numbering is assigned by question order in the FAQ body.
"""

from __future__ import annotations

import argparse
import csv
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Tuple
from run_artifact_utils import is_valid_run_id

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _read_docx_xml(docx_path: Path, name: str) -> ET.Element:
    with zipfile.ZipFile(docx_path, "r") as zf:
        return ET.fromstring(zf.read(name))


def _paragraph_text(p: ET.Element) -> str:
    return "".join((t.text or "") for t in p.iter(f"{W}t")).strip()


def _is_question(text: str) -> bool:
    if len(text) < 6:
        return False
    return text.endswith("？") or text.endswith("?")


def _extract_footnotes_map(footnotes_root: ET.Element) -> Dict[int, str]:
    mapping: Dict[int, str] = {}
    for fn in footnotes_root.findall(f"{W}footnote"):
        raw_id = fn.attrib.get(f"{W}id")
        if not raw_id or not raw_id.lstrip("-").isdigit():
            continue
        fid = int(raw_id)
        if fid < 0:
            continue
        mapping[fid] = _paragraph_text(fn)
    return mapping


def _extract_body_paragraphs(document_root: ET.Element) -> List[Tuple[str, List[int]]]:
    body = document_root.find(f"{W}body")
    if body is None:
        return []
    out: List[Tuple[str, List[int]]] = []
    for p in body.findall(f"{W}p"):
        text = _paragraph_text(p)
        if not text:
            continue
        refs: List[int] = []
        for r in p.iter(f"{W}footnoteReference"):
            rid = r.attrib.get(f"{W}id", "")
            if rid.isdigit():
                refs.append(int(rid))
        out.append((text, refs))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Q->source map for revised FAQ docx.")
    parser.add_argument("--input-docx", required=True, type=Path)
    parser.add_argument("--output-csv", type=Path, default=None)
    parser.add_argument(
        "--run-dir",
        type=Path,
        default=None,
        help="Run directory root. If set with --run-id and --output-csv omitted, "
        "defaults to <run-dir>/reports/q_source_map_<run_id>.csv",
    )
    parser.add_argument("--run-id", type=str, default=None)
    args = parser.parse_args()

    if args.output_csv is None:
        if args.run_dir is None or args.run_id is None:
            parser.error("--output-csv is required unless both --run-dir and --run-id are provided")
        if not is_valid_run_id(args.run_id):
            parser.error(f"Invalid --run-id format: {args.run_id}")
        args.output_csv = args.run_dir / "reports" / f"q_source_map_{args.run_id}.csv"

    doc_root = _read_docx_xml(args.input_docx, "word/document.xml")
    fn_root = _read_docx_xml(args.input_docx, "word/footnotes.xml")
    fn_map = _extract_footnotes_map(fn_root)
    paras = _extract_body_paragraphs(doc_root)

    question_idx = [i for i, (text, _) in enumerate(paras) if _is_question(text)]
    rows = []
    for qno, start in enumerate(question_idx, start=1):
        end = question_idx[qno] if qno < len(question_idx) else len(paras)
        qtext = paras[start][0]
        refs: List[int] = []
        for i in range(start + 1, end):
            refs.extend(paras[i][1])
        ref_ids = sorted(set(refs))
        src = [f"[{rid}] {fn_map.get(rid, '')}" for rid in ref_ids if rid in fn_map]
        rows.append(
            {
                "Q_no": qno,
                "Question": qtext,
                "Footnote_IDs": ",".join(str(x) for x in ref_ids),
                "Sources": " | ".join(src),
                "Has_Source": "YES" if len(ref_ids) > 0 else "NO",
            }
        )

    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.output_csv.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f, fieldnames=["Q_no", "Question", "Footnote_IDs", "Sources", "Has_Source"]
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"Q count: {len(rows)}")
    print(f"Output: {args.output_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
