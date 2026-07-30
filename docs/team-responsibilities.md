# Team responsibilities

The brief splits the work four ways. This maps each role onto the files it
owns, so two people editing at once do not collide.

## Team Member 1 — Request Intake & Incident Analysis Agent Engineer

**Owns**

| File | What is yours |
|---|---|
| `streamlit_app.py` | Chat UI, conversation history, approval panel, trace view. |
| `src/supplychain/agents/intake.py` | Request Intake Agent + regex fallback. |
| `src/supplychain/tools/incident.py` | Incident analysis tools + severity rules. |
| `src/supplychain/prompts.py` | `SHARED_CONTEXT`, `INTAKE_PROMPT`, `INCIDENT_ANALYSIS_PROMPT`. |

**Brief coverage:** Chat UI · prompt engineering · conversation memory ·
conversation history · system prompt · Request Intake Agent · Incident Analysis
Agent.

Conversation memory is the graph's checkpointer keyed by `thread_id` — you do
not manage a message list yourself. `runner.conversation_messages(thread_id)`
rehydrates history if you need it.

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
