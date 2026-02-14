#!/usr/bin/env python3
"""
Apply tracked revisions to PANOVA-3 FAQ DOCX using direct OOXML editing.

This script updates selected answer paragraphs with w:del/w:ins revisions and
adds new footnotes for official sources.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
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


FOOTNOTE_SOURCES: Dict[str, str] = {
    "jco2025": (
        "Source: Babiker HM, Picozzi V, Chandana SR, et al. PANOVA-3 phase III study. "
        "J Clin Oncol. 2025;43(21):2350-2360. doi:10.1200/JCO-25-00746. PMID:40448572. "
        "URL: https://pubmed.ncbi.nlm.nih.gov/40448572/ . "
        "Location: PubMed format (AB - RESULTS paragraph). "
        "Original excerpt: \"16.2 months ... v 14.2 months ... HR 0.82 ... P = .039\"; "
        "\"distant PFS ... 13.9 months ... v 11.5 months ... HR 0.74 ... P = .022\". "
        "Verified on: 2026-02-14."
    ),
    "poster2235": (
        "Source: Babiker HM, et al. ESMO 2025 Poster 2235P "
        "(TTFields device usage + CA 19-9 post-hoc analyses). "
        "URL: https://assets.novocure.biz/docs/2025-10/2025_ESMO_Babiker_P3%20usage%20CA19-9_POS.pdf . "
        "Location: Poster page 1, Figure 3 (device usage) and Table 2/Figure 4 (CA 19-9 subgroups). "
        "Original excerpt: \"17.1 vs 14.2 months, HR 0.71 ... p=0.003 (Figure 3A)\"; "
        "\"16.1 ... 14.1 ... HR 0.78 ... p=0.021\"; "
        "\"18.6 ... 14.7 ... HR 0.74 ... p=0.028\". "
        "Verified on: 2026-02-14."
    ),
    "esmogi_qol_presentation": (
        "Source: Macarulla T. PANOVA-3 pain and quality-of-life outcomes in LA-PAC, "
        "ESMO GI 2025 presentation (July 2, 2025), user-provided local full file. "
        "Location: /Users/alfred/Library/Containers/com.tencent.xinWeChat/Data/Documents/"
        "xwechat_files/wxid_3mjzo92w051t22_597a/msg/file/2026-02/ESMO-GI-2025 PANOVA-3 QoL presentation July2.pdf ; "
        "Page 7 (OS and pain-free survival), page 9 (global health status), "
        "page 12 (pain and pancreatic pain), page 13 (time to first opioid use). "
        "Original excerpt: \"16.2 ... 14.2 ... HR = 0.82\"; "
        "\"15.2 ... 9.1 ... HR = 0.74\"; "
        "\"10.1 ... 7.4 ... HR = 0.70\"; "
        "\"14.7 ... 10.2 ... HR = 0.69\"; "
        "\"9.3 ... 6.7 ... HR = 0.73\"; "
        "\"7.1 ... 5.4 ... HR = 0.80\". "
        "Verified on: 2026-02-14."
    ),
    "fda_approval": (
        "Source: U.S. FDA press announcement. "
        "URL: https://www.fda.gov/news-events/press-announcements/fda-approves-first-its-kind-device-treat-pancreatic-cancer . "
        "Location: Published date block and main body paragraph on approval basis. "
        "Original excerpt: \"February 12, 2026\"; "
        "\"approved a first-of-its-kind device\"; "
        "\"gemcitabine and nab-paclitaxel ... improved Overall Survival by approximately two months\". "
        "Verified on: 2026-02-14."
    ),
}


PATCHES: List[ParagraphPatch] = [
    ParagraphPatch(
        label="Q3",
        anchor="主要研究终点是总生存期，指从随机化开始至因任何原因引起死亡的时间。",
        replacement=(
            "主要研究终点是总生存期（OS），指从随机化开始至因任何原因引起死亡的时间。"
            "次要研究终点包括无进展生存期、局部无进展生存期、客观缓解率、1年生存率、"
            "生活质量、疼痛相关终点、无痛生存期、可切除率以及安全性和耐受性。"
            "其中疼痛、QoL、依从性和CA 19-9相关事后分析结果已公布。[[fn:jco2025]][[fn:poster2235]]"
            "[[fn:esmogi_qol_presentation]]"
        ),
        reason="原文将相关次要终点描述为待后续公布，现已有可核验公开数据，需更新状态为已公布。",
    ),
    ParagraphPatch(
        label="Q26",
        anchor="目前正在进一步分析可能在今后的大会上提出的数据。",
        replacement=(
            "除基线分层外，基于CA 19-9的事后分析结果已公布。以ITT人群为例，"
            "在基线CA 19-9 >37 U/mL亚组中，TTFields+GnP较GnP的中位OS为16.1 vs 14.1个月"
            "（HR 0.78，p=0.021）；在8周CA 19-9下降>50%亚组中为18.6 vs 14.7个月"
            "（HR 0.74，p=0.028）。在mITT人群中，对应亚组也观察到一致趋势"
            "（分别为HR 0.75，p=0.019；HR 0.71，p=0.019）。[[fn:poster2235]]"
        ),
        reason="原文为未来分析表述，现已发布CA 19-9分层事后分析具体数值，需替换为实测结果。",
    ),
    ParagraphPatch(
        label="Q29",
        anchor="总生存期是肿瘤学试验的金标准。",
        replacement=(
            "总生存期是肿瘤学试验的金标准。PANOVA-3中，ITT人群OS为16.2 vs 14.2个月"
            "（HR 0.82，p=0.039）；远处PFS事后分析为13.9 vs 11.5个月（HR 0.74，p=0.022）。"
            "同时，总体PFS和局部PFS未显示显著改善。"
            "[[fn:jco2025]]"
        ),
        reason="该题核心是解释OS与PFS差异，需补入主文已给出的OS与远处PFS定量结果。",
    ),
    ParagraphPatch(
        label="Q31",
        anchor="TTFields 已经确立的对肿瘤细胞的物理作用方式是抑制有丝分裂，这是局部的。",
        replacement=(
            "TTFields的物理作用位点在局部肿瘤区域。临床上，PANOVA-3报告远处PFS事后分析"
            "获益（13.9 vs 11.5个月；HR 0.74，p=0.022），且前3个月设备使用率≥50%的患者"
            "OS更长（ITT：17.1 vs 14.2个月，HR 0.71，p=0.003）。[[fn:jco2025]]"
            "[[fn:poster2235]]"
        ),
        reason="原文仅停留在作用机制层面，需补充已公布的远处控制与依从性相关临床证据。",
    ),
    ParagraphPatch(
        label="Q35",
        anchor="Novocure 正在评估可能在未来会议上提交的数据。",
        replacement=(
            "相关事后亚组结果已公布。按前3个月设备使用率分层：ITT中使用率≥50%的患者OS为"
            "17.1 vs 14.2个月（HR 0.71，p=0.003），mITT为17.8 vs 15.1个月（HR 0.77，"
            "p=0.034）。按CA 19-9分层：例如ITT中8周下降>50%亚组OS为18.6 vs 14.7个月"
            "（HR 0.74，p=0.028），mITT为19.2 vs 14.7个月（HR 0.71，p=0.019）。"
            "[[fn:poster2235]]"
        ),
        reason="原文写为尚在评估，现亚组结果已正式发布并有HR/p值，需更新为已发布结论。",
    ),
    ParagraphPatch(
        label="Q10",
        anchor="可切除率定义为在疾病进展前，由多学科团队（包括至少一名外科医生、一名肿瘤内科医生和一名放射科医生）认为肿瘤可切除的患者百分比。",
        replacement=(
            "可切除率是PANOVA-3的预设次要终点之一。当前可核验公开全文未提供“是否集中影像"
            "评审”和“两组可切除率效应值”的可直接引用原句，暂无法补充定量更新。"
            "[[fn:jco2025]][[fn:poster2235]]"
        ),
        reason="该题涉及可切除率评估方法；公开全文无新增可直接引用的定量细节，需改为证据边界声明。",
    ),
    ParagraphPatch(
        label="Q37",
        anchor="PANOVA-3 的安全性数据表明，TTFields通常耐受良好。",
        replacement=(
            "PANOVA-3安全性数据显示TTFields总体耐受良好。最常见器械相关不良事件为轻中度"
            "皮肤事件；≥3级器械相关AE发生率为7.7%；未见新增系统性安全信号。[[fn:jco2025]]"
        ),
        reason="补入主文已公布的关键安全性数值，避免仅保留笼统“耐受良好”表述。",
    ),
    ParagraphPatch(
        label="Q38",
        anchor="使用 TTFields 治疗后整体健康状况改善。",
        replacement=(
            "QoL和疼痛相关结果已公布。与GnP单药相比，TTFields+GnP延长无痛恶化生存"
            "（15.2 vs 9.1个月；HR 0.74，p=0.027）；延长疼痛量表恶化时间（QLQ-C30 pain："
            "10.1 vs 7.4个月，HR 0.70，p=0.003；PAN26 pancreatic pain：14.7 vs 10.2个月，"
            "HR 0.69，p=0.006）；全局健康状态恶化时间7.1 vs 5.7个月（HR 0.77，p=0.023）；"
            "首次阿片使用时间ITT为9.3 vs 6.7个月（HR 0.73，p=0.014），mITT为7.1 vs 5.4个月"
            "（HR 0.80，p=0.046）。"
            "[[fn:jco2025]][[fn:esmogi_qol_presentation]]"
        ),
        reason="原文未覆盖QoL与疼痛终点的已发布定量结果，需更新为可引用的终点数据。",
    ),
    ParagraphPatch(
        label="Q39",
        anchor="建议患者每月至少使用 TTFields 疗法 75% 的时间(平均每天 18 小时)。",
        replacement=(
            "建议患者每月至少使用TTFields疗法75%的时间（平均每天18小时）。PANOVA-3中位"
            "日使用率为62.1%（约15小时/日），中位治疗持续27.6周。前3个月设备使用率≥50%"
            "的患者OS更长（ITT：17.1 vs 14.2个月，HR 0.71，p=0.003；mITT：17.8 vs "
            "15.1个月，HR 0.77，p=0.034）。"
            "[[fn:jco2025]][[fn:poster2235]]"
        ),
        reason="该题涉及依从性与疗效关系，现已有使用率分层与OS结果，需补入已发布数据。",
    ),
    ParagraphPatch(
        label="Q40",
        anchor="目前，没有 PANOVA-3 试验的这些数据。",
        replacement=(
            "目前已有相关数据：事后分析显示，前3个月设备使用率≥50%的患者OS更长（ITT："
            "17.1 vs 14.2个月，HR 0.71，p=0.003；mITT：17.8 vs 15.1个月，HR 0.77，"
            "p=0.034）。该结论来自事后分析。[[fn:poster2235]]"
        ),
        reason="原文声称无数据，但相关事后分析已发布，需由“无数据”改为“有数据且为事后分析”。",
    ),
    ParagraphPatch(
        label="Q39b",
        anchor="中位每日器械使用率为62.1%（0–99.0%；相当于近15 h），PANOVA-3中 TTFields 治疗使用的中位（范围）持续时间为27.6(0.1–234.4) 周。目前正在分析使用和获益之间的关系，并可能在未来的大会上报告。",
        replacement=(
            "中位每日器械使用率为62.1%（0–99.0%；约15 h/日），TTFields治疗中位持续时间"
            "为27.6（0.1–234.4）周。该“使用率-获益”关系已在后续分析中公布：前3个月"
            "使用率≥50%与更长OS相关（ITT：HR 0.71，p=0.003；mITT：HR 0.77，p=0.034）。"
            "[[fn:jco2025]][[fn:poster2235]]"
        ),
        reason="该段原文为“正在分析”，现已有发布结果，需改为已公布并给出关键效应值。",
    ),
    ParagraphPatch(
        label="Q43",
        anchor="PANOVA-3 研究人群是局部晚期不可切除的胰腺癌的患者。",
        replacement=(
            "PANOVA-3研究人群为局部晚期不可切除胰腺腺癌（LA-PAC）。在美国，FDA已于"
            "2026年2月12日批准Optune Pax（TTFields）联合吉西他滨+白蛋白结合型紫杉醇"
            "用于成人LA-PAC。"
            "[[fn:jco2025]][[fn:fda_approval]]"
        ),
        reason="新增监管状态变化（FDA批准）会影响适用人群解读，需更新监管口径与日期。",
    ),
    ParagraphPatch(
        label="Q46",
        anchor="目前的标准治疗是化疗-吉西他滨联合白蛋白结合型紫杉醇，或 FOLFIRINOX",
        replacement=(
            "目前局部晚期胰腺癌系统治疗仍以化疗方案为基础（如GnP、FOLFIRINOX）。在美国，"
            "自2026年2月12日起，FDA已批准TTFields（Optune Pax）联合GnP用于成人LA-PAC。"
            "其他地区以当地审批和指南更新为准。"
            "[[fn:46]][[fn:fda_approval]]"
        ),
        reason="标准治疗表述需纳入美国新增批准事实并保持地区差异说明，避免口径过时。",
    ),
]


FN_PATTERN = re.compile(r"\[\[fn:([a-zA-Z0-9_]+)\]\]")


def paragraph_text(paragraph: ET.Element) -> str:
    return "".join((node.text or "") for node in paragraph.iter(qn("t")))


def collect_used_footnote_keys(patches: Iterable[ParagraphPatch]) -> List[str]:
    order: List[str] = []
    seen = set()
    for patch in patches:
        for key in FN_PATTERN.findall(patch.replacement):
            if key == "46":
                continue
            if key not in FOOTNOTE_SOURCES:
                raise KeyError(f"Unknown footnote key in replacement text: {key}")
            if key not in seen:
                seen.add(key)
                order.append(key)
    return order


def assert_patch_policy(patches: Iterable[ParagraphPatch]) -> None:
    for patch in patches:
        if not patch.reason.strip():
            raise ValueError(f"Patch {patch.label} has empty reason")
        keys = [k for k in FN_PATTERN.findall(patch.replacement) if k != "46"]
        if not keys:
            raise ValueError(f"Patch {patch.label} has no verifiable source footnote key")


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


def tokenize_replacement(replacement: str) -> List[Tuple[str, str]]:
    tokens: List[Tuple[str, str]] = []
    pos = 0
    for match in FN_PATTERN.finditer(replacement):
        if match.start() > pos:
            tokens.append(("text", replacement[pos : match.start()]))
        tokens.append(("footnote", match.group(1)))
        pos = match.end()
    if pos < len(replacement):
        tokens.append(("text", replacement[pos:]))
    return tokens


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
    footnote_id_map: Dict[str, int],
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
        elif kind == "footnote":
            if value == "46":
                make_footnote_ref_run(inserted, 46)
            else:
                make_footnote_ref_run(inserted, footnote_id_map[value])
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
    parser = argparse.ArgumentParser(description="Update PANOVA FAQ DOCX with tracked revisions.")
    parser.add_argument("--input-docx", required=True, type=Path)
    parser.add_argument("--output-docx", type=Path, default=None)
    parser.add_argument("--copy-to", type=Path, default=None, help="Optional second output path.")
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
    args.output_docx.parent.mkdir(parents=True, exist_ok=True)
    if args.audit_csv is not None:
        audit_csv = args.audit_csv
    elif args.run_dir is not None and args.run_id is not None and is_valid_run_id(args.run_id):
        audit_csv = args.run_dir / "revision" / f"revision_change_audit_{args.run_id}.csv"
    else:
        audit_csv = args.output_docx.with_name(f"{args.output_docx.stem}_change_audit.csv")

    document_root = load_xml_from_docx(args.input_docx, "word/document.xml")
    footnotes_root = load_xml_from_docx(args.input_docx, "word/footnotes.xml")
    assert_patch_policy(PATCHES)
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

    used_keys = collect_used_footnote_keys(PATCHES)
    next_fn_id = max_footnote_id(footnotes_root) + 1
    fn_id_map: Dict[str, int] = {}
    for key in used_keys:
        fn_id_map[key] = next_fn_id
        add_footnote(footnotes_root, next_fn_id, FOOTNOTE_SOURCES[key])
        next_fn_id += 1

    body = document_root.find(qn("body"))
    if body is None:
        print("Invalid document.xml: missing w:body", file=sys.stderr)
        return 1
    paragraphs = [p for p in body.findall(qn("p"))]

    cursor_change_id = next_change_id(document_root)
    applied_labels: List[str] = []
    audit_rows: List[Dict[str, str]] = []

    for patch in PATCHES:
        target = None
        target_idx = -1
        for p in paragraphs:
            if patch.anchor in paragraph_text(p):
                target = p
                target_idx = paragraphs.index(p)
                break
        if target is None:
            print(f"Patch anchor not found for {patch.label}: {patch.anchor}", file=sys.stderr)
            return 2
        question_text = ""
        for i in range(target_idx - 1, -1, -1):
            prev_text = paragraph_text(paragraphs[i]).strip()
            if prev_text:
                question_text = prev_text
                break
        tokens = tokenize_replacement(patch.replacement)
        cursor_change_id = apply_tracked_replacement(
            paragraph=target,
            new_tokens=tokens,
            footnote_id_map=fn_id_map,
            change_id_start=cursor_change_id,
            author=args.author,
            date_iso=args.date,
        )
        applied_labels.append(patch.label)
        source_keys = [k for k in FN_PATTERN.findall(patch.replacement) if k != "46"]
        source_ids = [str(fn_id_map[k]) for k in source_keys if k in fn_id_map]
        source_details = [FOOTNOTE_SOURCES[k] for k in source_keys if k in FOOTNOTE_SOURCES]
        audit_rows.append(
            {
                "Patch_Label": patch.label,
                "Question": question_text,
                "Reason_One_Sentence": patch.reason,
                "Source_Keys": ",".join(source_keys),
                "Source_Footnote_IDs": ",".join(source_ids),
                "Source_Details": " | ".join(source_details),
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
                "Source_Keys",
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
    print("New footnotes:", {k: fn_id_map[k] for k in used_keys})
    print("Change audit:", audit_csv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
