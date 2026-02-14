#!/usr/bin/env python3
"""
Apply evidence-gated tracked revisions to DOCX using direct OOXML editing.

The revision plan is provided via --patch-spec JSON so the tool is domain-agnostic
across legal, consulting, medical, IR, and operations workflows.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import shutil
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Tuple
import xml.etree.ElementTree as ET

from run_artifact_utils import is_valid_run_id


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML_NS = "http://www.w3.org/XML/1998/namespace"
W = f"{{{W_NS}}}"
XML_SPACE = f"{{{XML_NS}}}space"

ET.register_namespace("w", W_NS)


def qn(name: str) -> str:
    return f"{W}{name}"


@dataclass(frozen=True)
class ParagraphPatch:
    anchor: str
    replacement: str
    label: str
    reason: str
    anchor_match: str = "contains"
    question_anchor: str | None = None
    question_match: str = "contains"


# Replacement token syntax:
# - [[fn:key]]   -> create/use new footnote mapped by key in patch spec
# - [[fnid:123]] -> reference existing footnote id in source document
TOKEN_PATTERN = re.compile(r"\[\[(fn|fnid):([A-Za-z0-9_]+)\]\]")


def paragraph_text(paragraph: ET.Element) -> str:
    return "".join((node.text or "") for node in paragraph.iter(qn("t")))


def _normalize_match_mode(raw: str | None) -> str:
    mode = (raw or "contains").strip().lower()
    if mode not in {"contains", "exact"}:
        raise ValueError(f"Unsupported match mode: {raw}")
    return mode


def _matches(text: str, needle: str, mode: str) -> bool:
    return text == needle if mode == "exact" else needle in text


def _prev_non_empty_text(paragraphs: List[ET.Element], idx: int) -> str:
    for i in range(idx - 1, -1, -1):
        candidate = paragraph_text(paragraphs[i]).strip()
        if candidate:
            return candidate
    return ""


def _find_patch_target(paragraphs: List[ET.Element], patch: ParagraphPatch) -> Tuple[ET.Element, int, str]:
    candidates: List[Tuple[int, ET.Element, str]] = []
    for idx, para in enumerate(paragraphs):
        text = paragraph_text(para)
        if _matches(text, patch.anchor, patch.anchor_match):
            question_text = _prev_non_empty_text(paragraphs, idx)
            candidates.append((idx, para, question_text))

    if patch.question_anchor:
        candidates = [
            c
            for c in candidates
            if _matches(c[2], patch.question_anchor, patch.question_match)
        ]

    if len(candidates) == 0:
        suffix = ""
        if patch.question_anchor:
            suffix = f" and question_anchor={patch.question_anchor!r}"
        raise ValueError(
            f"Patch {patch.label} did not match any paragraph "
            f"for anchor={patch.anchor!r}{suffix}"
        )

    if len(candidates) > 1:
        preview = ", ".join(str(c[0]) for c in candidates[:8])
        raise ValueError(
            f"Patch {patch.label} matched multiple paragraphs ({len(candidates)}): {preview}. "
            "Refine anchor/question_anchor or use exact match mode."
        )

    idx, para, question_text = candidates[0]
    return para, idx, question_text


def load_patch_spec(path: Path) -> Tuple[List[ParagraphPatch], Dict[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    patch_items = payload.get("patches", [])
    if not isinstance(patch_items, list) or not patch_items:
        raise ValueError("patch-spec must contain non-empty list field: patches")

    footnote_sources = payload.get("footnote_sources", {})
    if not isinstance(footnote_sources, dict):
        raise ValueError("patch-spec field footnote_sources must be an object")
    source_texts: Dict[str, str] = {}
    for key, value in footnote_sources.items():
        source_texts[str(key)] = str(value)

    patches: List[ParagraphPatch] = []
    for item in patch_items:
        if not isinstance(item, dict):
            raise ValueError("Each patch in patch-spec must be an object")
        patch = ParagraphPatch(
            label=str(item.get("label", "")).strip(),
            anchor=str(item.get("anchor", "")),
            replacement=str(item.get("replacement", "")),
            reason=str(item.get("reason", "")).strip(),
            anchor_match=_normalize_match_mode(item.get("anchor_match")),
            question_anchor=(
                str(item.get("question_anchor")).strip() if item.get("question_anchor") is not None else None
            ),
            question_match=_normalize_match_mode(item.get("question_match")),
        )
        patches.append(patch)

    return patches, source_texts


def tokenize_replacement(replacement: str) -> List[Tuple[str, str]]:
    tokens: List[Tuple[str, str]] = []
    pos = 0
    for match in TOKEN_PATTERN.finditer(replacement):
        if match.start() > pos:
            tokens.append(("text", replacement[pos : match.start()]))
        token_type, token_val = match.group(1), match.group(2)
        if token_type == "fn":
            tokens.append(("footnote_new", token_val))
        else:
            tokens.append(("footnote_existing", token_val))
        pos = match.end()
    if pos < len(replacement):
        tokens.append(("text", replacement[pos:]))
    return tokens


def collect_used_footnote_keys(patches: Iterable[ParagraphPatch], source_texts: Dict[str, str]) -> List[str]:
    order: List[str] = []
    seen = set()
    for patch in patches:
        for kind, value in tokenize_replacement(patch.replacement):
            if kind != "footnote_new":
                continue
            if value not in source_texts:
                raise KeyError(
                    f"Patch {patch.label} references unknown footnote key: {value}. "
                    "Add it to footnote_sources in patch-spec."
                )
            if value not in seen:
                seen.add(value)
                order.append(value)
    return order


def assert_patch_policy(
    patches: Iterable[ParagraphPatch],
    source_texts: Dict[str, str],
    existing_footnote_ids: set[int],
) -> None:
    seen_labels = set()
    for patch in patches:
        if not patch.label:
            raise ValueError("Every patch must include a non-empty label")
        if patch.label in seen_labels:
            raise ValueError(f"Duplicate patch label detected: {patch.label}")
        seen_labels.add(patch.label)

        if not patch.anchor:
            raise ValueError(f"Patch {patch.label} has empty anchor")
        if not patch.replacement.strip():
            raise ValueError(f"Patch {patch.label} has empty replacement")
        if not patch.reason:
            raise ValueError(f"Patch {patch.label} has empty reason")

        tokens = tokenize_replacement(patch.replacement)
        source_ref_count = 0
        for kind, value in tokens:
            if kind == "footnote_new":
                source_ref_count += 1
                if value not in source_texts:
                    raise ValueError(
                        f"Patch {patch.label} references unknown footnote key: {value}. "
                        "Define it under footnote_sources in patch-spec."
                    )
            elif kind == "footnote_existing":
                source_ref_count += 1
                if not value.isdigit():
                    raise ValueError(
                        f"Patch {patch.label} has non-numeric existing footnote id: {value}"
                    )
                if int(value) not in existing_footnote_ids:
                    raise ValueError(
                        f"Patch {patch.label} references missing existing footnote id: {value}"
                    )

        if source_ref_count == 0:
            raise ValueError(f"Patch {patch.label} has no verifiable source footnote reference")


def max_footnote_id(footnotes_root: ET.Element) -> int:
    ids = []
    for fn in footnotes_root.findall(qn("footnote")):
        raw = fn.get(qn("id"))
        if raw is None:
            continue
        try:
            value = int(raw)
        except ValueError:
            continue
        if value >= 0:
            ids.append(value)
    return max(ids) if ids else 0


def existing_footnote_ids(footnotes_root: ET.Element) -> set[int]:
    out: set[int] = set()
    for fn in footnotes_root.findall(qn("footnote")):
        raw = fn.get(qn("id"))
        if raw is None:
            continue
        if raw.lstrip("-").isdigit():
            value = int(raw)
            if value >= 0:
                out.add(value)
    return out


def footnote_text_map(footnotes_root: ET.Element) -> Dict[int, str]:
    out: Dict[int, str] = {}
    for fn in footnotes_root.findall(qn("footnote")):
        raw = fn.get(qn("id"))
        if raw is None or not raw.lstrip("-").isdigit():
            continue
        fid = int(raw)
        if fid < 0:
            continue
        out[fid] = "".join((node.text or "") for node in fn.iter(qn("t"))).strip()
    return out


def add_footnote(footnotes_root: ET.Element, footnote_id: int, text: str) -> None:
    footnote = ET.Element(qn("footnote"), {qn("id"): str(footnote_id)})
    p = ET.SubElement(footnote, qn("p"))
    ppr = ET.SubElement(p, qn("pPr"))
    ET.SubElement(ppr, qn("pStyle"), {qn("val"): "af7"})
    ppr_rpr = ET.SubElement(ppr, qn("rPr"))
    ET.SubElement(
        ppr_rpr,
        qn("rFonts"),
        {qn("ascii"): "Times New Roman", qn("hAnsi"): "Times New Roman", qn("cs"): "Times New Roman"},
    )

    r_ref = ET.SubElement(p, qn("r"))
    r_ref_pr = ET.SubElement(r_ref, qn("rPr"))
    ET.SubElement(r_ref_pr, qn("rStyle"), {qn("val"): "af9"})
    ET.SubElement(
        r_ref_pr,
        qn("rFonts"),
        {qn("ascii"): "Times New Roman", qn("hAnsi"): "Times New Roman", qn("cs"): "Times New Roman"},
    )
    ET.SubElement(r_ref, qn("footnoteRef"))

    r_text = ET.SubElement(p, qn("r"))
    r_text_pr = ET.SubElement(r_text, qn("rPr"))
    ET.SubElement(
        r_text_pr,
        qn("rFonts"),
        {qn("ascii"): "Times New Roman", qn("hAnsi"): "Times New Roman", qn("cs"): "Times New Roman"},
    )
    t = ET.SubElement(r_text, qn("t"))
    t.text = text

    footnotes_root.append(footnote)


def next_change_id(document_root: ET.Element) -> int:
    values: List[int] = []
    for elem in document_root.iter():
        if elem.tag not in (qn("ins"), qn("del")):
            continue
        raw = elem.get(qn("id"))
        if raw is None:
            continue
        try:
            values.append(int(raw))
        except ValueError:
            continue
    return (max(values) + 1) if values else 1


def tracked_change_counts(document_root: ET.Element) -> Tuple[int, int]:
    ins_count = 0
    del_count = 0
    for elem in document_root.iter():
        if elem.tag == qn("ins"):
            ins_count += 1
        elif elem.tag == qn("del"):
            del_count += 1
    return ins_count, del_count


def make_regular_run(parent: ET.Element, text: str) -> None:
    r = ET.SubElement(parent, qn("r"))
    r_pr = ET.SubElement(r, qn("rPr"))
    ET.SubElement(
        r_pr,
        qn("rFonts"),
        {qn("ascii"): "Times New Roman", qn("hAnsi"): "Times New Roman", qn("cs"): "Times New Roman"},
    )
    t = ET.SubElement(r, qn("t"))
    if text.startswith(" ") or text.endswith(" ") or "  " in text:
        t.set(XML_SPACE, "preserve")
    t.text = text


def make_footnote_ref_run(parent: ET.Element, footnote_id: int) -> None:
    r = ET.SubElement(parent, qn("r"))
    r_pr = ET.SubElement(r, qn("rPr"))
    ET.SubElement(r_pr, qn("rStyle"), {qn("val"): "af9"})
    ET.SubElement(
        r_pr,
        qn("rFonts"),
        {qn("ascii"): "Times New Roman", qn("hAnsi"): "Times New Roman", qn("cs"): "Times New Roman"},
    )
    ET.SubElement(r, qn("footnoteReference"), {qn("id"): str(footnote_id)})


def apply_tracked_replacement(
    paragraph: ET.Element,
    new_tokens: List[Tuple[str, str]],
    new_footnote_id_map: Dict[str, int],
    change_id_start: int,
    author: str,
    date_iso: str,
) -> int:
    old = paragraph_text(paragraph)

    ppr = paragraph.find(qn("pPr"))
    for child in list(paragraph):
        if ppr is not None and child is ppr:
            continue
        paragraph.remove(child)

    del_id = change_id_start
    ins_id = change_id_start + 1

    deleted = ET.SubElement(
        paragraph,
        qn("del"),
        {qn("id"): str(del_id), qn("author"): author, qn("date"): date_iso},
    )
    del_run = ET.SubElement(deleted, qn("r"))
    del_rpr = ET.SubElement(del_run, qn("rPr"))
    ET.SubElement(
        del_rpr,
        qn("rFonts"),
        {qn("ascii"): "Times New Roman", qn("hAnsi"): "Times New Roman", qn("cs"): "Times New Roman"},
    )
    del_text = ET.SubElement(del_run, qn("delText"))
    del_text.set(XML_SPACE, "preserve")
    del_text.text = old

    inserted = ET.SubElement(
        paragraph,
        qn("ins"),
        {qn("id"): str(ins_id), qn("author"): author, qn("date"): date_iso},
    )
    for kind, value in new_tokens:
        if kind == "text":
            if value:
                make_regular_run(inserted, value)
        elif kind == "footnote_new":
            make_footnote_ref_run(inserted, new_footnote_id_map[value])
        elif kind == "footnote_existing":
            make_footnote_ref_run(inserted, int(value))
        else:
            raise ValueError(f"Unsupported token kind: {kind}")

    return change_id_start + 2


def load_xml_from_docx(docx_path: Path, member: str) -> ET.Element:
    with zipfile.ZipFile(docx_path, "r") as zf:
        return ET.fromstring(zf.read(member))


def write_docx_with_replacements(
    source_docx: Path,
    output_docx: Path,
    document_xml: ET.Element,
    footnotes_xml: ET.Element,
) -> None:
    with zipfile.ZipFile(source_docx, "r") as zin:
        with zipfile.ZipFile(output_docx, "w", compression=zipfile.ZIP_DEFLATED) as zout:
            for info in zin.infolist():
                data = zin.read(info.filename)
                if info.filename == "word/document.xml":
                    data = ET.tostring(document_xml, encoding="utf-8", xml_declaration=True)
                elif info.filename == "word/footnotes.xml":
                    data = ET.tostring(footnotes_xml, encoding="utf-8", xml_declaration=True)
                zout.writestr(info, data)


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply generic evidence-gated tracked revisions to DOCX.")
    parser.add_argument("--input-docx", required=True, type=Path)
    parser.add_argument("--output-docx", type=Path, default=None)
    parser.add_argument("--copy-to", type=Path, default=None, help="Optional second output path.")
    parser.add_argument("--patch-spec", required=True, type=Path, help="JSON revision plan and source footnotes")
    parser.add_argument(
        "--run-dir",
        type=Path,
        default=None,
        help="Run directory root. If set with --run-id and --output-docx omitted, "
        "defaults to <run-dir>/revision/revised_<run_id>.docx",
    )
    parser.add_argument("--run-id", type=str, default=None)
    parser.add_argument(
        "--audit-csv",
        type=Path,
        default=None,
        help="Per-change audit table (Q/Reason/Source). Default: <output>_change_audit.csv",
    )
    parser.add_argument(
        "--allow-incremental",
        action="store_true",
        help="Allow using an input DOCX that already contains tracked revisions (w:ins/w:del).",
    )
    parser.add_argument("--author", default="Codex")
    parser.add_argument(
        "--date",
        default=dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        help="Revision timestamp in ISO-8601, e.g. 2026-02-12T12:00:00Z",
    )
    args = parser.parse_args()

    if args.run_id is not None and not is_valid_run_id(args.run_id):
        parser.error(f"Invalid --run-id format: {args.run_id}")

    if args.output_docx is None:
        if args.run_dir is None or args.run_id is None:
            parser.error("--output-docx is required unless both --run-dir and --run-id are provided")
        args.output_docx = args.run_dir / "revision" / f"revised_{args.run_id}.docx"

    if not args.input_docx.exists():
        print(f"Input docx not found: {args.input_docx}", file=sys.stderr)
        return 1
    if not args.patch_spec.exists():
        print(f"Patch spec not found: {args.patch_spec}", file=sys.stderr)
        return 1

    args.output_docx.parent.mkdir(parents=True, exist_ok=True)
    if args.audit_csv is not None:
        audit_csv = args.audit_csv
    elif args.run_dir is not None and args.run_id is not None and is_valid_run_id(args.run_id):
        audit_csv = args.run_dir / "revision" / f"revision_change_audit_{args.run_id}.csv"
    else:
        audit_csv = args.output_docx.with_name(f"{args.output_docx.stem}_change_audit.csv")

    patches, source_texts = load_patch_spec(args.patch_spec)

    document_root = load_xml_from_docx(args.input_docx, "word/document.xml")
    footnotes_root = load_xml_from_docx(args.input_docx, "word/footnotes.xml")

    existing_ids = existing_footnote_ids(footnotes_root)
    existing_text_map = footnote_text_map(footnotes_root)
    assert_patch_policy(patches, source_texts, existing_ids)

    ins_count, del_count = tracked_change_counts(document_root)
    if (ins_count > 0 or del_count > 0) and not args.allow_incremental:
        print(
            "Input DOCX already contains tracked revisions "
            f"(w:ins={ins_count}, w:del={del_count}). "
            "For full re-cut, use original clean baseline DOCX. "
            "If you intentionally want incremental patching, pass --allow-incremental.",
            file=sys.stderr,
        )
        return 3

    used_keys = collect_used_footnote_keys(patches, source_texts)
    next_fn_id = max_footnote_id(footnotes_root) + 1
    new_fn_id_map: Dict[str, int] = {}
    for key in used_keys:
        new_fn_id_map[key] = next_fn_id
        add_footnote(footnotes_root, next_fn_id, source_texts[key])
        next_fn_id += 1

    body = document_root.find(qn("body"))
    if body is None:
        print("Invalid document.xml: missing w:body", file=sys.stderr)
        return 1
    paragraphs = [p for p in body.findall(qn("p"))]

    cursor_change_id = next_change_id(document_root)
    applied_labels: List[str] = []
    audit_rows: List[Dict[str, str]] = []

    for patch in patches:
        target, _target_idx, question_text = _find_patch_target(paragraphs, patch)
        tokens = tokenize_replacement(patch.replacement)
        cursor_change_id = apply_tracked_replacement(
            paragraph=target,
            new_tokens=tokens,
            new_footnote_id_map=new_fn_id_map,
            change_id_start=cursor_change_id,
            author=args.author,
            date_iso=args.date,
        )
        applied_labels.append(patch.label)

        source_refs: List[str] = []
        source_ids: List[str] = []
        source_details: List[str] = []
        for kind, value in tokens:
            if kind == "footnote_new":
                source_refs.append(f"fn:{value}")
                source_ids.append(str(new_fn_id_map[value]))
                source_details.append(source_texts[value])
            elif kind == "footnote_existing":
                fid = int(value)
                source_refs.append(f"fnid:{fid}")
                source_ids.append(str(fid))
                source_details.append(existing_text_map.get(fid, ""))

        audit_rows.append(
            {
                "Patch_Label": patch.label,
                "Question": question_text,
                "Reason_One_Sentence": patch.reason,
                "Source_Refs": ",".join(source_refs),
                "Source_Footnote_IDs": ",".join(source_ids),
                "Source_Details": " | ".join([d for d in source_details if d]),
            }
        )

    write_docx_with_replacements(args.input_docx, args.output_docx, document_root, footnotes_root)

    audit_csv.parent.mkdir(parents=True, exist_ok=True)
    with audit_csv.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "Patch_Label",
                "Question",
                "Reason_One_Sentence",
                "Source_Refs",
                "Source_Footnote_IDs",
                "Source_Details",
            ],
        )
        writer.writeheader()
        writer.writerows(audit_rows)

    if args.copy_to is not None:
        args.copy_to.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(args.output_docx, args.copy_to)

    print("Applied patches:", ", ".join(applied_labels))
    print("Output:", args.output_docx)
    if args.copy_to:
        print("Copy:", args.copy_to)
    print("New footnotes:", {k: new_fn_id_map[k] for k in used_keys})
    print("Change audit:", audit_csv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
