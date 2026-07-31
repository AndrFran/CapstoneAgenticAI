# Team responsibilities

The brief splits the work four ways. This maps each role onto the files it
owns, so two people editing at once do not collide.

## Team Member 1 — Request Intake & Incident Analysis Agent Engineer

**Status: delivered** (branch `feat/tm1-intake-incident-agents`).

**Owns**

| File | What is yours |
|---|---|
| `streamlit_app.py` | Chat UI, conversation browser, approval panel, trace view. |
| `src/supplychain/agents/intake.py` | Request Intake Agent: extraction, normalisation, validation, entity memory. |
| `src/supplychain/memory.py` | Conversation memory (checkpointer) and history (conversation index). |
| `src/supplychain/tools/incident.py` | Incident analysis tools + severity rules. |
| `src/supplychain/prompts.py` | `SHARED_CONTEXT`, `INTAKE_PROMPT`, `INCIDENT_ANALYSIS_PROMPT`, and the versioning/changelog machinery. |
| `tests/test_intake.py`, `tests/test_memory.py`, `tests/test_prompts.py`, `tests/test_ui.py`, `tests/test_live_agents.py` | Your tests. |

**Brief coverage:** Chat UI · prompt engineering · conversation memory ·
conversation history · system prompt · Request Intake Agent · Incident Analysis
Agent.

### What was built

**Request Intake Agent** — four stages, only one of which is the model:

1. The LLM classifies the request and summarises intent.
2. Deterministic extraction and normalisation: `shp 2026 2`, `SHP_2026_02` and
   `shipment SHP/2026/0002` all become `SHP-2026-0002`. Seven identifier kinds,
   plus quantity extraction that ignores digits belonging to ids and years.
3. Validation against the data layer: an id that does not exist becomes
   `missing_information` with close-match suggestions ("did you mean…") instead
   of reaching an agent and failing there.
4. Entity memory carry-forward, so "that shipment" resolves from what the
   conversation already mentioned.

The model's identifiers and the regex's are unioned, then re-normalised and
validated — the model sometimes paraphrases an id, the regex never invents one.

**Incident Analysis Agent** — added `find_related_incidents` for duplicate
detection (an open incident on the same shipment is a duplicate, not a new
problem), and extended the severity rules with downstream order exposure:
at-risk order value over $100k scores high, 3+ orders at risk or 5+ stores
exposed scores medium. Severity stays rule-based, not model judgement.

**Conversation memory and history** — `memory.py`. Memory is the checkpointer;
history is a `conversations` index so the UI can list, reopen, rename and delete
past conversations. SQLite by default, so both survive a restart. Entity memory
lives in state as `conversation_entities` and is the one field not reset per
turn.

**Prompt engineering** — every prompt now carries a version, with a `CHANGELOG`
recording why it changed. `observability.run_config` attaches all versions to
every LangSmith run, so two prompt versions can be compared in the UI. Intake
went to v2 with five worked examples; incident analysis went to v2 with the
duplicate check and an explicit no-writes boundary.

**Chat UI** — conversation browser with severity badges, per-turn intake panel
(what was understood, what was carried forward from memory, unknown ids with
suggestions, any clarification needed), workflow trace, markdown export, rename
and delete. Reopening a past conversation restores its trace metadata, not just
the text, because each turn's route/severity/intake summary is persisted
alongside the checkpoint.

**Testing** — 246 hermetic tests plus 8 opt-in live ones
(`pytest tests/test_live_agents.py --live`) that verify the model path: intake
really uses the model and normalises sloppy ids, unsupported and
under-specified requests are handled, references resolve from memory, and the
Incident Analysis Agent calls the severity rule engine and the duplicate check
instead of judging for itself.

### Notes for whoever picks this up

- Conversation memory is the checkpointer keyed by `thread_id` — do not manage a
  message list yourself. `runner.conversation_turns(thread_id)` rehydrates the
  UI; `runner.export_conversation(thread_id)` renders markdown.
- Starting a new conversation is just a new `thread_id`. Do **not** call
  `runner.reset_graph()` for that — it tears down the memory back end.
- Adding a new identifier kind means one entry in `intake.IDENTIFIER_SPECS`,
  one field on `state.IntakeResult`, and one name in `memory.ENTITY_FIELDS`.

## Team Member 2 — Shipment & Order Impact Agent Engineer

**Owns**

| File | What is yours |
|---|---|
| `src/supplychain/tools/shipment.py` | All shipment and order tools. |
| `src/supplychain/prompts.py` | `SHIPMENT_PROMPT`. |
| `scripts/generate_mock_data.py` | Shipment, order and route fixtures. |
| `src/supplychain/data/access.py` | Shipment/order accessors (shared with TM3). |
| `tests/test_tools.py` | The shipment sections. |

**Brief coverage:** shipment prompt design · shipment and order tool
development · JSON / mock API integration · Shipment Agent · agent testing ·
error handling for shipment workflows.

Error handling convention: return `common.error(...)` with a hint, never raise.
`tests/test_tools.py::test_track_shipment_unknown_id_returns_error_not_exception`
is the contract.

## Team Member 3 — Inventory & Supplier Agent Engineer

**Owns**

| File | What is yours |
|---|---|
| `src/supplychain/tools/inventory.py` | All inventory tools. |
| `src/supplychain/tools/supplier.py` | All supplier tools. |
| `src/supplychain/prompts.py` | `INVENTORY_PROMPT`, `SUPPLIER_PROMPT`. |
| `scripts/generate_mock_data.py` | Inventory, supplier and product fixtures. |
| `tests/test_tools.py` | The inventory and supplier sections. |

**Brief coverage:** inventory and supplier prompt design · inventory and
supplier tool development · mock API / JSON integration · Inventory Agent ·
Supplier Agent · agent testing.

Two invariants your tests protect: `available = on_hand - reserved` is what the
agent answers with, and a transfer never drops a donor warehouse below its
safety stock.

## Team Member 4 — Recovery & Supervisor Agent Engineer

**Status: delivered** (branch `feat/tm4-supervisor-recovery`).

**Owns**

| File | What is yours |
|---|---|
| `src/supplychain/graph.py` | Workflow, routing edges, loop guard, approval node. |
| `src/supplychain/state.py` | Shared state contract. |
| `src/supplychain/agents/supervisor.py` | Supervisor Agent + deterministic fallback. |
| `src/supplychain/agents/responder.py` | Final response generation. |
| `src/supplychain/agents/base.py` | Worker factory, proposal extraction. |
| `src/supplychain/actions.py` | Approved write execution. |
| `src/supplychain/tools/recovery.py` | Recovery tools + write proposals. |
| `src/supplychain/runner.py` | `run_turn` / `resume_turn` API for the UI. |
| `src/supplychain/prompts.py` | `SUPERVISOR_PROMPT`, `RECOVERY_PROMPT`, `RESPONDER_PROMPT`. |
| `tests/test_graph.py` | Routing, loop guard, HITL, action execution. |

**Brief coverage:** Recovery Agent · Supervisor Agent · LangGraph workflow ·
agent routing · shared state · human-in-the-loop · final response generation ·
cross-agent error handling.

If you change `state.SupplyChainState`, tell the other three — it is the
contract everyone codes against.

### What was built

**LangGraph workflow** — `START → intake → supervisor ⇄ five workers →
respond → END`, with `recovery → approval → supervisor` for writes. The
supervisor is the only node that routes; `respond` is the single exit.

**Human-in-the-loop, structurally.** The Recovery Agent has no write tools at
all — only `propose_*` tools that describe an action. `base.find_pending_proposal`
reads the proposal back out of the agent's own tool calls, the graph routes to
`approval`, and `interrupt()` hands it to a human. `actions.py` is the only
module that writes, and it is reachable only from that node. The node is
side-effect-free before the interrupt, because LangGraph re-enters it on resume.

**Routing that cannot be talked out of.** Two structural clamps sit between the
router's choice and the edge taken, because a routing rule in a prompt is a
suggestion: a read-only status query never reaches recovery and stops after two
specialists, and no agent that failed this turn is dispatched again. Plus the
hop-limit loop guard.

**Cross-agent error handling.** This was the gap. Intake fell back to regex,
the supervisor fell back to its routing table and the responder caught its own
failure — but a worker agent raising propagated all the way out of
`graph.invoke` and reached the operator as a class name, discarding the work
every earlier agent had already done. Now `graph._worker_failure` turns it into
a finding (`failed: True`), the supervisor moves on, and the responder is told
what is missing so the answer says so. Underneath, `resilience.call_with_retry`
waits out the transient failures first — chiefly `429 RESOURCE_EXHAUSTED`,
which on the free tier is the normal failure rather than an edge case.

**Fixed: gated turns lost their trace.** `resume_turn` never called
`record_turn`, and `conversation_turns` pairs metadata to assistant messages by
position — so a conversation that passed through the approval gate did not
merely lose one turn's trace, it slid every later trace onto the wrong answer,
and showed `0 turns` in the sidebar. `tests/test_runner.py` pins both.

### Notes for whoever picks this up

- A worker returning findings with `failed: True` is a contained failure, not a
  bug. `graph.failed_agents(state)` lists them.
- Add a clamp to `_apply_routing_policy`, not to `supervisor_node` — the list
  is ordered by precedence and the first override wins.
- `resilience.is_transient` is deny-by-default: an unrecognised error is
  treated as permanent. Add markers rather than inverting that.

## Shared by all four

The brief is explicit that no one person owns evaluation or deployment.

| Area | Files | Notes |
|---|---|---|
| LangSmith tracing | `src/supplychain/observability.py` | One owner per PR, reviewed by another. |
| Prompt evaluation | `scripts/run_eval_conversations.py`, `docs/langsmith-report.md` | Each member analyses their own agent's traces; the prompt improvement is written up jointly. |
| Testing | `tests/` | Each member's tests live in the section for their tools. |
| Deployment | `README.md`, `requirements.txt`, `.env.example` | Whoever deploys pairs with one other member. |
| Documentation | `docs/`, `README.md` | Each member documents their own agent. |
| Architecture diagram | `docs/architecture.md` | TM4 drafts, all four review. |
| Final presentation | — | One section each, matching the split above. |

## Working agreement that avoids merge pain

1. `prompts.py` is shared. Only edit your own prompt constants.
2. `generate_mock_data.py` is shared. Edit only your own build function, then
   re-run it and commit the regenerated JSON in the same commit.
3. `access.py` is shared. Add new accessors at the bottom of the lookups
   section; do not reorganise existing ones.
4. Anything new goes in your own module, not into `graph.py`.
5. Run `python scripts/smoke_test.py` and `pytest` before you push — neither
   needs an API key.
