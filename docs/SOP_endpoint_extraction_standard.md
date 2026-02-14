# Standard SOP for Evidence Extraction Tasks (General)

## 1. Scope
- Applies to tasks that extract information from multi-source materials and fill structured outputs.
- Works for table completion, literature endpoint extraction, field completion, and evidence-backed Q&A.
- Not tied to one disease area, one file name, or one fixed paper set.

## 2. North Star
- Minimize guessing.
- Make every filled value evidence-traceable.
- If not fillable, return an auditable missing reason instead of inference.

## 3. Core Principles
- Goal before execution: define success criteria first.
- Plan before implementation: choose strategy before running retrieval.
- Evidence over speed: quality first, recall second.
- Reproducibility: any claim should be independently reproducible.
- Explicit failure: uncertainty must be surfaced, not hidden.

## 4. Hard Gates (Must Be Declared Per Run)
- Lock evidence strategy before execution (for example: fulltext-first).
- Outputs that fail gates must not be delivered.
- Recommended baseline gate:
  - `MUST`: every value has evidence location (quote/page/path/source).
  - `MUST NOT`: no value from unsupported inference.
  - `MUST`: if gate fails, record structured missing reason.

### 4.1 Core Data Global Rules (Persistent)
- `MUST`: core data uses fulltext as primary evidence.
- `MUST`: long sentences remain eligible candidates.
- `MUST NOT`: abstract-only evidence cannot be the primary source for core fields.
- `ALLOWED`: abstracts can support context, fallback notes, and missing-reason explanation.
- `FAIL CONDITION`: if a core value is supported only by abstract evidence, the run fails.

### 4.2 Full Traversal Rules (Persistent)
- `MUST`: every target unit (cell/field) traverses all available fulltext pages and candidate sentences.
- `MUST`: if endpoint evidence is split across pages, continue scanning neighboring text and merge at evidence level.
- `MUST`: final filled value must come from aggregated candidates, not from first-hit truncation.
- `MUST`: evidence layer stores merged page locations (multi-page allowed).
- `FAIL CONDITION`: any first-hit early-stop behavior on a target unit fails QC.

### 4.3 Revision Rules (Persistent)
- `MUST`: each modified unit includes at least one verifiable source reference from fulltext-priority traversal.
- `MUST`: each modified unit includes one sentence explaining the modification reason.
- `MUST`: full re-cut revisions start from the original clean baseline document (no tracked changes).
- `MUST`: incremental revisions are allowed only when explicitly declared.
- `MUST`: per-run export includes a change-audit table with:
  - target ID (Q/cell/field)
  - source key + source location
  - one-sentence reason
- `FAIL CONDITION`: any delivered modification without source + reason fails QC.

## 5. Top-Down Execution Framework

### 5.1 Task Contract
- Define input/output schema and acceptance criteria.
- Break work into smallest independent units (fillable vs not fillable).
- Define completion vs failure conditions up front.

### 5.2 Evidence Profile Selection
- Define evidence priority chain before retrieval.
- Suggested profiles:
  - `Profile A: Fulltext-Only`
  - `Profile B: Fulltext-Preferred`
  - `Profile C: Metadata-Only`
- Core data should default to `Profile A`.

### 5.3 Source Planning
- Preferred order: local fulltext -> cached fulltext -> remote fulltext -> abstract/metadata.
- Every fallback step must be logged.
- Source order should be config-driven, not hard-coded per task.

### 5.4 Unit-Level Execution (Cell/Field)
- For each target unit:
  - locate target definition
  - traverse all fulltext candidates
  - if gate passes: extract + normalize
  - if gate fails: move to missing-reason branch
- Do not cross-contaminate evidence between units.

### 5.5 Extraction and Normalization
- Extraction layer keeps raw quote and location context.
- Normalization only standardizes equivalent terms/formatting, not facts.
- Multi-sentence merge must preserve merged evidence references.
- Cross-page continuation must stay in candidate pool.

### 5.6 Missing-Reason Taxonomy (Structured)
Recommended reasons:
- `fulltext_missing`
- `not_reported_in_source`
- `source_unreadable_or_ocr_failed`
- `metadata_missing`
- `evidence_conflict_needs_manual_review`

Each missing entry must include target locator and attempted chain.

## 6. Output Contract
- Result layer: filled structured values.
- Evidence layer: quote/source/location/status per value.
- Missing layer: reason + attempted chain per unfilled value.
- These three layers must map 1:1.

## 7. Quality Control
- Consistency: result/evidence/missing layers must align.
- Gate compliance: detect forbidden evidence patterns.
- Completeness: coverage by target unit.
- Traceability: random backtrace from value to source.

## 8. Delivery Standard
- Start with completion status, missing rate, and gate pass rate.
- Then list key deltas (added/fixed/pending).
- End with rerun entrypoints (scripts/config/paths).

## 9. Continuous Improvement
- Track recurring error classes (missed extraction, wrong extraction, over-normalization, source misclassification).
- Convert fixes into:
  - configurable rules
  - reproducible tests
  - reusable QC checklist
- No verbal-only rule is valid until written into SOP/config.

## 10. Minimum Pre-Run Checklist
- Evidence profile selected and locked.
- Output contract (3-layer mapping) defined.
- Missing-reason taxonomy and fallback chain defined.
- QC checks and fail conditions defined.
- Delivery format and rerun entrypoint defined.

## 11. Baseline in This Repository
- Runtime mode baseline: `max-reach`.
- GROBID baseline: remote mirror first, local fallback.
- Key files:
  - `src/paper_hub.py`
  - `src/source_registry.yaml`
