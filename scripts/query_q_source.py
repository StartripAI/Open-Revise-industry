#!/usr/bin/env python3
"""
Query source mapping for a single FAQ question number.
"""

from __future__ import annotations

import argparse
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Tuple

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
QUESTION_PREFIX_RE = re.compile(
    r"^\s*(?:Q\s*\d+|Question\s*\d+|\d+[\.\)]|[一二三四五六七八九十]+[、\.])\s*",
    re.IGNORECASE,
)
QUESTION_HINT_RE = re.compile(
    r"(?:\?|？|what|how|why|when|where|which|who|whether|是否|如何|为什么|什么|哪|谁|何时|多少)",
    re.IGNORECASE,
)


def _read_xml(docx_path: Path, member: str) -> ET.Element:
    with zipfile.ZipFile(docx_path, "r") as zf:
        return ET.fromstring(zf.read(member))


def _text(node: ET.Element) -> str:
    return "".join((t.text or "") for t in node.iter(f"{W}t")).strip()


def _is_question(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 6:
        return False
    if stripped.endswith("？") or stripped.endswith("?"):
        return True
    if QUESTION_PREFIX_RE.match(stripped) and QUESTION_HINT_RE.search(stripped):
        return True
    return False


def _footnotes_map(root: ET.Element) -> Dict[int, str]:
    out: Dict[int, str] = {}
    for fn in root.findall(f"{W}footnote"):
        rid = fn.attrib.get(f"{W}id", "")
        if rid.lstrip("-").isdigit() and int(rid) >= 0:
            out[int(rid)] = _text(fn)
    return out


def _body_paragraphs(root: ET.Element) -> List[Tuple[str, List[int]]]:
    body = root.find(f"{W}body")
    if body is None:
        return []
    out: List[Tuple[str, List[int]]] = []
    for p in body.findall(f"{W}p"):
        t = _text(p)
        if not t:
            continue
        refs: List[int] = []
        for r in p.iter(f"{W}footnoteReference"):
            rid = r.attrib.get(f"{W}id", "")
            if rid.isdigit():
                refs.append(int(rid))
        out.append((t, refs))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Query one Q->source mapping from revised DOCX.")
    parser.add_argument("--input-docx", required=True, type=Path)
    parser.add_argument("--q", required=True, type=int, help="Question order number in FAQ body (Q1..Qn).")
    args = parser.parse_args()

    doc_root = _read_xml(args.input_docx, "word/document.xml")
    fn_root = _read_xml(args.input_docx, "word/footnotes.xml")
    fn_map = _footnotes_map(fn_root)
    paras = _body_paragraphs(doc_root)
    question_pos = [i for i, (t, _) in enumerate(paras) if _is_question(t)]

    if args.q < 1 or args.q > len(question_pos):
        print(f"Q{args.q} out of range. Available: Q1..Q{len(question_pos)}")
        return 1

    start = question_pos[args.q - 1]
    end = question_pos[args.q] if args.q < len(question_pos) else len(paras)
    qtext = paras[start][0]

    refs: List[int] = []
    for i in range(start + 1, end):
        refs.extend(paras[i][1])
    ref_ids = sorted(set(refs))

    print(f"Q{args.q}: {qtext}")
    if not ref_ids:
        print("Sources: NONE")
        return 0

    print("Sources:")
    for rid in ref_ids:
        src = fn_map.get(rid, "")
        if src:
            print(f"- [{rid}] {src}")
        else:
            print(f"- [{rid}] (not found in footnotes.xml)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
