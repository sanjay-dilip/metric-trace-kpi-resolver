# MetricTrace: Multi-Agent KPI Dispute Resolver

An investigation copilot that takes a known KPI discrepancy between two dashboards and produces an evidence-backed reconciliation — which differences explain the gap, how much each contributes in dollars, and what remains unresolved.

**Status:** In development. Deterministic core, single-agent baseline, and data-quality checks are built and tested (135/135 tests passing). Benchmark authoring and the evaluation harness are in progress.

---

## Overview

Teams often end up with two dashboards that are supposed to report the same KPI but don't agree. The usual reason isn't a bug — it's that the two queries behind them quietly disagree: different joins, different date fields, different status filters, one query running against a stale extract, or a dashboard's own SQL drifting from what it claims to measure. Today an analyst resolves this by hand: reading both queries side by side, checking declared metric definitions, and tracing the data until the gap makes sense.

MetricTrace automates that investigation. Given two SQL queries (plus, optionally, each side's declared metric definition) and the known dollar gap between them, it runs a set of deterministic tools that diff the SQL, diff the definitions, check each dashboard against its own stated logic, and check for data-quality problems like stale extracts or missing partitions. It then computes exactly how many dollars of the gap each cause explains — executed against real data, not estimated — and hands the assembled evidence to an LLM that writes a plain-English explanation. The LLM only narrates; it never computes a number or invents a SQL relationship it can't point to in the evidence.

The output is a single evidence object: every difference found, its dollar contribution, and whatever portion of the gap is still unexplained.

---

## Why I Built This

I wanted to practice an end-to-end investigation-style analytics project where correctness matters more than a fitted model score — the kind of problem where a wrong answer (a fabricated root cause, or a double-counted dollar figure) would actually mislead someone. That constraint shaped the whole build: every reconciled dollar figure had to check out against a real, executed query before being trusted, and the LLM was deliberately kept out of anything load-bearing. It also gave me a reason to build and interrogate a non-trivial deterministic pipeline — schema design, SQL AST parsing, counterfactual arithmetic, cross-tool evidence collisions — before layering any agent orchestration on top of it.

---

## Key Features

- Deterministic SQL structural diff (via `sqlglot`) that catches join-type, filter, date-field, aggregation, `DISTINCT`, and `GROUP BY` differences between two queries.
- Metric-definition diff that compares each side's declared configuration when available, and falls back to inferring a definition directly from the SQL — with an honest confidence level — when no declared configuration exists.
- Self-consistency check that catches a dashboard's own query silently contradicting what it claims to measure (governance drift), separate from cross-dashboard disagreement.
- Deterministic data-quality pre-checks — stale extract, missing partition, referential-integrity — computed independently of the LLM, with dollar impact measured by re-running the same query against complete data.
- A reconciliation engine that attributes dollars to each cause using exact single-cause subtraction when only one cause is present, and pairwise counterfactual (Shapley-style) averaging when two causes interact on the same rows — proven against real data to differ meaningfully from naive fixed-order subtraction.
- Cross-tool collision handling: several of the tools above can independently flag the same underlying fact (e.g. a `COUNT` vs `COUNT DISTINCT` mismatch showing up in two different diffs). Two real collisions were found by execution and resolved with explicit suppression rules so reconciled dollars aren't double-counted.
- An LLM explainer that turns the assembled evidence into plain-English prose, manually verified against the 7 original fixture scenarios — including two scenarios purpose-built to tempt it into inventing an unsupported cause — to reference only what's actually in the evidence. The 11 scenarios added since have not been re-checked against the explainer.

---

## Tech Stack

- **Python 3.14**, `pydantic` v2 (typed schemas for every tool and agent output)
- **sqlglot** — SQL parsing and AST-level diffing
- **DuckDB** — row-level synthetic seed data backing every test scenario
- **Hugging Face Inference Providers** (`huggingface_hub`) — the LLM explainer (`meta-llama/Llama-3.1-8B-Instruct`)
- **tenacity** — retry/backoff around the LLM client
- **pytest** — 135 tests across schema validation, each tool in isolation, and full pipeline assembly
- **python-dotenv** — local secrets (`HF_TOKEN`)
- Installed but not yet in use: **LangGraph** (multi-agent orchestration, planned for the next build) and **Streamlit** (demo UI, planned after that)

---

## Project Workflow

1. **Scenario input** — two SQL queries, plus each side's declared metric definition if one exists (declared config is optional per side, never assumed).
2. **SQL structural diff** — parse both queries and flag join/filter/date-field/aggregation/`DISTINCT`/grouping differences.
3. **Metric-definition diff** — compare declared configs directly, or infer a definition from SQL when config is missing.
4. **Self-consistency check** — flag a dashboard whose own SQL contradicts its declared definition; where this fires, it takes precedence over the equivalent cross-dashboard diff so the same fact isn't reported twice.
5. **Data-quality pre-checks** — stale extract, missing partition, referential integrity, run independently and attached as additive evidence.
6. **Collision suppression** — where two tools trace to the same underlying fact, one finding is suppressed by an explicit, tested rule rather than left to double-count.
7. **Reconciliation arithmetic** — attribute a dollar amount to each surviving cause, executed against real DuckDB data: direct subtraction for a single cause, pairwise counterfactual averaging for two interacting causes.
8. **Evidence assembly** — every finding is collected into one `InvestigationEvidence` object, with an `unexplained_residual` equal to whatever the known gap doesn't yet account for.
9. **LLM explainer** — the assembled evidence (not the raw SQL or data) is handed to an LLM that writes the human-readable explanation.

---

## Data Source

All 18 scenarios in the scored benchmark set (`SCENARIOS`, `tests/fixtures/scenarios.py`) are hand-authored synthetic fixtures, each backed by real, queryable DuckDB data (`scripts/build_seed_data.py`, written to `data/sample/`). No external or API data is used. Every scenario is deliberately constructed around one confirmed root cause (or, for the negative control, no cause at all), and each scenario's reported values and known gap are derived by actually executing its SQL against its seed data — not hand-typed — so the benchmark numbers reconcile against something real rather than an assumed target. (Three additional fixtures exist in the same file to prove specific cross-tool collision mechanisms but are deliberately kept out of `SCENARIOS` — see `docs/decisions.md`, decisions 17–18 and 27.)

---

## Results / Outcomes

A scored accuracy benchmark (root-cause accuracy, human-escalation accuracy, unsupported-claim rate across 20-30 scenarios) is the next build and isn't done yet — the numbers below are what's been verified so far, mostly through the test suite and direct execution rather than a formal scored evaluation:

- **135/135 tests passing**, covering schema validation, each deterministic tool in isolation, cross-tool collision suppression, and full end-to-end evidence assembly.
- Across every scenario with a real, findable cause, the reconciliation engine's dollar attributions sum to exactly the known gap (0% residual) — proven by execution, not just by an algebraic sum-check that would pass regardless of whether the attribution itself was meaningful. The one true negative-control scenario (two dashboards that genuinely agree) correctly reports no cause and no gap.
- Two real cross-tool evidence collisions were deliberately proven by execution (not just reasoned about) before being fixed: a `referential_integrity` finding colliding with a `join_type` finding on the same orphaned row, and a `filter` finding colliding with an `excluded_statuses` finding on the same status column. Both are recorded in `docs/decisions.md` (decisions 17-18) with the actual dollar figures that exposed them.
- The LLM explainer was manually checked against the 7 original fixture scenarios and did not fabricate a cause or a dollar figure on any of them, including the two scenarios specifically built to tempt it (a 100%-unexplained case and the negative control). Two smaller, real model-quality issues were found and documented rather than fixed: it once dropped a minus sign narrating a negative dollar figure, and once described an empty finding category as a cause that "doesn't exist" instead of simply omitting it. The 11 scenarios added since (data-quality checks and the Build 3 technical-benchmark set) have not been re-run through the explainer.

---

## Screenshots / Demo

No UI exists yet — there's no Streamlit app to screenshot. A demo UI with human-in-the-loop escalation is planned as a later build (see Future Improvements). Until then, the pipeline is run and inspected directly from the command line — see **How to Run** below for a real example.

---

## How to Run

```bash
git clone https://github.com/sanjay-dilip/metric-trace-kpi-resolver.git
cd metric-trace-kpi-resolver

python -m venv metric-trace
# Windows:
metric-trace\Scripts\activate
# macOS/Linux:
source metric-trace/bin/activate

pip install -r requirements.txt
pip install -r requirements-dev.txt   # for running tests

cp .env.example .env   # add your HF_TOKEN (Hugging Face Inference Providers, free tier)

# Build the row-level seed data every test scenario runs against
python -m scripts.build_seed_data

# Run the full test suite
python -m pytest tests/ -v
```

Run one scenario through the full pipeline, including the LLM explainer:

```bash
python -c "from src.reconciliation_assembly import assemble_investigation_evidence; from src.explainer import explain_investigation; from tests.fixtures.scenarios import CASE_1_JOIN_TYPE as s; print(explain_investigation(assemble_investigation_evidence(s)))"
```

Requires `HF_TOKEN` to be set in `.env` for any command that calls the LLM explainer; the deterministic pipeline and test suite run without it.

---

## Repository Structure

```
metric-trace-kpi-resolver/
│
├── src/                       # Core deterministic pipeline
│   ├── schema.py               # Pydantic models for every tool/evidence output
│   ├── scenario.py             # Input scenario representation (two SQL queries + optional declared definitions)
│   ├── sql_parser.py           # sqlglot-based SQL parsing utility
│   ├── sql_diff.py             # Deterministic SQL structural diff tool
│   ├── definition_diff.py      # Declared/inferred metric-definition diff tool
│   ├── self_consistency.py     # Self-consistency check + cross-tool collision suppression rules
│   ├── data_quality.py         # Stale extract / missing partition / referential-integrity checks
│   ├── query_mutation.py       # Mechanically constructs a "corrected" SQL query from a found difference
│   ├── reconciliation.py       # Single-cause and pairwise Shapley-style dollar attribution
│   ├── reconciliation_assembly.py  # Full pipeline: assembles one InvestigationEvidence per scenario
│   ├── llm_client.py           # Provider-isolated LLM client (Hugging Face Inference Providers)
│   └── explainer.py            # Turns InvestigationEvidence into plain-English prose
│
├── tests/                     # 135 tests, one file per module above
│   └── fixtures/scenarios.py   # 18 hand-authored, DuckDB-backed test scenarios (SCENARIOS)
│
├── scripts/
│   └── build_seed_data.py      # Builds every scenario's row-level DuckDB seed data
│
├── data/sample/                # DuckDB seed data (committed, small synthetic datasets)
├── docs/decisions.md           # Running log of every non-obvious design decision made, with reasoning
├── app/                        # Streamlit demo UI (not yet built — planned)
├── config.py                   # Central path configuration
├── requirements.txt
└── README.md
```

---

## What I Learned

- How to keep a deterministic core and an LLM cleanly separated — the LLM never computes a number in this project, and proving that meant checking its output against the evidence object by hand, not just trusting a well-written prompt.
- That an algebraic sum-check (attributions summing to the known gap) can pass for the wrong reason — it's a property of the averaging formula itself, not proof the attribution split is meaningful. The real proof came from comparing attributions against independently known real-execution figures.
- That two independently-built detection tools can quietly describe the same underlying fact from different angles, and that this doesn't show up by reading the code — it showed up three separate times only once a fixture was built specifically to force the collision to fire.
- That recording a design decision in a decisions log doesn't mean the code implementing it exists — one entry was later found to be pure prose with no enforcing code, and a follow-up session had to build what was assumed already built.
- That test fixture values need to be calibrated against real execution from the start — several early scenarios reported a "known gap" that didn't match what their own seed data actually produced when queried, which silently broke every reconciliation check built on top of them until it was caught and fixed.

---

## Future Improvements

- Author the 20-30 scenario benchmark and build the scored evaluation harness (root-cause accuracy, human-escalation accuracy, unsupported-claim rate) — the next build.
- Add the LangGraph multi-agent core: parallel metric-definition and SQL-lineage specialist agents merged before reconciliation.
- Build the Streamlit demo UI with human-in-the-loop escalation for genuinely ambiguous business-rule questions.
- Add `late_arriving_data` detection (deferred — needs its own fixture and schema design pass before it can be built safely).
- Add a correction mechanism for SQL `filter` differences so that category can flow through the full reconciliation pipeline like the others already do.

---

## Contact

**Sanjay Dilip**
GitHub: [sanjay-dilip](https://github.com/sanjay-dilip)
Email: sanjaydilip7265@gmail.com
