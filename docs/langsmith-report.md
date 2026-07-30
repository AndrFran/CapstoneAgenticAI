# LangSmith report

The brief requires: tracing enabled, at least 10 recorded conversations, latency
analysis, a review of failed runs, and one prompt improved from trace insights.
This report covers the first full traced evaluation run (2026-07-30, TM2).

## 1. Setup

```bash
# .env — API-key path
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=<key from https://smith.langchain.com/settings>
LANGSMITH_PROJECT=novaretail-supplychain
```

This run used the **Vertex AI auth path** instead of an AI Studio key
(`GOOGLE_GENAI_USE_VERTEXAI=true` + `GOOGLE_CLOUD_PROJECT`, gcloud ADC). Org
policy on the GCP project allows only `gemini-2.5-flash`, so
`SUPPLYCHAIN_MODEL=gemini-2.5-flash` — every latency number below is for that
model, not the default `gemini-3.1-flash-lite`.

## 2. Recording the conversations

```bash
python scripts/run_eval_conversations.py
```

**Recorded 2026-07-30:** 12 conversations, 14 turns, all passed
(`failed_cases: none`). Results in `docs/eval_runs.json`; traces tagged `eval`
plus the case name in the `novaretail-supplychain` project (18 root runs — an
approval interrupt splits a turn into two traced runs, see §3).

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
| `shipment` | 10.1 | 2.7s (single tool) to 18.1s (six tool calls) — see §5. |
| `inventory` | 13.6 | |
| `supplier` | 8.4 | |
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
  calls in the shipment agent — that became the prompt improvement in §5.

## 4. Failed runs

**In the recorded eval set: none.** All 12 cases produced grounded answers, the
unsupported request was declined, and the missing-information case asked for
the shipment id instead of guessing.

Two real failures occurred *before* the eval run, during Vertex bring-up, and
were reviewed from their traces / errors:

| Failure | Root cause | Fix | Status |
|---|---|---|---|
| `FAILED_PRECONDITION: Organization Policy constraint constraints/vertexai.allowedModels violated` on every agent call | GCP org policy allows only `gemini-2.5-flash`; default model is `gemini-3.1-flash-lite` | `SUPPLYCHAIN_MODEL=gemini-2.5-flash` in `.env`; probed the allowlist per model | fixed |
| `INVALID_ARGUMENT: thinking_level is not supported by this model` | The app always sent `reasoning_effort` (→ `thinking_level`), which Gemini 2.x rejects | `llm.py` now omits `reasoning_effort` for `gemini-2*` models | fixed |

Notably, the first failure demonstrated the deterministic degradation paths
working as designed: intake fell back to regex extraction and the supervisor to
the fixed routing table, so the graph still routed correctly and the hard error
only surfaced at the worker.

## 5. Prompt improvement from trace insights

**Prompt changed:** `SHIPMENT_PROMPT` (`src/supplychain/prompts.py`)

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
structural observation, see §6.)

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

## 6. Evaluation observations (TM2 scope)

The shipment workflows are reliable on the happy path and on bad input: every
eval answer quoted tool-grounded numbers (72h delay, 4 orders / 3 stores on
SHP-2026-0002), and the hardened error contract (`hint` + `sample_ids` on every
shipment/order tool) is what makes a wrong id survivable mid-conversation —
`tests/test_shipment_agent.py` pins that behaviour without a model.

Latency is not evenly distributed: specialists cost seconds, recovery costs
tens of seconds, and the biggest avoidable specialist cost was redundant tool
calls (§5). The next improvements in TM2 territory would be (a) recording
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

Candidates the team already knows about, to confirm or refute from traces:

- Workers receive a briefing rather than the raw conversation. Multi-turn case
  turn 2 ("Who's the supplier on that shipment?") resolved the reference
  correctly — no lost context observed in this set.
- Severity is rule-based, not model-judged. No disagreement observed; the
  damaged-goods and delay cases both got plausible severities.
- The recovery plan ranks options by lead time then cost — unreviewed in this
  run (recovery is TM4 scope).
