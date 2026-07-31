# Presentation section — Team Member 1

Request Intake & Incident Analysis Agent Engineer

## Role

Own the front door and the first judgement: the chat UI, the Request Intake
Agent, the Incident Analysis tools and severity rules, conversation memory and
history, and the prompt module with its versioning machinery.

## Agents

| Agent | Job | Kind |
|---|---|---|
| **Intake** | Classify the request, extract and normalise every identifier, resolve references to earlier turns, flag what is missing or unsupported | Structured output, no tools |
| **Incident Analysis** | Establish the facts, check for duplicates, classify severity by rule | ReAct, 7 tools |

## The one idea to lead with

**Intake is four stages, and only one of them is the model.**

```
1. LLM          classify intent, summarise it
2. deterministic  extract + normalise    "shp 2026 2" -> SHP-2026-0002
3. deterministic  validate against data  unknown id -> missing_information + close matches
4. deterministic  entity memory          "that shipment" -> the id from turn 1
```

Everything a wrong answer would be built on — the identifiers — is handled by
code, not by the model. `analyse_request` never raises; if the LLM call fails
entirely, `_regex_intake` extracts by pattern and the turn continues with
`request.extracted_by == "regex"` visible in the UI and in the trace.

That is the whole argument for the design: the model is used for the thing it
is good at (reading intent from sloppy prose) and kept away from the thing it
is bad at (inventing plausible identifiers).

## Invariants (demo talking points)

1. **An unknown id never reaches an agent.** It becomes
   `missing_information` with close-match suggestions instead.
2. **Severity is a tool, not an opinion.** `classify_incident_severity` applies
   the rules over live data; `INCIDENT_ANALYSIS_PROMPT` says *"do not assign
   severity from your own judgement, and do not argue with the result."*
3. **Entity memory is the one state field never reset per turn.** Everything
   else in `intake_node`'s reset block is per-turn; `conversation_entities`
   is what makes turn 3 resolve "that shipment".
4. **Intake cannot write.** Nothing it produces touches data; incidents are
   proposed by Recovery and approved by a human.
5. **Memory and history are two different things.** The checkpointer holds
   messages and workflow state (and makes the approval interrupt resumable);
   the `conversations` table indexes which threads exist, because a
   checkpointer has no notion of that and the UI's conversation list needs it.

## Prompt design (what I own and what changed)

| Prompt | Version | What it does |
|---|---|---|
| `SHARED_CONTEXT` | v2 | Client scenario, identifier conventions, four grounding rules. Injected into all nine prompts. |
| `INTAKE_PROMPT` | v2 | **Few-shot**: five worked examples, chosen to cover five different failure shapes. |
| `INCIDENT_ANALYSIS_PROMPT` | v2 | Fixed work order, duplicate check, severity deferral, explicit no-write boundary. |

`INTAKE_PROMPT` v2 is the clearest example of few-shot prompting in the
project, and intake is the natural place for it: it is the one agent whose
output is a fixed schema, so an example can show the whole answer. A ReAct
worker's output is a trajectory of tool calls, which does not fit in an
example. The five examples are deliberately *different failure shapes* rather
than five similar cases — sloppy ids, multi-entity, under-specified, a
referring expression, and an unsupported request.

I also own the versioning machinery every other member's prompt work depends
on: `PROMPT_VERSIONS`, `CHANGELOG`, and `prompt_versions()`, which
`observability.run_config` attaches to every LangSmith run so two runs can be
compared knowing which prompt produced each.

## Demo scripts (live UI)

Use **New conversation** between demos. Open **Workflow trace** under each
answer — that is where `extracted_by`, the route and the severity signals are.

All five below were run against the committed fixtures before being written
down; the expectations are observed, not hoped for.

1. **Sloppy identifiers**
   `whats up with shp 2026 2 into wh n2`
   Expect: normalised to `SHP-2026-0002` / `WH-N02`, `status_query`, routed to
   shipment. Point at the trace: the model never saw a padded id — stage 2 did
   that.

2. **Unknown identifier** — the strongest single demo
   `what is the status of SHP-9999-0001`
   Expect: **no agent runs**. `missing_information` reads *"SHP-9999-0001 does
   not exist in our systems. Did you mean SHP-2026-0001, SHP-2026-0021,
   SHP-2026-0020?"* Contrast with what an ungated agent would have done:
   called a tool, got an error, and improvised.

3. **Unsupported request**
   `can you reset my email password`
   Expect: `is_supported: false`, `incident_type: other`, declined without
   touching a tool.

4. **Entity memory across turns** — same conversation, three turns
   `SHP-2026-0002 is late into WH-N02 - what's the impact?`
   then `and who's the supplier on that one?`
   then `is that supplier reliable?`
   Expect: turn 2 resolves "that one" to `SHP-2026-0002`, turn 3 carries the
   supplier id forward. The trace shows `resolved_from_memory`.

5. **Incident analysis and severity**
   `A pallet of SKU-3001 arrived damaged at WH-N02 from SUP-005.`
   Expect: facts pulled, `find_related_incidents` checked against the seeded
   `INC-2026-0001`, `classify_incident_severity` called, and the severity
   quoted with the numbers that drove it. Say out loud that the agent did not
   *decide* the severity.

## Memory and history demo

Worth doing because it is the least expected thing in the room:

1. Ask something that ends in an approval gate (a reroute).
2. **Restart the app** (or just reload the browser).
3. Reopen the conversation from the sidebar.

The history is there, and so is the approval gate — `runner.pending_approval`
rebuilds it from the checkpoint. Approve it and the write executes.

Caveat to state honestly if asked: this survives a restart wherever the SQLite
file survives. Streamlit Cloud's filesystem is ephemeral across redeploys, so a
redeploy starts clean; changing that needs a hosted database, not a code fix.

## Running the demo safely

The free Gemini tier allows **15 requests/minute** and **500/day per model**,
and one multi-agent turn is several requests. Demo 4 is three turns and demo 5
is a full multi-agent turn.

- Rehearse on a different model than the one you will present on
  (`SUPPLYCHAIN_MODEL`) — the daily quota is per model, so this genuinely
  gives you a second allowance.
- Leave 20-30 seconds between demos. The retry logic will wait out a rate
  limit, but silence on stage is worse than pacing.
- **Backup**: have the LangSmith project open in a second tab with a good
  trace already loaded. If the quota goes mid-demo, walk the recorded trace
  instead. It shows more than the UI does anyway.
- If a worker does fail, that is a demo too: the answer says which check is
  missing rather than pretending, which is `_worker_failure` plus
  `RESPONDER_PROMPT` v2 working as designed.

## Files owned

| Path | What |
|---|---|
| `streamlit_app.py` | Chat UI, conversation browser, approval panel, trace view |
| `src/supplychain/agents/intake.py` | Extraction, normalisation, validation, entity memory |
| `src/supplychain/memory.py` | Conversation memory (checkpointer) + history (index) |
| `src/supplychain/tools/incident.py` | Incident tools + severity rules |
| `src/supplychain/prompts.py` | `SHARED_CONTEXT`, `INTAKE_PROMPT`, `INCIDENT_ANALYSIS_PROMPT`, and the versioning machinery |
| `tests/test_intake.py`, `test_memory.py`, `test_prompts.py`, `test_ui.py`, `test_live_agents.py` | My tests |

## Shared with the team

LangSmith tracing and the evaluation set, the UI theme and answer
visualisations, deployment — collaboration, not sole ownership.
