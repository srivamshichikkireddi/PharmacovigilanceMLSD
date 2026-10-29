# Evaluation 1: corpus confirmation and project plan

**Course:** UE24AM342AA5 — ML System Design and AgentOps
**Project:** Mini Project 14 — Pharmacovigilance Adverse Event Intake and Triage Service
**Prepared:** 29 October 2026
**Purpose:** Confirm the reference corpus, map the section 4.1 case to its evidence and test values, and record a staged implementation/evaluation plan.

## 1. Project objective and boundaries

Build a locally runnable, asynchronous decision-support service that accepts a synthetic adverse-event report, validates it, assesses seriousness and expectedness against the selected reference documents, and recommends a reporting stream with citations. If the evidence is insufficient or conflicting, the service escalates to a qualified person for pharmacovigilance (QPPV).

The service does not submit regulatory reports and does not determine causality. Reports used for development and demonstrations must be synthetic; no real patient information is in scope. The model may help translate reaction terminology and choose evidence-retrieval tools, but a deterministic rule applies to verified evidence to produce the route.

## 2. Corpus confirmation

The documents below are the local source files used to build the evaluation index. PDF page counts, extractability, SHA-256 values, and chunk counts were checked against `data/index/manifest.json`. All three files have extractable text; the configured builder used `pdftotext`.

| Document | Local file / source ID | Verified description | PDF pages | Chunks | Intended use |
|---|---|---|---:|---:|---|
| New Drugs and Clinical Trials Rules, 2019 | `corpus/source_pdfs/cdsco_rules.pdf` / `cdsco_rules` | Gazette of India Extraordinary, No. 200, 19 March 2019 | 264 | 950 | Fifth Schedule: periodic safety update reporting and the serious-unexpected 15-day provision |
| IPC pharmacovigilance guidance for marketing authorisation holders | `corpus/source_pdfs/ipc_mah_v2.pdf` / `ipc_mah_v2` | Published by NCC-PvPI, Indian Pharmacopoeia Commission, with CDSCO; PDF metadata identifies the 29 August 2024 guidance document | 86 | 171 | ICSR validity, seriousness criteria, and serious/non-serious reporting timelines |
| Tegretol (carbamazepine) prescribing information | `corpus/source_pdfs/tegretol_us_pi.pdf` / `tegretol_us_pi` | Manufacturer prescribing information; the selected file is a **US** package insert | 25 | 95 | Listed reactions for the selected drug and the section 4.1 vocabulary-matching exercise |

SHA-256 checksums of the confirmed local PDF files:

- `cdsco_rules.pdf`: `b25fdd5476a24ed0e5c87fb0a7b4e7e991b5d93913b20ddc3ee2e9f0c1722804`
- `ipc_mah_v2.pdf`: `9142de7460f75d5c52a4c7baa49416883f751b738ce39263f6cc84834eb3f09d`
- `tegretol_us_pi.pdf`: `7e788668f892a236b9583a9766e1e87478281a1f8e07c9d7407f533c79a8bccb`

**Scope qualification:** The project brief asks for one approved package insert for a marketed drug. The selected Tegretol document is a US prescribing-information document, not an Indian-market insert. It is the package insert used by the current implementation and makes the section 4.1 vocabulary gap testable, but it does not establish Indian product-label approval. This jurisdiction limitation must be stated in the evaluation and final report. Replacing it with an appropriate Indian-market insert is a possible scope improvement if the instructor requires it.

The checked corpus version is `17027899456b`, containing **1,216 chunks**. The C0 offline evaluation used this same corpus version. The vector-enabled C6 index also contained 1,216 chunks. The source files are not modified by evaluation runs.

## 3. Section 4.1 case: evidence mapping and test values

### Case supplied by the brief

> “The patient's skin started peeling badly a week after starting the drug, and she was admitted to hospital.”

### Concrete synthetic evaluation input

The fixed scenario `PV-01` in `eval/scenarios.json` adds structured values needed for validation and repeatable testing:

| Field | Test value |
|---|---|
| Patient | 35-year-old female (synthetic) |
| Suspect drug | Tegretol (carbamazepine) |
| Start date | `2026-10-01` |
| Reaction date | `2026-10-08` (one week after start) |
| Received date | `2026-10-26` (synthetic evaluation value) |
| Reaction description | The section 4.1 wording above |
| Hospitalization | `true` |
| Reporter type | `doctor` |
| Expected route | `expedited_report` |
| Expected seriousness | serious, based on hospitalization |
| Expectedness | expected/listed in the selected Tegretol insert |
| Deadline | 15 calendar days from receipt; for this test receipt date, `2026-11-10` |
| Human handling | Surface the IPC/NDCT provision conflict and require QPPV confirmation |

The reporting deadline is derived from the scenario's receipt date. A live UI submission whose receipt date defaults to a different day will, correctly, have a different due date.

### Evidence to show at Evaluation 1

| Assessment claim | Source location | Why it matters |
|---|---|---|
| Hospitalization is a seriousness criterion | IPC guidance, PDF page 31, section 2.5.3.6 | The narrative says the patient was admitted; this supports serious status. |
| “Skin peeling” may correspond to listed severe dermatologic reactions | Tegretol US PI, PDF page 1 (boxed warning) and page 4 (warnings): toxic epidermal necrolysis (TEN) and Stevens–Johnson syndrome (SJS) | Demonstrates why reporter wording cannot be matched only by literal search. |
| Exfoliative dermatitis is listed | Tegretol US PI, PDF page 13 (adverse reactions) | Provides a listed medical term related to the reported skin reaction. |
| All serious adverse events have a 15-calendar-day IPC timeline | IPC guidance, PDF page 33, section 2.7.1 | Supports the expedited interpretation for serious cases. |
| Serious unexpected reactions are singled out for 15-day reporting; PSURs follow the periodic cycle | NDCT Rules 2019, PDF page 229, Fifth Schedule | Shows why the serious-and-expected case has a provision conflict rather than an unambiguous single-source answer. |

Page references above are PDF page references used by the current corpus/index and citations. When presenting the physical documents, confirm the printed page labels as well; PDF page numbering and printed pagination may differ.

### Expected system behavior and confirmation

For `PV-01`, the current reference answer is `expedited_report`, with a 15-day clock, citations to the seriousness criterion, listed label terms, IPC timeline, and NDCT provision. The conflict is explicitly shown and the output requires QPPV confirmation. Vamshi confirmed this intended section 4.1 route on 29 October 2026. This is a project/reference-answer confirmation, **not** an independent regulatory opinion; the final operational decision belongs to the QPPV.

The case is in the fixed 26-scenario evaluation set as `PV-01`. The baseline and later results are reproducible using the commands in [EVALUATION.md](./EVALUATION.md); the detailed expected values and gold passages are in [../eval/scenarios.json](../eval/scenarios.json).

## 4. Baseline setup and recorded starting point

The command-line evaluation runs without an LLM or network. The baseline uses the TF-IDF retrieval path and the fixed scenario/gold set. In the 26-scenario set, there are 22 valid cases and 4 boundary-rejection cases.

```powershell
python -m pv.corpus.build
python -m pv.eval.run --validate-gold
python -m pv.eval.run --label agentic
$env:AGENTIC_RETRIEVAL = "false"
python -m pv.eval.run --label single_pass_baseline
Remove-Item Env:AGENTIC_RETRIEVAL
```

Recorded C0 metrics:

| Metric | Single-pass baseline | Agentic TF-IDF baseline |
|---|---:|---:|
| Route accuracy (22 valid cases) | 0.3182 | 1.0000 |
| Context relevance | 0.0398 | 0.3696 |
| Context recall | 0.0889 | 0.9367 |
| Answered rate | 0.0000 | 0.6818 |
| Groundedness / faithfulness | n/a (no answers) | 1.0000 / 1.0000 |
| Hallucination / unsafe-answer rate | 0.0000 / 0.0000 | 0.0000 / 0.0000 |

The single-pass baseline safely escalates but misses most gold passages. The agentic baseline improves evidence retrieval and routes the current test cases correctly. These are engineering results on an author-created scenario set, not a population-level accuracy claim. A second reviewer must independently verify the expected outcomes and gold passages.

## 5. Project plan and gates

This plan preserves the brief's human confirmation gate and records the work completed to date. No future work is claimed as complete until its evidence is captured.

| Stage | Goal and deliverable | Status / exit evidence |
|---|---|---|
| Evaluation 1: corpus and plan | Confirm source documents, the case-to-passage mapping, concrete synthetic test values, and a reproducible baseline. | **Prepared.** This document, `eval/scenarios.json`, `data/index/manifest.json`, and C0 evaluation evidence record the work. Reference route confirmed by Vamshi on 29 October 2026. |
| C0–C3: baseline, service, and human case gate | Establish offline behavior, container/API operation, and the section 4.1 reference route. | Completed and tagged `cp0`–`cp3`. |
| C4: observability | Exercise the multi-worker stack, metrics, traces, and dashboard. | Completed and tagged `cp4`; the 20-report run was a smoke check, not an SLO benchmark. |
| C5: prompt evaluation | Compare two prompt versions with an authorized free-tier model while preserving the no-paid-spend gate. | Completed and tagged `cp5`; inconclusive due to `llm_unavailable` degradation; rules-only provider configuration restored. |
| C6: retrieval quality | Exercise embeddings, incremental sync, retrieval metrics, and a precision experiment. | Recorded and tagged `cp6`; lexical-weighted hybrid fusion improved relevance to 0.3788 while keeping context recall 0.9667 and route accuracy 1.0. A dependency-alignment image rebuild still needs healthy Docker verification. |
| C7: independent gold review | Have a second reviewer inspect all scenario outcomes and source/page/anchor gold passages; record reviewer, date, decisions, and changes. | Pending. Do not silently edit the gold set; preserve review notes and rerun evaluation after approved changes. |
| C8: declared-SLO load test | Use the LLM stub and Locust to measure 1-worker and 3-worker end-to-end runs against the already-declared SLO. Record RPS, p50/p95, failures, failure concurrency, and bottleneck. | Pending. `docs/SLO.md` predates the required measurements; C4 smoke timings are not a substitute. |
| D7/L5: cache and cost evidence | Measure cache-off/on p95 and cost per run; measure cost per 100 runs and largest per-step driver using an explicitly stated provider/stub configuration. | Pending. The LLM-free notional cost of a rules-only run is not an estimate of provider billing. |
| Release and presentation | Rebuild and smoke-test from a clean clone, update stale report placeholders/status notes, rehearse all applicable demo items, and preserve evidence. | Pending final validation after Docker is healthy. |

Owners and calendar deadlines for C7 onward are intentionally left unassigned; the team should agree these during planning rather than treating estimates as commitments.

## 6. Risks and decisions to revisit

1. **Jurisdiction of the selected product information:** the current label is US Tegretol PI. Confirm with the instructor whether this meets the project's “approved package insert” expectation or replace it with a suitable Indian-market document.
2. **IPC/NDCT interpretation:** the sources differ on serious-but-expected cases. The project reference answer is expedited with the conflict shown, but QPPV confirmation remains required.
3. **Gold-label bias:** scenarios and system were authored by the same team. Independent review is a planned gate, not optional evidence to imply after the fact.
4. **Embedding image:** C6 retrieval did run, but a later dependency-alignment rebuild stalled during Docker image unpacking. Verify the final image and tracing before relying on it in a fresh deployment.
5. **Performance and cost:** no Locust SLO result or measured cache/cost comparison is recorded yet.
6. **Synthetic data only:** do not use real patient information in demonstrations, logs, tests, or feedback.

## 7. Repository pointers

- Course brief: *Mini Project 14 — Pharmacovigilance Adverse Event Intake and Triage Service* (distributed separately by the course)
- [Corpus and evaluation details](./EVALUATION.md)
- [Data contract](./DATA_CONTRACT.md)
- [Architecture](./ARCHITECTURE.md)
- [Declared SLO](./SLO.md)
- [Runbook and incident log](./RUNBOOK.md) · [incident log](./INCIDENT_LOG.md)
- [Fixed scenarios](../eval/scenarios.json)
- [Corpus source provenance](../corpus/SOURCES.md) · index metadata is regenerated by `python -m pv.corpus.build`
- [C0 evidence](../evidence/c0/) · [C6 evidence](../evidence/c6/)
