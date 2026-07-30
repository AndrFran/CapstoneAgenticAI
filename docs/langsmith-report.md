# LangSmith report

> **Status: template.** Everything in angle brackets must be filled in from a
> real traced run before submission. The commands below produce the numbers.

The brief requires: tracing enabled, at least 10 recorded conversations, latency
analysis, a review of failed runs, and one prompt improved from trace insights.

## 1. Setup

```bash
# .env
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=<key from https://smith.langchain.com/settings>
LANGSMITH_PROJECT=novaretail-supplychain
```

Verify before running anything: the Streamlit sidebar shows
**"LangSmith tracing → novaretail-supplychain"**, and
`python scripts/smoke_test.py` reports `llm_configured True`.

## 2. Recording the conversations

```bash
python scripts/run_eval_conversations.py
```

Twelve conversations, fourteen turns, each on its own thread and tagged `eval`
plus the case name. Results are written to `docs/eval_runs.json`.

Filter in LangSmith with `tag:eval`; a single case with `tag:<case-name>`.

| # | Case | Functional requirement covered | Turns |
|---|---|---|---|
| 1 | `track_shipment` | Track Shipment | 1 |
| 2 | `delay_impact` | Check Shipment Delay, Recommend Recovery Action | 1 |
| 3 | `affected_orders` | Identify Affected Orders | 1 |
| 4 | `delay_backlog` | Identify Delayed Orders | 1 |
| 5 | `inventory_shortage` | Check Product Stock, Identify Inventory Shortage | 1 |
| 6 | `warehouse_availability` | Check Warehouse Availability | 1 |
| 7 | `supplier_failure` | Find Alternative Suppliers, Compare Supplier Alternatives | 1 |
| 8 | `damaged_goods` | Report Damaged Goods, Create Supply Chain Incident | 1 |
| 9 | `incident_status` | Check Incident Status, Escalate Critical Incident | 1 |
| 10 | `multi_turn_memory` | Conversation memory across turns | 3 |
| 11 | `unsupported_request` | Handle unsupported requests | 1 |
| 12 | `missing_information` | Detect missing information | 1 |

## 3. Latency analysis

From `docs/eval_runs.json` → `summary.latency_seconds`.

| Metric | Value (s) |
|---|---|
| Turns measured | `<count>` |
| Min | `<min>` |
| Median | `<median>` |
| Mean | `<mean>` |
| p95 | `<p95>` |
| Max | `<max>` |

Per-agent breakdown — read from the LangSmith trace tree (each worker node is a
child run):

| Node | Median (s) | Share of turn | Notes |
|---|---|---|---|
| `intake` | `<x>` | `<x>%` | Structured output at `SUPPLYCHAIN_ROUTER_REASONING_EFFORT`. |
| `supervisor` (per hop) | `<x>` | `<x>%` | Multiply by hop count. |
| `incident_analysis` | `<x>` | `<x>%` | |
| `shipment` | `<x>` | `<x>%` | |
| `inventory` | `<x>` | `<x>%` | |
| `supplier` | `<x>` | `<x>%` | |
| `recovery` | `<x>` | `<x>%` | |
| `respond` | `<x>` | `<x>%` | |

Observations to write up:

- Which node dominates a multi-agent turn?
- How much does each extra supervisor hop cost? (`hops` is in every result.)
- Does `SUPPLYCHAIN_REASONING_EFFORT=low` vs `medium` vs `high` change answer
  quality on the eval set, and at what latency cost? Re-run with each and
  compare — the value is recorded in every run's LangSmith metadata.
- Is `gemini-3.1-flash-lite` enough for the recovery agent, or does the harder
  planning step justify `gemini-3.6-flash`? Compare on the same eval set.
- Token usage per turn from the LangSmith run metadata.

## 4. Failed runs

Filter LangSmith on `tag:eval` and status `error`; cross-check against
`failed_cases` in `docs/eval_runs.json`.

| Trace / case | Failure | Root cause | Fix | Status |
|---|---|---|---|---|
| `<link>` | `<what went wrong>` | `<why>` | `<what changed>` | `<fixed / accepted>` |

Things to look for specifically:

- **Fallback paths taken.** `request.extracted_by == "regex"` means the intake
  structured-output call failed; `route_reason` ending in `(fallback policy)`
  means supervisor routing failed. Both are silent degradations — count them.
- **Hop-limit hits.** `route_reason` mentioning the hop limit means the
  supervisor did not converge.
- **Tool errors.** Tool results containing an `error` field, and whether the
  agent recovered on the next step or gave up.
- **Rejected proposals.** Whether the answer correctly avoided claiming the
  action had happened.

## 5. Prompt improvement from trace insights

Required: at least one prompt improved based on what the traces showed.

**Prompt changed:** `<constant name in src/supplychain/prompts.py>`

**What the traces showed**

`<Cite specific runs. Example shape: "In 4 of 12 conversations the supervisor
routed to `supplier` before `inventory` on a shortage, so the agent priced a
purchase order for units that were already sitting in another warehouse — two
extra hops and an answer that recommended the wrong action.">`

**Before**

```
<the exact prompt text before the change>
```

**After**

```
<the exact prompt text after the change>
```

**Result** — re-run `python scripts/run_eval_conversations.py` after the change:

| Metric | Before | After |
|---|---|---|
| Median latency (s) | `<x>` | `<x>` |
| Mean hops per turn | `<x>` | `<x>` |
| Failed cases | `<x>` | `<x>` |
| Fallback paths taken | `<x>` | `<x>` |
| `<quality metric you define>` | `<x>` | `<x>` |

## 6. Evaluation observations

`<Two or three paragraphs: where the system is reliable, where it is not, and
what you would change next. Be specific and cite traces — this is the section
that shows you actually read them.>`

Candidates the team already knows about, to confirm or refute from traces:

- Workers receive a briefing rather than the raw conversation. Does that ever
  lose context the agent needed?
- Severity is rule-based, not model-judged. Does the rule set ever disagree with
  what an operator would say?
- The recovery plan ranks options by lead time then cost. Is that the right
  order for NovaRetail?
