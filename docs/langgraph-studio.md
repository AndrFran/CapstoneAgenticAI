# LangGraph Studio

Studio is the graph view that sits next to the traces in LangSmith. A trace
tells you what happened after the fact, in a tree; Studio draws the actual
topology — intake → supervisor → the five workers → approval → respond — and
lets you run a turn through it, stop at any node, read the state, edit it, and
resume. The approval `interrupt()` shows up as something you act on rather than
something you read about.

## What can and cannot be "in Studio"

Studio renders whatever a **LangGraph Server** exposes. That is the one thing
the Streamlit deployment is not: Streamlit Cloud runs the compiled graph
in-process behind a chat UI and speaks no LangGraph protocol, so there is no
endpoint for Studio to attach to. Nothing about `langgraph.json` changes that.

The split is therefore:

| Surface | Where it runs | What LangSmith shows |
|---|---|---|
| Streamlit app (deployed) | Streamlit Cloud | **Traces** — every node, model call and tool call, per thread. Already working; see [langsmith-report.md](langsmith-report.md). |
| LangGraph Server (`langgraph dev`, or LangGraph Platform) | Your machine, or Platform | **Studio** — the graph itself, steppable, plus traces from those runs. |

Both serve the same compiled graph object (`supplychain/studio.py`), so the
picture Studio draws is the system rather than a diagram of it that can drift.

## Running it

The CLI pulls a dependency set that downgrades `protobuf` (`grpcio-tools` pins
`<7`, the app venv is on 7.x), so **install it into its own environment**
rather than the project `.venv`:

```bash
uv tool install "langgraph-cli[inmem]"
```

or, without `uv`, a throwaway virtualenv with the project's runtime deps plus
`langgraph-cli[inmem]`.

`langgraph.json` names the graph as a dotted module path
(`supplychain.studio:make_graph`), so the package has to be importable. This is
the one workflow that needs it installed — everything else adds `src/` to
`sys.path` itself:

```bash
pip install -e .
```

A file path (`./src/supplychain/studio.py:make_graph`) also parses, but the
server loads it as a standalone module and every relative import in the package
fails. `test_studio.py` pins the dotted form.

Then, from the repo root:

```bash
langgraph dev
```

It reads `.env` for `GOOGLE_API_KEY` and the LangSmith keys, serves on
`http://127.0.0.1:2024`, and opens

```
https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024
```

Studio is the hosted LangSmith UI talking to your local server; the graph and
your keys stay on your machine.

## How far the chain is visible

Studio draws the nodes of the outer graph. The five workers are LangChain
`create_agent` loops compiled separately and invoked inside
`graph._worker_node`, so each appears as **one node**, not as an expanded
`model → tools → model` subgraph — Studio can only expand a subgraph that was
added with `add_node`, and adding them that way would mean the supervisor
routing to a worker's internals.

The worker's internal loop is fully visible in the **trace** for the run, which
is where `observability.traced()` puts the deterministic logic too. Studio for
topology and state, traces for the tool-by-tool chain: between them nothing is
hidden.

## Persistence

`studio.make_graph()` compiles **without** a checkpointer. The server owns
threads, history and interrupt resumption; ours would be ignored, and having
two would make "which one is in force" an unanswerable question. In-process
(`runner.get_graph()`) the app still supplies its own — see
[`memory.py`](../src/supplychain/memory.py).

The dev server writes its state to `.langgraph_api/` (git-ignored), so a
restart keeps the threads you created.

## Deploying a server for real

If the graph should be reachable without a laptop running `langgraph dev`,
deploy it to LangGraph Platform from the same `langgraph.json` — the graph,
dependency and env declarations are already what a Platform build reads. That
is a second deployment alongside Streamlit Cloud, not a replacement for it:
Streamlit stays the operator UI, and the Platform URL is what Studio and the
SDK attach to.
