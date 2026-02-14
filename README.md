# Open Revise Industry

Evidence-gated DOCX revision toolkit for high-stakes Q&A documents.

`revise` 不是写作润色器，而是“有证据才改”的文档修订流水线：
- 只在有可核验 fulltext 证据时修改；
- 修改必须是实质性变更（数据、指标、口径、关键词、关键措辞）；
- 每条修改都可追溯到来源（`Q -> source`）。

## North Star
- 不猜测，不编造。
- 先证据，后修订。
- 证据不足时明确写出：`not available in currently verifiable fulltext`。

## 什么时候应该修订
- 新数据出现或原数据更新。
- 关键指标、统计口径、阈值发生变化。
- 监管/官方公告更新导致结论变化。
- 关键词、术语、定义发生变化。
- 关键风险措辞、适应症、限制条件变化。

## 什么时候不应该修订
- 仅做扩写。
- 仅做文风美化。
- 仅做同义改写但不改变事实含义。

## 适用行业与文档
- 法律/合规：法规 FAQ、合同问答、申报/审查问答、政策解释稿。
- 咨询/企业：尽调 FAQ、投标问答、管理层 Q&A、对外口径 FAQ。
- 医学/科研：论文 FAQ、审稿回复问答、临床/药政问答。
- IR/公共事务：财报问答、风险披露问答、舆情回应 FAQ。
- 技术/运营：产品合规 FAQ、安全 FAQ、SOP 问答。

当前主目标文档格式：`.docx`（带修订痕迹输出）。
证据源支持：网页公告、PDF、论文/Poster 等可核验 fulltext。

## 方法论（Top-down）
1. 定义问题与 scope：明确用户问题、受众、时点、不可改边界。
2. MECE 拆分：把每个目标问题分解为互斥且穷尽的子问题。
3. 证据门禁：逐子问题检查 required source 与 fulltext 可得性。
4. 修订决策：仅对证据充分的目标执行改写。
5. DOCX 落地：以 tracked changes 写入（`w:del` + `w:ins`），并保留脚注来源。
6. 审计导出：生成 source gate 报告与完整 Q-source 映射。

## Quick Start
要求：
- Python 3.10+
- `pypdf`

安装依赖：
```bash
python3 -m pip install pypdf
```

推荐入口（run 级治理）：
```bash
python3 scripts/run_revise_pipeline_v2.py \
  --input-docx "/absolute/path/to/original.docx"
```

运行后自动执行：
1. source gate 检查
2. docx 修订
3. q-source map 导出
4. manifest 写入与 `run_index` 更新

## 企业网络/证书链
若你在企业网络中遇到 TLS/证书问题，可指定 CA bundle：
```bash
python3 scripts/run_revise_pipeline_v2.py \
  --input-docx "/absolute/path/to/original.docx" \
  --ca-bundle "/absolute/path/to/corp_root_ca.pem"
```

仅用于排障（不推荐长期使用）：
- `--allow-insecure-tls`

## 产物与审计
每次运行写入：`runs/<run_id>/`

固定关键产物：
- `source_gate_report_<run_id>.json`
- `revision_change_audit_<run_id>.csv`
- `q_source_map_<run_id>.csv`
- `revised_<run_id>.docx`
- `revise_sync_manifest_<run_id>.tsv`
- `deleted_docx_manifest_<run_id>.tsv`
- `artifact_manifest_<run_id>.tsv`

全局索引：
- `reports/run_index.tsv`

## 目录结构
| Path | Purpose |
|---|---|
| `scripts/revise_docx.py` | 主修订脚本（tracked changes + footnotes） |
| `scripts/check_revise_sources.py` | Source gate（required/optional 检查） |
| `scripts/run_revise_pipeline.py` | 兼容旧入口（显式输入输出） |
| `scripts/run_revise_pipeline_v2.py` | 推荐入口（run_id 目录、manifest、index） |
| `scripts/build_q_source_map.py` | 生成完整 Q-source CSV |
| `scripts/query_q_source.py` | 查询单题来源 |
| `scripts/update_run_index.py` | 更新 `reports/run_index.tsv` |
| `scripts/housekeeping.py` | 热冷分层与过期清理 |
| `config/revise_sources.json` | Source gate 规则 |
| `config/source_registry.yaml` | 来源注册快照 |
| `docs/SOP_endpoint_extraction_standard.md` | SOP 基线 |

## 核心策略
- Fulltext-first。
- Abstract-only 不足以支持核心结论改写。
- required source 任一失败，默认阻断修订。
- 所有修改必须可审计、可回溯、可复核。
