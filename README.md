# OpenRevise for all Industry

"OpenRevise" updates documents from high-confidence public evidence (papers, announcements, etc.), accepts sources in any format (PDF, image, doc, link), and outputs auditable tracked changes.

## Target Industries and Document Types
- Legal/Compliance: regulatory FAQs, contract Q&A, filing/review Q&A, policy interpretation notes.
- Consulting/Enterprise: diligence FAQs, bid Q&A, management Q&A, external messaging FAQs.
- Medical/Research: paper FAQs, reviewer response Q&A, clinical/regulatory Q&A.
- IR/Public Affairs: earnings Q&A, risk disclosure Q&A, public response FAQs.
- Tech/Operations: product compliance FAQs, security FAQs, SOP Q&A.

Primary output format: `.docx` with tracked changes.
Evidence inputs: verifiable fulltext from announcements, PDFs, papers, posters, and similar sources.

## Product Boundaries
This product intentionally does not do:
- prose polishing;
- cosmetic rewrites;
- unsupported factual expansion.

## What Counts as a Valid Revision
- New data appears or existing data changes.
- Key metrics, thresholds, or definitions change.
- Official announcements or regulatory updates change conclusions.
- Critical keywords, terms, or framing change.
- Material risk language or scope constraints change.

## What Does Not Count
- Expansion for style.
- Cosmetic rewriting.
- Synonym swaps that do not change facts.

## Method (Top-down)
1. Define problem and scope: clarify user intent, audience, time anchor, and no-change boundaries.
2. Decompose with MECE: split each target question into mutually exclusive and collectively exhaustive sub-questions.
3. Run source gate: verify required sources and fulltext evidence for each sub-question.
4. Decide revisions: revise only targets with sufficient evidence.
5. Write DOCX changes: apply tracked changes (`w:del` + `w:ins`) and preserve source footnotes.
6. Export audit trail: generate source gate report and full Q-to-source mapping.

## Quick Start
Requirements:
- Python 3.10+
- `pypdf`

Install dependency:
```bash
python3 -m pip install pypdf
```

Recommended entrypoint (run-scoped governance):
```bash
python3 scripts/run_revise_pipeline_v2.py \
  --input-docx "/absolute/path/to/original.docx" \
  --patch-spec "config/revision_patch_spec_template.json"
```

This automatically runs:
1. source gate check
2. DOCX revision
3. Q-source map export
4. manifest writing and run index update

Revision plans are supplied via JSON patch spec:
- template: `config/revision_patch_spec_template.json`
- each patch must include anchor, replacement, reason, and source footnote refs.

Source gate configuration:
- default config path: `config/revise_sources.json`
- define at least one `required_sources` entry (empty required sources are treated as gate failure).

## Enterprise TLS / Certificate Chain
If your network requires enterprise root certificates, provide a CA bundle:
```bash
python3 scripts/run_revise_pipeline_v2.py \
  --input-docx "/absolute/path/to/original.docx" \
  --ca-bundle "/absolute/path/to/corp_root_ca.pem"
```

Diagnostic-only switch (not recommended for normal use):
- `--allow-insecure-tls`

## Outputs and Auditability
Each run writes into: `runs/<run_id>/`

Core artifacts:
- `source_gate_report_<run_id>.json`
- `revision_change_audit_<run_id>.csv`
- `q_source_map_<run_id>.csv`
- `revised_<run_id>.docx`
- `revise_sync_manifest_<run_id>.tsv`
- `deleted_docx_manifest_<run_id>.tsv`
- `artifact_manifest_<run_id>.tsv`

Global index:
- `reports/run_index.tsv`

## Repository Structure
| Path | Purpose |
|---|---|
| `scripts/revise_docx.py` | Main DOCX reviser (tracked changes + footnotes) |
| `scripts/check_revise_sources.py` | Source gate checker (required/optional checks) |
| `scripts/run_revise_pipeline.py` | Legacy pipeline entrypoint (explicit in/out paths) |
| `scripts/run_revise_pipeline_v2.py` | Recommended entrypoint (run_id dirs, manifests, index) |
| `scripts/build_q_source_map.py` | Export full Q-to-source CSV |
| `scripts/query_q_source.py` | Query sources for one question |
| `scripts/update_run_index.py` | Update `reports/run_index.tsv` |
| `scripts/housekeeping.py` | Hot/cold retention and cleanup |
| `config/revise_sources.json` | Source gate rules |
| `config/revision_patch_spec_template.json` | Generic revision patch spec template |
| `config/source_registry.yaml` | Source registry snapshot |
| `docs/SOP_endpoint_extraction_standard.md` | SOP baseline |

## Policy Summary
- Fulltext-first.
- Abstract-only evidence is insufficient for core claim revisions.
- Any required-source failure blocks revision by default.
- Every change must be auditable, traceable, and reviewable.
