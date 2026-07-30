# LangSmith report

> **Status: TM3 slice complete for graded local checks.** §5 prompt improvement
> filled; TM3 eval cases in `docs/eval_runs_tm3.json`; presentation notes in
> `docs/presentation-tm3.md`. Full 12-case LangSmith-hosted pack still needs
> `LANGSMITH_API_KEY` + non-exhausted Gemini quota.

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

### 3a. Full suite (`docs/eval_runs.json`)

From `docs/eval_runs.json` → `summary.latency_seconds` (after the team runs
all 12 cases):

| Metric | Value (s) |
|---|---|
| Turns measured | `<count>` |
| Min | `<min>` |
| Median | `<median>` |
| Mean | `<mean>` |
| p95 | `<p95>` |
| Max | `<max>` |

### 3b. TM3 cases only (`docs/eval_runs_tm3.json`, 2026-07-30)

Harness turn `latency_seconds` (last graph invoke; approval resumes can make
this look shorter than wall clock) and case `total_seconds` (wall clock):

| Case | Route | Hops | Turn latency (s) | Wall clock (s) | OK |
|---|---|---|---|---|---|
| `inventory_shortage` | inventory → recovery | 3 | 3.86 | 18.14 | yes |
| `warehouse_availability` | inventory | 2 | 10.0 | 10.03 | yes |
| `supplier_failure` | incident_analysis → supplier → inventory → recovery | 5 | 4.48 | 54.77 | yes |

TM3 summary latency (turn metric): count 3 · min 3.86 · median 4.48 · mean 6.11 · max 10.0. Failed cases: none.

Per-agent breakdown — read from the LangSmith trace tree (each worker node is a
child run). TM3 local notes until hosted traces exist:

| Node | Median (s) | Share of turn | Notes |
|---|---|---|---|
| `intake` | `<x>` | `<x>%` | Structured output at `SUPPLYCHAIN_ROUTER_REASONING_EFFORT`. |
| `supervisor` (per hop) | `<x>` | `<x>%` | Multiply by hop count. |
| `incident_analysis` | `<x>` | `<x>%` | Used on `supplier_failure` (severity high). |
| `shipment` | `<x>` | `<x>%` | |
| `inventory` | ~3–10 s wall on TM3 cases | high on shortage/warehouse | Decision-tree tools; structured finding. |
| `supplier` | included in ~55 s wall `supplier_failure` | mid | Details → alternatives → compare/cost when qty known. |
| `recovery` | after inventory/supplier | HITL approve on 2/3 TM3 cases | TM4-owned. |
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
- **TM3:** On `supplier_failure` the supervisor visited `supplier` before
  `inventory` (opposite of the written routing preference). Inventory still
  closed transfers afterward; worth a TM4 routing-trace review, not a tool bug.

## 4. Failed runs

Filter LangSmith on `tag:eval` and status `error`; cross-check against
`failed_cases` in `docs/eval_runs.json`.

| Trace / case | Failure | Root cause | Fix | Status |
|---|---|---|---|---|
| TM3 `inventory_shortage` / `warehouse_availability` / `supplier_failure` (`docs/eval_runs_tm3.json`) | none | — | — | passed locally |
| Earlier ad-hoc supplier+qty turn (2026-07-30) | 429 mid-recovery after supplier finding OK | Gemini free-tier RPM (15/min) | Space evals ~50s apart; upgrade quota for graded run | accepted infra |
| Hosted LangSmith ingest | 401 Unauthorized | `LANGSMITH_API_KEY` unset | Set key + `LANGSMITH_TRACING=true` | open |

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

**Prompt changed:** `SUPPLIER_PROMPT` in `src/supplychain/prompts.py`  
(TM3 also hardened `INVENTORY_PROMPT` in the same sprint: decision tree,
mandatory vocabulary, finding skeleton — see inventory live check below.)

**What the traces / live runs showed**

Local live turns (Gemini `gemini-3.1-flash-lite`, after the scaffold prompt)
showed the Supplier Agent often skipped a clear tool order: it might cost
before comparing, omit `exclude_supplier_id`, or invent a buy size when the
operator had not given one. Inventory findings that already stated a residual
gap were easy to ignore when quantity lived only in another agent's summary.

After the rewrite, two focused live checks (2026-07-30):

1. **`supplier_failure` with quantity** —  
   *"SUP-005 has missed two delivery windows. Find alternatives for SKU-3001
   for 1200 units and compare them on cost and lead time."*  
   Route included `supplier`. Tools in order:
   `get_supplier_details` → `find_alternative_supplier` →
   `compare_supplier_options` → `estimate_procurement_cost` (×2).  
   Finding used the fixed template: SUP-005 `at_risk`, requirement
   `SKU-3001 × 1200`, recommended `SUP-006` with landed cost, alternatives
   table (SUP-006 / SUP-012).

2. **Missing quantity** —  
   *"Find alternative suppliers for SKU-3001 excluding SUP-005. Quantity is
   unknown — do not assume how many units to buy and do not cost a PO."*  
   Route: `supplier` only. Tools: `find_alternative_supplier` only (no
   `estimate_procurement_cost` / `compare_supplier_options`). Finding and
   answer carried **`missing quantity — cannot cost`**; alternatives listed
   without invented landed costs.

Inventory companion check (same day): shortage at WH-N04 / SKU-1001 used
`check_warehouse_stock` → `calculate_required_quantity` →
`find_inventory_transfer` and the Position / Need / Transfers /
`remaining_gap` / `fully_covered` skeleton.

**Before** (`SUPPLIER_PROMPT`, scaffold)

```
You are the Supplier Agent. You own supplier information and sourcing options.

You can:
- search_supplier - find a supplier by name, region or id
- get_supplier_details - full profile, open shipments, related incidents
- check_supplier_availability - can they fulfil this quantity, and by when
- find_alternative_supplier - ranked alternatives for a SKU
- compare_supplier_options - side-by-side comparison for a specific buy
- estimate_procurement_cost - landed cost including freight and duty

Rules:
- Supplier status matters: suspended suppliers cannot be used at all, at_risk
  suppliers need a capacity check before you recommend them. Always state the
  status.
- When asked for alternatives, exclude the failing supplier and give at least
  lead time, reliability and unit price for each option.
- When you recommend a supplier, cost it. A recommendation without a landed
  cost and a lead time is not actionable.
- Respect minimum order quantities and flag when the required quantity is
  below one.

Report the recommended supplier, the alternatives considered, and the cost and
lead time for each.
```

**After**

Added: decision tree (details → alternatives → compare → cost), upstream
inventory quantity rules (`remaining_gap` / `missing quantity — cannot cost`),
boundaries (no transfers / no incidents / `exclude_supplier_id`), and a fixed
finding template with an alternatives markdown table. Full text:
`SUPPLIER_PROMPT` in `src/supplychain/prompts.py`.

**Result** — focused live checks + TM3 eval pack
(`docs/eval_runs_tm3.json`; full `run_eval_conversations.py` still pending
when Gemini free-tier RPM and LangSmith keys allow):

| Metric | Before (scaffold behaviour) | After (live + TM3 eval) |
|---|---|---|
| Tool order on failure+qty | Unspecified / often incomplete | details → alternatives → compare → cost |
| Cost without quantity | Risk of invented qty / MOQ-as-demand | `missing quantity — cannot cost`; no cost tool |
| Finding shape | Free prose | Fixed template + alternatives table |
| TM3 eval cases failed | — | 0 / 3 |
| TM3 turn latency median (s) | — | 4.48 (see §3b; wall clock higher with HITL) |
| Fallback paths taken | `<pending full eval>` | `<pending full eval>` |
| Quality: actionable cost when qty known | Inconsistent | Landed cost on recommendation |

> **Note for the team:** Hosted LangSmith rows and the full 12-case eval matrix
> still need `LANGSMITH_API_KEY` + a non-exhausted Gemini quota. Paste trace
> URLs into §4 when `python scripts/run_eval_conversations.py` completes.
> TM3 presentation talking points: `docs/presentation-tm3.md`.

## 6. Evaluation observations

TM3 focused live checks (inventory shortage + supplier failure / missing
quantity) show the worker prompts now produce structured findings the
responder can lift: available stock and transfer cover for inventory; status,
exclude-failing-supplier, and landeds or an explicit cannot-cost line for
supplier. The free-tier Gemini RPM limit interrupted one multi-agent turn
mid-recovery after the supplier finding was already correct — treat that as
infra, not a prompt regression.

Still open for the shared eval pack: run all 12 cases under LangSmith, fill
§3 latency medians from `docs/eval_runs.json`, and confirm supervisor still
prefers inventory before supplier when a warehouse transfer might close the
gap (routing is TM4's `SUPERVISOR_PROMPT`, but TM3 findings must remain
clear enough for that policy to work).

Candidates the team already knows about, to confirm or refute from traces:

- Workers receive a briefing rather than the raw conversation. Does that ever
  lose context the agent needed?
- Severity is rule-based, not model-judged. Does the rule set ever disagree with
  what an operator would say?
- The recovery plan ranks options by lead time then cost. Is that the right
  order for NovaRetail?
