# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A LangGraph multi-agent supply chain disruption management platform (university
capstone, built for a fictional client "NovaRetail Group"). A Streamlit chat UI
in front, six agents plus a supervisor behind, over a deterministic tool layer
reading mock JSON data.

**Stack:** LangChain / LangGraph **1.x**, Google AI (Gemini) via
`langchain-google-genai`, Streamlit, Python 3.11+.

## Commands

A `.venv` exists at the repo root. On Windows PowerShell, prefix with
`.\.venv\Scripts\python.exe -m` if it is not activated.

```bash
pytest                                   # 240 tests, no API key needed
pytest tests/test_tools.py               # one file
pytest tests/test_tools.py::test_track_shipment_returns_full_record
pytest -k "severity or transfer"         # by name
pytest -o addopts="" --tb=short          # pyproject sets -q; this restores the summary line

python scripts/smoke_test.py             # data + all 33 tools + graph compile, no API key
python scripts/generate_mock_data.py     # regenerate the committed JSON fixtures
python scripts/run_eval_conversations.py # 12 traced conversations + latency stats (needs keys)
python scripts/run_eval_conversations.py --case delay_impact   # one case

streamlit run streamlit_app.py
```

`pytest` config lives in `pyproject.toml` (`pythonpath = ["src"]`).

## Design invariants — do not break these

**1. Nothing needs an API key except a live model call.** The full test suite,
the smoke test and graph compilation all run with no key. Agents are built
lazily (`agents/base.get_worker` is `lru_cache`d and only constructs a model
when first invoked), and `llm._require_key` raises `LLMNotConfiguredError` at
that point. Keep it this way — it is what makes CI free and isolates data bugs
from model behaviour.

**2. The human-in-the-loop gate is structural, not a prompt instruction.** The
Recovery Agent has *no* write tools. It has `propose_incident`,
`propose_reroute`, `propose_escalation`, which only describe the action. The
flow is:

```
recovery agent calls propose_*  →  agents/base.find_pending_proposal() reads it
back out of the agent's own tool calls  →  graph routes to the `approval` node
→  interrupt()  →  human decides  →  actions.execute()
```

`actions.py` is the **only** module that writes. The `approval` node must stay
side-effect-free before its `interrupt()`, because LangGraph re-enters the node
on resume. `tests/test_graph.py::test_only_recovery_can_propose_writes` enforces
the tool split.

**3. State reducers are deliberately absent on per-turn fields.** In
`state.SupplyChainState`, only `messages` has a reducer (`add_messages`).
`findings`, `hops`, `visited` and `executed_actions` are last-write-wins and are
merged explicitly by the nodes (`state.merge_findings`), then **reset in
`graph.intake_node`** at the start of every turn. Adding an accumulating reducer
to any of them silently breaks multi-turn conversations: the hop limit exhausts
after a few turns, and turn 2 gets answered with turn 1's findings still
attached.

**4. Two deterministic degradation paths exist and are load-bearing.** If the
structured-output call fails, `agents/intake._regex_intake` extracts identifiers
by regex, and `agents/supervisor._fallback_decision` applies a fixed routing
table. Both surface in the result (`request.extracted_by`, `route_reason` ending
in `(fallback policy)`). `test_fallback_policy_terminates_from_every_state`
walks the fallback table to exhaustion for every incident type — keep it
terminating.

**5. Tool parameter types are constrained by Gemini's function-calling schema.**
Scalars and lists of scalars only (`str`, `int`, `float`, `bool`, `list[str]`,
and their `| None` forms). `bind_tools` converts **lazily**, so a bad signature
would only fail on the first live request —
`tests/test_gemini_schemas.py` converts all 33 tools up front and enforces the
allowlist. Nested containers and dict params will fail that test.

**6. Tool failures are data, never exceptions.** Every tool returns a JSON
string via `tools/common.as_json`, and a bad id returns
`tools/common.error(msg, hint=..., sample_ids=[...])` so the agent can
self-correct. Raising instead aborts the agent loop.

**7. Date maths uses `tools/common.SCENARIO_TODAY` (2026-07-30), not the wall
clock.** The fixtures are generated around that date; using `date.today()` makes
ETAs and delays nonsensical.

**8. `conversation_entities` is the one state field that must NOT be reset per
turn.** It is the entity memory that makes "that shipment" resolve on turn 3.
Everything else in `intake_node`'s reset block is per-turn; this one is not.

**9. The test suite is hermetic and must stay that way.** `tests/conftest.py`
has two autouse fixtures that delete `GOOGLE_API_KEY`/`GEMINI_API_KEY` and pin
`SUPPLYCHAIN_MEMORY_BACKEND=memory`. Without them a developer with a real key in
`.env` would have the suite make live Gemini calls (slow, costly,
non-deterministic) and write to their real conversation history. A test that
needs a model object sets the key itself — see `tests/test_config_llm.py`.

## Architecture

```
START → intake → supervisor ⇄ { incident_analysis · shipment · inventory · supplier · recovery }
                           → recovery → approval (human) → supervisor
                           → respond → END
```

The supervisor is the only node that decides where work goes; every worker
returns to it; `respond` is the single exit. `graph.supervisor_node` counts hops
and forces `respond` past `SUPPLYCHAIN_MAX_HOPS`.

**Agent kinds** (all in `src/supplychain/agents/`):

- `intake.py` and `supervisor.py` — structured output only, no tools. Both use
  `llm.get_structured_llm()` (lower reasoning effort, smaller output budget).
- `base.py` — factory for the five ReAct workers, using LangChain 1.x
  **`create_agent(model, tools, system_prompt=...)`** (not
  `langgraph.prebuilt.create_react_agent`). Compiled worker nodes are named
  `model`/`tools`.
- `responder.py` — one LLM call that writes the single user-facing answer.

**Workers never see the raw conversation.** `base.build_context_block` renders
the intake summary plus other agents' findings into a briefing, and
`base.run_worker` invokes the agent with just that. Keeps traces focused and
token cost predictable.

**Layer boundaries:**

| Layer | Rule |
|---|---|
| `data/access.py` | The only module that reads files or makes HTTP calls. Reads JSON fixtures, or the mock REST API when `SUPPLYCHAIN_API_BASE_URL` is set. |
| `tools/*.py` | Plain Python over `access.py`. No LLM. Unit-tested for exact numbers. |
| `actions.py` | The only module that writes. Called only by the approval node. |
| `llm.py` | The only module that names a provider (`init_chat_model` + `MODEL_PROVIDER = "google_genai"`). |
| `config.py` | The only module that reads `os.environ`. |
| `prompts.py` | Every system prompt, plus `PROMPT_VERSIONS` and `CHANGELOG`. Bump the version in the same commit as a prompt change — `observability.run_config` ships versions to LangSmith so runs can be compared. |
| `memory.py` | Conversation memory (the checkpointer) and history (the conversation index). The only module that knows a checkpointer exists. |

`runner.py` (`run_turn` / `resume_turn` / `health`, plus the conversation
history helpers) is the API the UI and scripts use — do not have callers touch
the graph, the checkpointer or the store directly.

**Intake is four stages, only one of which is the model** (`agents/intake.py`).
The LLM classifies and summarises intent; around it sit deterministic
extraction + normalisation (`shp 2026 2` → `SHP-2026-0002`), validation against
the data layer (an unknown id becomes `missing_information` with close-match
suggestions rather than reaching an agent), and entity memory carry-forward.
`analyse_request` returns an `IntakeOutcome` and never raises.

## Conversation memory and history

Two different things, both in `memory.py`:

- **Memory** — the LangGraph checkpointer, keyed by `thread_id`. Holds messages
  and workflow state; it is also what makes the approval `interrupt()`
  resumable.
- **History** — a `conversations` table indexing which threads exist, with
  title/turns/last severity. LangGraph checkpointers have no notion of "which
  threads exist", so the UI's conversation list needs this index.

Default back end is SQLite at `.supplychain/conversations.sqlite` (git-ignored),
so both survive a restart; `SUPPLYCHAIN_MEMORY_BACKEND=memory` is in-process
only. A failure to open the database degrades to in-process rather than taking
the app down. Deleting a conversation drops its checkpoint rows too, by
discovering tables with a `thread_id` column rather than hard-coding LangGraph's
schema.

## Data layer

Fixtures in `src/supplychain/data/mock/*.json` are **generated and committed**.
To change them, edit `scripts/generate_mock_data.py` (fixed seed `20260730`),
re-run it, and commit the regenerated JSON in the same commit.

Approved write actions go to `src/supplychain/data/runtime/*.json`
(git-ignored) and are layered over the fixtures on read, so demos never dirty
the committed dataset. `access.reset_runtime_store()` clears it —
`tests/conftest.py` does this around every test.

**Data invariant:** a route listed in another route's `alternate_route_ids`
must serve the same `destination_warehouse_id`, because `propose_reroute`
validates that and would otherwise reject the only alternate the recovery plan
offers.

## Configuration

All environment-driven via `config.Settings`; see `.env.example`. Notable:

- `GOOGLE_API_KEY` (or `GEMINI_API_KEY`) — required for any live call.
- `SUPPLYCHAIN_MODEL` — default `gemini-3.1-flash-lite`; `gemini-3.6-flash` is
  the step up for harder agentic reasoning.
- `SUPPLYCHAIN_REASONING_EFFORT` (`medium`) / `SUPPLYCHAIN_ROUTER_REASONING_EFFORT`
  (`low`) — map to Gemini's `thinking_level`. Invalid values fall back to the
  default rather than failing at request time.
- `SUPPLYCHAIN_REQUIRE_APPROVAL` — set `false` only for unattended demos.
- `SUPPLYCHAIN_MEMORY_BACKEND` (`sqlite` | `memory`) and
  `SUPPLYCHAIN_MEMORY_PATH` — conversation memory and history.
- `SUPPLYCHAIN_ENTITY_MEMORY_DEPTH` (`3`) — how many identifiers of each kind
  the conversation remembers.

Temperature is deliberately never set (Google recommends leaving Gemini 3.x at
default sampling and controlling depth with reasoning effort).

`get_settings()` is `lru_cache`d. After changing env vars at runtime, call
`config.reload_settings()` **and** `llm.reset_llm_cache()` — see the
`no_api_key` fixture in `tests/test_ui.py`.

## src layout without an install

The package lives in `src/` and is **not** pip-installed. Three entry points add
it to `sys.path` themselves: `streamlit_app.py`, `tests/conftest.py`, and each
script in `scripts/`. New scripts must do the same:

```python
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
```

## Shared files — edit conventions

Four people work this repo in parallel (`docs/team-responsibilities.md` has the
file-level ownership map). Three files are shared:

- `prompts.py` — edit only your own prompt constant.
- `scripts/generate_mock_data.py` — edit only your own build function, then
  re-run and commit the JSON.
- `data/access.py` — add new accessors at the bottom of the lookups section;
  do not reorganise existing ones.

Anything genuinely new belongs in its own module, not in `graph.py`.

## Extension points

| Want to… | Change |
|---|---|
| Add a tool | `@tool` in the right `tools/` module, append to that module's `*_TOOLS` list. |
| Add an agent | Prompt in `prompts.py`, tool list in `agents/base.AGENT_TOOLS`, node + edges in `graph.py`, name in `state.RouteDecision`'s Literal. |
| Swap the Gemini model | `SUPPLYCHAIN_MODEL`. |
| Swap provider entirely | `MODEL_PROVIDER` + the key in `llm.py`; nothing else names Gemini. |
| Persist conversations | Pass a durable checkpointer to `graph.build_graph()` (currently an in-process `MemorySaver`, so memory resets on restart and is not shared across replicas). |

Deeper rationale in `docs/architecture.md`.
