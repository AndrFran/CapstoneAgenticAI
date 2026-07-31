# NovaRetail Supply Chain Assistant

An AI-powered supply chain disruption management platform for **NovaRetail
Group** — 150 stores, 8 regional warehouses, 300+ suppliers, ~5,000 shipments a
day.

Operations teams ask one assistant in plain English. Behind it, a LangGraph
multi-agent workflow works out which supply chain function owns the request,
calls the right tools, and coordinates the recovery.

```
"SHP-2026-0002 is late into WH-N02. What's the impact and what should we do?"

  → intake: shipment_delay, SHP-2026-0002, WH-N02
  → incident_analysis: 72h late on a disrupted route; severity HIGH
  → shipment: 4 orders at risk across 3 stores, revised ETA +3 days
  → inventory: SKU-3001 below safety stock at WH-N02, 900 units coverable by transfer
  → supplier: SUP-005 at risk; SUP-006 can cover the gap in 9 days at $58.4k
  → recovery: reroute onto RTE-111 recovers 2 days for $3.3k — proposes an incident
  → [approval gate] operator approves → INC-2026-0002 created
```

## Quick start

```bash
git clone https://github.com/AndrFran/CapstoneAgenticAI.git
cd CapstoneAgenticAI

python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

pip install -r requirements.txt

copy .env.example .env          # Windows  (cp on macOS/Linux)
#  → add GOOGLE_API_KEY (or Vertex AI settings, see Configuration),
#    and LANGSMITH_API_KEY for tracing

python scripts/smoke_test.py    # verifies data + tools + graph, no API key needed
streamlit run streamlit_app.py
```

Open <http://localhost:8501>.

**Requires Python 3.11+.**

## Configuration

Everything is environment-driven; see [`.env.example`](.env.example) for the
annotated list.

| Variable | Default | Purpose |
|---|---|---|
| `GOOGLE_API_KEY` | — | **Required** unless using Vertex AI (below). Get one at [aistudio.google.com/apikey](https://aistudio.google.com/apikey). `GEMINI_API_KEY` is also accepted. |
| `GOOGLE_GENAI_USE_VERTEXAI` | `false` | Set `true` to use the Vertex AI backend instead of an API key. Needs `GOOGLE_CLOUD_PROJECT` (+ optional `GOOGLE_CLOUD_LOCATION`, default `global`) and `gcloud auth application-default login`. |
| `SUPPLYCHAIN_MODEL` | `gemini-3.1-flash-lite` | Model for every agent. `gemini-3.6-flash` is the step up for harder agentic reasoning. |
| `SUPPLYCHAIN_REASONING_EFFORT` | `medium` | Worker-agent thinking depth: `minimal` · `low` · `medium` · `high`. |
| `SUPPLYCHAIN_ROUTER_REASONING_EFFORT` | `low` | Thinking depth for intake extraction and routing. |
| `SUPPLYCHAIN_MAX_OUTPUT_TOKENS` | `4096` | Max output tokens per call. |
| `LANGSMITH_TRACING` | `false` | Set `true` (with a key) to trace every run. |
| `LANGSMITH_API_KEY` | — | Required for tracing. [smith.langchain.com](https://smith.langchain.com/settings). |
| `LANGSMITH_PROJECT` | `novaretail-supplychain` | LangSmith project name. |
| `SUPPLYCHAIN_API_BASE_URL` | — | Blank reads the JSON fixtures; set it to use the mock REST API instead. |
| `SUPPLYCHAIN_MAX_HOPS` | `8` | Supervisor loop guard. |
| `SUPPLYCHAIN_REQUIRE_APPROVAL` | `true` | Human-in-the-loop gate on write actions. |
| `SUPPLYCHAIN_MEMORY_BACKEND` | `sqlite` | `sqlite` persists conversations across restarts; `memory` is in-process only. |
| `SUPPLYCHAIN_MEMORY_PATH` | `.supplychain/conversations.sqlite` | Where conversation memory and history live. |
| `SUPPLYCHAIN_ENTITY_MEMORY_DEPTH` | `3` | Identifiers of each kind a conversation remembers. |

## What it can do

**Shipment** — track a shipment · check a delay and its cause · identify
affected orders and stores · estimate downstream delivery impact · list the
current delay backlog · check a route and its alternates.

**Inventory** — network stock position for a SKU · one SKU at one warehouse · a
warehouse's shortage picture · everything below reorder point · units required
for a target days-of-cover · inter-warehouse transfer options that respect donor
safety stock.

**Supplier** — search suppliers · full profile with open shipments · can they
fulfil this quantity by when · ranked alternatives for a SKU · side-by-side
comparison · landed procurement cost with freight and duty.

**Incident management** — assess damaged goods · rule-based severity
classification · create an incident (with approval) · check incident status ·
escalate a critical incident (with approval).

**Recovery planning** — ranked recovery options from the live position · cost of
the option against the cost of inaction · reroute a shipment (with approval) ·
stakeholder summary.

**Understanding the request** — operations staff do not type canonical ids, so
`shp 2026 2`, `SHP_2026_02` and `shipment SHP/2026/0002` all resolve to
`SHP-2026-0002`. An id that does not exist comes back with close matches ("did
you mean SHP-2026-0020?") rather than failing inside an agent.

**Conversation memory** — follow-up questions work: ask about a shipment, then
"who's the supplier on that one?" and "what would it cost to source the same
SKUs elsewhere?" without repeating an identifier. Conversations persist across
restarts and can be reopened, renamed, exported or deleted from the sidebar.

Try the sample prompts in the sidebar, or:

```
What's the status of shipment SHP-2026-0002?
Which shipments are delayed right now, and which warehouse is worst hit?
We're short on SKU-1001 at WH-N04. Can we cover it from another warehouse?
SUP-005 has missed two windows. Find alternatives for SKU-3001 and compare cost and lead time.
SHP-2026-0005 arrived damaged at WH-N08. Assess the loss and raise an incident.
What's the status of incident INC-2026-0001, and should it be escalated?
```

## Architecture

Six agents plus a supervisor, over a deterministic tool layer. Built on
**LangChain / LangGraph 1.x** (`create_agent`, `init_chat_model`) with
**Google AI (Gemini)** as the model provider.

```
intake → supervisor ⇄ { incident_analysis · shipment · inventory · supplier · recovery }
                    → recovery → approval (human) → supervisor
                    → respond
```

Full diagram, state contract and design rationale:
**[`docs/architecture.md`](docs/architecture.md)**.

Two properties are enforced by the graph's structure rather than by prompting:

- **Loop guard** — the supervisor counts hops and forces a response past
  `SUPPLYCHAIN_MAX_HOPS`.
- **Human-in-the-loop** — the Recovery Agent has no write tools, only
  `propose_*` tools. The graph reads the proposal back out of the agent's own
  tool calls, interrupts for a human decision, and only then executes. The model
  cannot skip the gate.

The system also degrades rather than failing: if the structured-output call for
intake or routing fails, deterministic regex extraction and a fixed routing
table take over, and the UI shows which path was used.

## Folder structure

```
.
├── streamlit_app.py                 # Chat UI (TM1)
├── .streamlit/config.toml           # Theme: light + dark palettes (TM1)
├── assets/favicon.svg               # Brand mark, local so it works offline
├── requirements.txt                 # Pinned dependencies
├── pyproject.toml                   # Package metadata + pytest config
├── .env.example                     # Annotated configuration
├── src/supplychain/
│   ├── graph.py                     # LangGraph workflow, routing, HITL (TM4)
│   ├── state.py                     # Shared state contract (TM4)
│   ├── runner.py                    # run_turn / resume_turn API (TM4)
│   ├── actions.py                   # The only module that writes (TM4)
│   ├── config.py                    # Environment-driven settings
│   ├── llm.py                       # Model factory (Google AI / Gemini)
│   ├── memory.py                    # Conversation memory + history (TM1)
│   ├── observability.py             # LangSmith tracing + run config
│   ├── prompts.py                   # Every system prompt, versioned
│   ├── agents/
│   │   ├── base.py                  # Worker factory, proposal extraction (TM4)
│   │   ├── intake.py                # Request Intake Agent (TM1)
│   │   ├── supervisor.py            # Supervisor Agent (TM4)
│   │   └── responder.py             # Final response (TM4)
│   ├── tools/
│   │   ├── shipment.py              # 9 tools (TM2)
│   │   ├── inventory.py             # 6 tools (TM3)
│   │   ├── supplier.py              # 6 tools (TM3)
│   │   ├── incident.py              # 6 tools (TM1)
│   │   ├── recovery.py              # 6 tools (TM4)
│   │   └── common.py                # JSON / error conventions
│   ├── ui/
│   │   ├── theme.py                 # Stylesheet + shipment components (TM1)
│   │   ├── overview.py              # Read-only network snapshot (TM1)
│   │   └── visuals.py               # Charts behind each answer (TM1)
│   └── data/
│       ├── access.py                # JSON fixtures or mock REST API
│       ├── mock/*.json              # Committed dataset (seeded, reproducible)
│       └── runtime/                 # Approved writes (git-ignored)
├── scripts/
│   ├── generate_mock_data.py        # Regenerate the dataset
│   ├── smoke_test.py                # Pre-flight check, no API key needed
│   └── run_eval_conversations.py    # 12 traced conversations + latency stats
├── tests/                           # 317 tests, none need an API key
│   ├── test_data_access.py          # Fixtures, relationships, runtime writes
│   ├── test_tools.py                # All 34 tools, exact numbers
│   ├── test_graph.py                # Routing, loop guard, HITL, actions
│   ├── test_shipment_agent.py       # Shipment Agent behaviour (fake LLM)
│   ├── test_intake.py               # Normalisation, validation, entity memory
│   ├── test_memory.py               # Conversation memory + history, both back ends
│   ├── test_prompts.py              # Prompt versioning reaches LangSmith
│   ├── test_config_llm.py           # Google AI wiring, reasoning effort
│   ├── test_gemini_schemas.py       # Tool schemas convert for Gemini
│   ├── test_ui.py                   # Streamlit AppTest chat flow
│   ├── test_ui_theme.py             # KPI strip agrees with the tools, escaping
│   ├── test_ui_visuals.py           # Panel dispatch, lane geometry, tool parity
│   └── test_live_agents.py          # Opt-in: real Gemini calls (--live)
└── docs/
    ├── architecture.md              # Architecture + diagram
    ├── team-responsibilities.md     # Who owns what
    ├── presentation-tm3.md          # TM3 presentation talking points
    ├── handoff-inventory-supplier-recovery.md  # TM3 cross-agent handoff
    ├── eval_runs_tm3.json           # TM3-focused eval results
    └── langsmith-report.md          # Tracing / evaluation report
```

## Data

The dataset is generated from a fixed seed and committed, so every team member
and grader sees the same numbers:

8 warehouses · 12 products · 12 suppliers · 12 routes · 28 shipments · 57 orders
· 96 inventory positions · 1 seeded incident.

```bash
python scripts/generate_mock_data.py      # regenerate after editing the script
```

Scripted scenarios in the data: `SHP-2026-0002` is 72h late on a disrupted route
from an at-risk supplier; `SHP-2026-0005` arrived damaged; `SUP-010` is
suspended; `SKU-1001` at `WH-N04` and `SKU-3001` at `WH-N02` are short.

Approved write actions land in `src/supplychain/data/runtime/` and are layered
over the fixtures on read, so a demo never dirties the committed dataset. Reset
with `python -c "import sys; sys.path.insert(0,'src'); from supplychain.data import access; access.reset_runtime_store()"`.

**Switching to the mock REST API:** set `SUPPLYCHAIN_API_BASE_URL` and every
collection is fetched from `{base}/{collection}` (accepting either a bare list or
a `{"data": [...]}` envelope). No tool code changes.

## Testing

```bash
pytest                        # 269 tests
python scripts/smoke_test.py  # data + every tool + graph compile
```

Neither needs an API key — tools are deterministic and the graph builds agents
lazily. That is deliberate: it keeps CI cheap and isolates data bugs from model
behaviour. `tests/conftest.py` enforces it: even with a real key in `.env`, the
suite removes it and pins conversation memory to the in-process back end, so a
test run never calls the API or touches your real conversation history.

To check the model path itself — before a demo, or after changing a prompt or
the model:

```bash
pytest tests/test_live_agents.py --live
```

Eight tests, real Gemini calls, ~15s: intake extraction and normalisation,
unsupported-request and missing-information handling, reference resolution from
conversation memory, and that the Incident Analysis Agent uses the severity rule
engine and the duplicate check rather than judging for itself.

`test_gemini_schemas.py` is worth knowing about: `bind_tools` converts tool
schemas *lazily*, so a tool signature Gemini cannot express would otherwise only
fail on the first live request. That test converts all 33 up front and rejects
parameter types outside scalars and lists of scalars.

## LangSmith tracing and evaluation

```bash
# .env: LANGSMITH_TRACING=true and LANGSMITH_API_KEY=...
python scripts/run_eval_conversations.py
```

Runs 12 conversations (14 turns) covering every functional requirement plus the
unsupported-request and missing-information cases, each on its own thread and
tagged `eval`. Prints latency statistics and writes `docs/eval_runs.json`.

On a **free-tier** Gemini key (15 requests/minute) a full run will hit the
quota — one multi-agent turn is several requests. The harness retries quota
errors automatically; add `--pace 20` to space turns out and let the whole set
through:

```bash
python scripts/run_eval_conversations.py --pace 20
```

Filter in LangSmith with `tag:eval`. Write-up template:
[`docs/langsmith-report.md`](docs/langsmith-report.md).

## Deployment

### Streamlit Community Cloud

1. Push to GitHub.
2. On [share.streamlit.io](https://share.streamlit.io), create an app from the
   repo with **main file** `streamlit_app.py`.
3. Add secrets under **Advanced settings → Secrets**:

   ```toml
   GOOGLE_API_KEY = "AIza..."
   LANGSMITH_TRACING = "true"
   LANGSMITH_API_KEY = "lsv2_..."
   LANGSMITH_PROJECT = "novaretail-supplychain"
   ```

4. Deploy. Streamlit Cloud installs `requirements.txt`; `streamlit_app.py` adds
   `src/` to the path itself, so no install step is required.

Notes for a real deployment:

- Conversation memory is an in-process `MemorySaver`, so it resets when the app
  restarts and is not shared between replicas. Pass a durable checkpointer to
  `build_graph()` for anything beyond a demo.
- The runtime write store is local disk and is ephemeral on Streamlit Cloud.
- Never commit `.env`; it is git-ignored.

### Local demo (no internet)

```bash
streamlit run streamlit_app.py --server.port 8501 --server.headless true
```

## Team

Four engineers, one part of the solution each — see
[`docs/team-responsibilities.md`](docs/team-responsibilities.md) for the
file-level ownership map.

| Member | Role |
|---|---|
| TM1 | Request Intake & Incident Analysis Agent Engineer (+ chat UI) |
| TM2 | Shipment & Order Impact Agent Engineer |
| TM3 | Inventory & Supplier Agent Engineer |
| TM4 | Recovery & Supervisor Agent Engineer (+ workflow) |

LangSmith tracing, prompt evaluation, testing, deployment, documentation, the
architecture diagram and the final presentation are shared by all four.

## Future enhancements

- Durable checkpointing (Postgres) for real conversation history across restarts.
- Push notifications: email / Teams via the notification system in the client's
  stack, wired as an approved write action.
- A proactive monitor that watches routes and inventory and raises incidents
  before an operator asks.
- Learned severity thresholds from historical outcomes, replacing the fixed
  rule table.
- Per-agent evaluation datasets in LangSmith with automatic regression gates in
  CI.
- Role-based access control on write actions (who may approve what).
