# Presentation section — Team Member 3

Inventory & Supplier Agent Engineer

## Role

Own stock visibility and sourcing options for NovaRetail’s ops assistant:
two ReAct workers (`inventory`, `supplier`), twelve deterministic tools, mock
JSON fixtures, prompts, and tool tests.

## Agents

| Agent | Job | Tools (6 each) |
|---|---|---|
| **Inventory** | Stock positions, shortages, required qty, inter-warehouse transfers | `check_inventory`, `check_warehouse_stock`, `check_warehouse_availability`, `identify_inventory_shortages`, `calculate_required_quantity`, `find_inventory_transfer` |
| **Supplier** | Profiles, availability, alternatives, comparison, landed cost | `search_supplier`, `get_supplier_details`, `check_supplier_availability`, `find_alternative_supplier`, `compare_supplier_options`, `estimate_procurement_cost` |

Workers never see the raw chat. They receive an intake briefing + other
agents’ findings (`agents/base.build_context_block`). Final prose is written
by the Responder (TM4).

## Invariants (demo talking points)

1. **`available = on_hand − reserved`** — answers always lead with available,
   never on_hand alone.
2. **Transfers never breach donor safety stock** — `find_inventory_transfer`
   only offers spare above safety stock; tests lock this in.
3. **Suspended suppliers are never available** — status gates
   `check_supplier_availability` / alternatives ranking.
4. **Writes are not ours** — Inventory/Supplier are read-only; Recovery + HITL
   own incidents / reroutes / escalations.

## Prompt design (what we changed)

- **Inventory:** decision tree (tool order), mandatory vocabulary, fixed
  finding skeleton (`remaining_gap` / `fully_covered`).
- **Supplier:** decision tree (details → alternatives → compare → cost),
  upstream inventory quantity (`remaining_gap` → buy size),
  `missing quantity — cannot cost`, boundaries (`exclude_supplier_id`, no
  transfers / no incidents), fixed finding template with alternatives table.

Documented as the brief’s “one prompt improved from traces” in
`docs/langsmith-report.md` §5 (`SUPPLIER_PROMPT`, with Inventory companion).

## Cross-agent handoff (TM3 slice of TM4)

Inventory → supplier → recovery uses a fixed findings contract (see
`docs/handoff-inventory-supplier-recovery.md`):

1. Inventory always emits `remaining_gap` / `fully_covered` / `Next`.
2. Supplier uses `remaining_gap` as buy qty; otherwise
   `missing quantity — cannot cost`.
3. Recovery prefers transfers when fully covered; otherwise the supplier
   `Recommended` id + landed cost.
4. Tool failures are JSON `error`s with hints — agents retry once, never crash.

## Demo scripts (live UI)

Use **New conversation** between demos. Open **Workflow trace** under the answer.

1. **Shortage + transfer**  
   `We're short on SKU-1001 at WH-N04. Can we cover it from another warehouse?`  
   Expect: inventory tools in order; Position / Need / Transfers;
   `remaining_gap` / `fully_covered`.

2. **Warehouse overview**  
   `How is stock looking at WH-N02 - anything below reorder point?`  
   Expect: `check_warehouse_availability` (and/or shortages); no false PO.

3. **Supplier failure + compare**  
   `SUP-005 has missed two delivery windows. Find alternatives for SKU-3001 for 1200 units and compare them on cost and lead time.`  
   Expect: exclude SUP-005; details → alternatives → compare → cost; landed $.

4. **Missing quantity**  
   `Find alternative suppliers for SKU-3001 excluding SUP-005. Quantity is unknown — do not cost a PO.`  
   Expect: `missing quantity — cannot cost`; no invented buy size.

5. **Suspended supplier**  
   `Can SUP-010 supply 1000 units of SKU-5001?`  
   Expect: not available / suspended.

## Files owned

| Path | What |
|---|---|
| `src/supplychain/tools/inventory.py` | Inventory tools |
| `src/supplychain/tools/supplier.py` | Supplier tools |
| `src/supplychain/prompts.py` | `INVENTORY_PROMPT`, `SUPPLIER_PROMPT` only |
| `scripts/generate_mock_data.py` | products / suppliers / inventory builders |
| `tests/test_tools.py` | Inventory & supplier sections |

## Shared with the team

LangSmith tracing, eval cases `inventory_shortage` /
`warehouse_availability` / `supplier_failure`, architecture review, deployment
pairing — not sole ownership.
