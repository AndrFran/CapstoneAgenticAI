# LangSmith report

The brief requires: tracing enabled, at least 10 recorded conversations, latency
analysis, a review of failed runs, and one prompt improved from trace insights.

This report is written jointly. Each member analysed their own agent's traces,
so §5 and §6 are split by slice: TM2 covers the shipment workflows, TM3 the
inventory and supplier agents, TM4 the supervisor and recovery path.

## 1. Setup

```bash
# .env — API-key path
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=<key from https://smith.langchain.com/settings>
LANGSMITH_PROJECT=novaretail-supplychain
```

Verify before running anything: the Streamlit sidebar shows
**"Tracing to novaretail-supplychain"**, and `python scripts/smoke_test.py`
reports `llm_configured True`.

The full-suite run in §3a used the **Vertex AI auth path** instead of an AI
Studio key (`GOOGLE_GENAI_USE_VERTEXAI=true` + `GOOGLE_CLOUD_PROJECT`, gcloud
ADC). Org policy on that GCP project allows only `gemini-2.5-flash`, so
`SUPPLYCHAIN_MODEL=gemini-2.5-flash` — its latency numbers are for that model,
not the default `gemini-3.1-flash-lite`. The TM3 cases in §3b and the
supervisor work in §4b–4c were run on `gemini-3.1-flash-lite`, so **do not
compare timings across those sections**.

## 2. Recording the conversations

```bash
python scripts/run_eval_conversations.py
```

**Recorded 2026-07-30:** 12 conversations, 14 turns, all passed
(`failed_cases: none`). Results in `docs/eval_runs.json`; traces tagged `eval`
plus the case name in the `novaretail-supplychain` project (18 root runs — an
approval interrupt splits a turn into two traced runs, see §3a).

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

From `docs/eval_runs.json` → `summary.latency_seconds`
(model `gemini-2.5-flash`, reasoning effort `medium`):

| Metric | Value (s) |
|---|---|
| Turns measured | 14 |
| Min | 5.8 |
| Median | 14.39 |
| Mean | 17.33 |
| p95 | 28.44 |
| Max | 36.18 |

**Measurement caveat found while reading the traces:** for turns that hit the
approval interrupt, `latency_seconds` records only the post-approval resume
segment. The traces show the real wall-clock: `delay_impact` was **93.1s + 10.7s**
(recorded as 10.65), `incident_status` **82.0s + 5.8s**, `damaged_goods`
**54.3s + 12.3s**, `multi_turn_memory` turn 1 **91.2s + 6.8s**. Worth fixing in
`runner.py` if we want honest end-to-end numbers (flagged to TM4).

Per-node medians, read from the 18 LangSmith trace trees:

| Node | Median (s) | Notes |
|---|---|---|
| `intake` | 2.8 | Structured output at router reasoning effort. |
| `supervisor` (per hop) | 2.4 | Occasional 7–14s outliers when findings are long. |
| `incident_analysis` | 12.4 | |
| `shipment` | 10.1 | 2.7s (single tool) to 18.1s (six tool calls) — see §5a. |
| `inventory` | 13.6 | Decision-tree tools; structured finding. |
| `supplier` | 8.4 | Details → alternatives → compare/cost when qty known. |
| `recovery` | 30.0 | **Dominates every multi-agent turn** (20–43s). |
| `respond` | 3.9 | |

Observations:

- **The recovery node dominates** multi-agent turns: 42.8s of `delay_impact`'s
  93s, 36.5s of `multi_turn_memory` turn 1. It runs plan → cost → propose, each
  a model round-trip. The single highest-value latency work is there, not in
  the specialists.
- Each extra supervisor hop costs ~2.4s median. No turn hit the hop limit
  (max hops seen: 5 of 8).
- Worker time scales with tool-call count, and the traces showed redundant
  calls in the shipment agent — that became the prompt improvement in §5a.

### 3b. TM3 cases only (`docs/eval_runs_tm3.json`, 2026-07-30)

Run separately on `gemini-3.1-flash-lite`. Harness turn `latency_seconds` is
the last graph invoke, so an approval resume makes it look shorter than the
wall clock — both are shown.

| Case | Route | Hops | Turn latency (s) | Wall clock (s) | OK |
|---|---|---|---|---|---|
| `inventory_shortage` | inventory → recovery | 3 | 3.86 | 18.14 | yes |
| `warehouse_availability` | inventory | 2 | 10.0 | 10.03 | yes |
| `supplier_failure` | incident_analysis → supplier → inventory → recovery | 5 | 4.48 | 54.77 | yes |

TM3 summary (turn metric): count 3 · min 3.86 · median 4.48 · mean 6.11 ·
max 10.0. Failed cases: none.

**TM3 routing observation:** on `supplier_failure` the supervisor visited
`supplier` before `inventory`, the opposite of the written routing preference.
Inventory still closed the transfers afterwards, so this is a routing-trace
question for TM4 rather than a tool bug.

### Still open

- Does `SUPPLYCHAIN_REASONING_EFFORT=low` vs `medium` vs `high` change answer
  quality on the eval set, and at what latency cost? The value is recorded in
  every run's LangSmith metadata, so the comparison is a re-run away.
- Is `gemini-3.1-flash-lite` enough for the recovery agent, or does the harder
  planning step justify `gemini-3.6-flash`? Compare on the same eval set.
- Token usage per turn, from the LangSmith run metadata.

## 4. Failed runs

**In the recorded eval set: none.** All 12 cases produced grounded answers, the
unsupported request was declined, and the missing-information case asked for
the shipment id instead of guessing. The TM3 pack was likewise 0 of 3 failed.

Failures that occurred outside the recorded set, reviewed from their traces and
errors:

| Failure | Root cause | Fix | Status |
|---|---|---|---|
| `FAILED_PRECONDITION: Organization Policy constraint constraints/vertexai.allowedModels violated` on every agent call | GCP org policy allows only `gemini-2.5-flash`; default model is `gemini-3.1-flash-lite` | `SUPPLYCHAIN_MODEL=gemini-2.5-flash` in `.env`; probed the allowlist per model | fixed |
| `INVALID_ARGUMENT: thinking_level is not supported by this model` | The app always sent `reasoning_effort` (→ `thinking_level`), which Gemini 2.x rejects | `llm.py` now omits `reasoning_effort` for `gemini-2*` models | fixed |
| Ad-hoc supplier+quantity turn: `429` mid-recovery, after the supplier finding was already correct | Gemini free-tier RPM (15/min) | Retry with back-off, plus `--pace` — see §4d | fixed |
| Hosted LangSmith ingest: `401 Unauthorized` | `LANGSMITH_API_KEY` unset | Set the key and `LANGSMITH_TRACING=true` | fixed |

Notably, the first failure demonstrated the deterministic degradation paths
working as designed: intake fell back to regex extraction and the supervisor to
the fixed routing table, so the graph still routed correctly and the hard error
only surfaced at the worker.

What to look for specifically when reviewing a new run:

- **Fallback paths taken.** `request.extracted_by == "regex"` means the intake
  structured-output call failed; `route_reason` ending in `(fallback policy)`
  means supervisor routing failed. Both are silent degradations — count them.
- **Hop-limit hits.** `route_reason` mentioning the hop limit means the
  supervisor did not converge.
- **Tool errors.** Tool results containing an `error` field, and whether the
  agent recovered on the next step or gave up.
- **Rejected proposals.** Whether the answer correctly avoided claiming the
  action had happened.

## 4b. Findings already observed (first live run, 2026-07-30)

Two cases run live on `gemini-3.1-flash-lite`, reasoning effort `medium`, tracing
on. Four turns, all completed, no errors.

| Case | Turns | Route | Latency |
|---|---|---|---|
| `track_shipment` | 1 | shipment → incident_analysis → inventory → recovery | 50.7s, 5 hops |
| `multi_turn_memory` | 3 | shipment / shipment / supplier | 10.7s, 61.1s, 14.5s |

Median 32.6s, mean 34.2s, max 61.1s.

**Finding 1 - the supervisor over-routes simple lookups (owner TM4).**
"What's the status of shipment SHP-2026-0002?" is a `status_query`, and the
supervisor prompt says a simple status lookup should go straight to the
specialist and then to `respond`. Instead it visited four agents over five hops,
took 50.7s, and volunteered an unrequested inventory-transfer proposal — so a
read-only question ended at an approval gate. The deterministic fallback table
routes this correctly (`status_query → shipment`), so the defect is in the LLM
routing decision, not the policy. Resolved in §4c.

**Finding 2 - latency is dominated by hop count.** The same question answered in
one hop took 10.7s; in five hops, 50.7s. Roughly 10s per supervisor+agent hop at
`medium` effort. Fixing Finding 1 should cut typical status-query latency by
~4x, which is a bigger win than any model or effort change.

**Finding 3 - conversation memory works as intended (closed).** In
`multi_turn_memory`, turn 2's "that shipment" resolved to SHP-2026-0002 and turn
3's "the same SKUs" resolved to SKU-3001/SKU-3002, with no identifier repeated
by the user. Entity memory is populated deterministically in intake, so this
holds even when the structured-output call fails.

## 4c. Finding 1 resolved — supervisor over-routing (2026-07-30)

Fixed and re-measured on the same case, same model and settings.

| | Before | After |
|---|---|---|
| Route for "What's the status of SHP-2026-0002?" | shipment → incident_analysis → inventory → recovery | **shipment** |
| Supervisor hops | 5 | **2** |
| Latency | 50.7s | **11.1s** |
| Ended at an approval gate | yes (unrequested transfer proposal) | **no** |

Two changes, because a routing rule written only in a prompt is a suggestion:

1. `SUPERVISOR_PROMPT` v1 → v2: the stop condition is now explicit, with the
   status-lookup case spelled out, and over-collection is named as a failure
   rather than thoroughness.
2. `graph._constrain_read_only`: a structural clamp. A `status_query` never
   routes to `recovery`, and stops after `STATUS_QUERY_WORKER_BUDGET` (2)
   specialist agents. Two is deliberate — "status of X, and do we have stock?"
   legitimately needs two — and the clamp applies to nothing but status
   queries.

Regression check: `delay_impact` still routes incident_analysis → recovery in
5.6s, so disruptions are unaffected. Six tests in `tests/test_graph.py` pin the
clamp's boundaries, including that non-status requests still reach recovery.

**Finding 2 (latency tracks hop count) is confirmed by the same numbers**: 3
fewer hops removed 39.6s, ~13s per hop.

## 4d. Free-tier rate limits

The Gemini free tier allows **15 requests per minute** for
`gemini-3.1-flash-lite`, and one multi-agent turn is several requests. A full
12-conversation run will hit `429 RESOURCE_EXHAUSTED` partway through — it did
here, on the second case, and again on an ad-hoc TM3 supplier turn where the
recovery step died after the supplier finding was already correct.

`scripts/run_eval_conversations.py` retries quota errors, honouring the
server's suggested retry delay and backing off exponentially (4 attempts), and
takes `--pace SECONDS` to space turns out:

```bash
python scripts/run_eval_conversations.py --pace 20
```

Budget roughly 20 minutes for the full set on a free-tier key. A paid key or a
Vertex AI project needs neither flag.

## 5. Prompt improvement from trace insights

Required: at least one prompt improved based on what the traces showed. Three
were, on different agents.

### 5a. `SHIPMENT_PROMPT` — redundant tool calls (TM2)

**What the traces showed**

For the plain status question "What's the status of shipment SHP-2026-0002?"
(`track_shipment` case and again in `multi_turn_memory` turn 1), the shipment
agent opened with `get_shipment_status` and then called `check_shipment_delay`
— whose output is a **strict superset** of `get_shipment_status`'s — so the
first call bought nothing. With the follow-ups (`find_affected_orders`,
`check_delivery_route`) that made 4 calls and shipment-node times of 9.3s and
18.1s. The prompt listed the tools but gave no tool-economy guidance. Compare
`affected_orders`: one tool call, 2.7s node, 10.8s turn.

(Reading these traces needs care: `track_shipment` / `check_delivery_route`
entries appearing mid-trace after `get_shipment_details` / `check_route_status`
belong to the **incident-analysis agent's alias tools**, which internally
invoke the shipment tools — that cross-agent re-fetching is a separate,
structural observation, see §6a.)

**Before**

```
Rules:
- When a shipment is delayed, always quantify the delay and check the affected
  orders before you report back. ...
```

**After** (new first rule)

```
Rules:
- Be economical with tools. track_shipment already returns the full record
  (status, ETA, delay hours, contents, value) - do not follow it with
  get_shipment_status, and use get_shipment_status alone only when nothing
  else is needed. Never repeat a call you already have the answer to.
- When a shipment is delayed, always quantify the delay and check the affected
  orders before you report back. ...
```

**Result** — re-run of the affected cases
(`--case track_shipment --case multi_turn_memory`,
`docs/eval_runs_after_prompt.json`). The supervisor's routing differed between
runs (the after-run escalated `track_shipment` through recovery), so the fair
comparison is at the shipment-node level:

| Metric | Before | After |
|---|---|---|
| Opens with the full-record tool (`track_shipment`) | no — `get_shipment_status` first | yes, both cases |
| Redundant subset calls (`get_shipment_status` + superset) | 2 (one per case) | 0 |
| Shipment-agent tool calls, `track_shipment` case | 4 | 4 (wasted call replaced by `estimate_delivery_delay` — new information) |
| Shipment node, `track_shipment` case (s) | 9.3 | 8.0 |
| Shipment node, `multi_turn` t1 (s) | 18.1 | 8.7 |
| Failed cases | 0 | 0 |

One run per condition — treat the latency deltas as directional, not
statistical; the behavioural change (no redundant subset call, leads with the
full record) is what the prompt targeted and is visible in every after-trace.

### 5b. `SUPPLIER_PROMPT` and `INVENTORY_PROMPT` — tool order and invented quantities (TM3)

**What the traces / live runs showed**

Local live turns on `gemini-3.1-flash-lite` showed the Supplier Agent skipping a
clear tool order: it might cost before comparing, omit `exclude_supplier_id`, or
**invent a buy size when the operator had not given one**. Inventory findings
that already stated a residual gap were easy to ignore when the quantity lived
only in another agent's summary.

**Before** (`SUPPLIER_PROMPT`, scaffold)

```
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
```

**After**

Added: a decision tree (details → alternatives → compare → cost), upstream
inventory quantity rules (`remaining_gap`, and `missing quantity — cannot cost`
when there is none), explicit boundaries (no transfers, no incidents, always
pass `exclude_supplier_id`), and a fixed finding template with an alternatives
table. `INVENTORY_PROMPT` got the same treatment in the same sprint: decision
tree, mandatory vocabulary, and a Position / Need / Transfers / `remaining_gap`
/ `fully_covered` finding skeleton. Full text in `src/supplychain/prompts.py`.

**Result** — two focused live checks (2026-07-30) plus the TM3 eval pack:

1. **`supplier_failure` with a quantity** — *"…find alternatives for SKU-3001
   for 1200 units and compare them on cost and lead time."* Tools fired in
   order: `get_supplier_details` → `find_alternative_supplier` →
   `compare_supplier_options` → `estimate_procurement_cost` (×2). The finding
   used the template: SUP-005 `at_risk`, requirement `SKU-3001 × 1200`,
   recommended SUP-006 with a landed cost, alternatives table.
2. **Missing quantity** — *"…quantity is unknown."* Route: `supplier` only.
   Tools: `find_alternative_supplier` only — no costing. The finding carried
   **`missing quantity — cannot cost`** and listed alternatives without
   invented landed costs.

| Metric | Before (scaffold) | After |
|---|---|---|
| Tool order on failure+qty | unspecified, often incomplete | details → alternatives → compare → cost |
| Cost without a quantity | risk of invented qty / MOQ-as-demand | `missing quantity — cannot cost`; costing tool not called |
| Finding shape | free prose | fixed template + alternatives table |
| TM3 eval cases failed | — | 0 of 3 |
| Actionable landed cost when qty known | inconsistent | on every recommendation |

## 6. Evaluation observations

### 6a. Shipment workflows (TM2)

The shipment workflows are reliable on the happy path and on bad input: every
eval answer quoted tool-grounded numbers (72h delay, 4 orders / 3 stores on
SHP-2026-0002), and the hardened error contract (`hint` + `sample_ids` on every
shipment/order tool) is what makes a wrong id survivable mid-conversation —
`tests/test_shipment_agent.py` pins that behaviour without a model.

Latency is not evenly distributed: specialists cost seconds, recovery costs
tens of seconds, and the biggest avoidable specialist cost was redundant tool
calls (§5a). The next improvements in TM2 territory would be (a) recording
end-to-end latency across approval interrupts in `runner.py`, and (b) checking
whether `delay_backlog`'s 14.8s shipment node (one tool call, large JSON
result) justifies a summarised variant of `identify_delayed_shipments`.

A structural finding from reading the traces: **agents re-fetch each other's
data**. The incident-analysis agent's `get_shipment_details` and
`check_route_status` internally invoke `track_shipment` and
`check_delivery_route`, so on every disruption turn the same shipment record
is fetched twice — once by the shipment agent, once by incident analysis.
Workers share only summary text, not tool results; passing key tool results in
the briefing (`base.build_context_block`) would remove the duplication. Flagged
to TM4 as a candidate improvement.

### 6b. Inventory and supplier (TM3)

The rewritten worker prompts now produce structured findings the responder can
lift directly: available stock and transfer cover for inventory; status,
excluded failing supplier, and either a landed cost or an explicit
cannot-cost line for supplier. That matters beyond readability — the supervisor
routes on finding text, so a vague finding is a routing problem as well as a
presentation one.

The free-tier RPM limit interrupted one multi-agent turn mid-recovery after the
supplier finding was already correct. That is infrastructure, not a prompt
regression, and it is what §4d addresses.

Still open for TM3: confirm the supervisor prefers inventory before supplier
when a warehouse transfer might close the gap (see the routing observation in
§3b — the policy is TM4's `SUPERVISOR_PROMPT`, but TM3's findings have to stay
clear enough for that policy to work).

### 6c. Cross-cutting

Candidates the team already knew about, confirmed or refuted from traces:

- **Workers receive a briefing rather than the raw conversation.** Multi-turn
  case turn 2 ("Who's the supplier on that shipment?") resolved the reference
  correctly — no lost context observed in this set.
- **Severity is rule-based, not model-judged.** No disagreement observed; the
  damaged-goods and delay cases both got plausible severities.
- **The recovery plan ranks options by lead time then cost.** Unreviewed in
  this run — recovery is TM4 scope, and it is also the node that dominates
  latency (§3a), so it is the obvious next target.
