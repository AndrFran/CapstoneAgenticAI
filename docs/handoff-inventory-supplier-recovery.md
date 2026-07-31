# Handoff: inventory → supplier → recovery

Cross-agent contract owned by TM3 (with thin supervisor/recovery prompt
hooks). Tool failures stay data (`{"error": ...}`); findings use stable field
names so routing and recovery do not guess.

## Sequence

```text
shortage / cover question
  → inventory
      remaining_gap / fully_covered / Next
  → supplier   (only if remaining_gap > 0 or user asked for sourcing)
      Requirement qty = remaining_gap (or intake quantities)
      Recommended + landed  |  missing quantity — cannot cost
  → recovery
      prefer transfer if fully_covered
      else alternative_supplier from Recommended + qty
  → respond  (+ HITL if propose_*)
```

## Finding fields (parse these)

| Agent | Field | Meaning for the next hop |
|---|---|---|
| Inventory | `remaining_gap` | Units procurement must buy if > 0 |
| Inventory | `fully_covered` | Skip supplier when true (unless user asked sourcing) |
| Inventory | `Next: procurement must source N` | Supervisor task hint for supplier |
| Inventory | `Next: transfers sufficient` | Supervisor may go recovery/respond |
| Supplier | `Requirement: SKU × N` | Qty used for cost/compare |
| Supplier | `missing quantity — cannot cost` | Do not invent MOQ; route inventory if shortage |
| Supplier | `Recommended` + landed | Recovery's leading buy candidate |
| Supplier | `Handoff: recovery may use…` | Explicit pass-through line |

## Error handling

1. Tools return `error` (+ `hint` / sample ids) — never raise into the agent loop.
2. Workers: one correction retry from the hint, then stop cleanly.
3. Supervisor: if supplier says cannot cost and inventory has not run on a
   shortage, route inventory before asking the operator.
4. Recovery: retry once on tool errors; do not invent PO size when supplier
   cannot cost.

## Tests that lock the contract

- Inventory unknown SKU / warehouse → `error` (no exception)
- `find_inventory_transfer` exposes `remaining_gap` / `fully_covered`
- Supplier unknown id / no search hit / bad qty / missing price → `error`

```bash
pytest tests/test_tools.py -k "inventory or supplier"
```

## Related prompts

- `INVENTORY_PROMPT` — error + handoff sections  
- `SUPPLIER_PROMPT` — upstream qty, error + handoff  
- `SUPERVISOR_PROMPT` — inventory before supplier when gap remains  
- `RECOVERY_PROMPT` — consume remaining_gap / Recommended  

Presentation notes: `docs/presentation-tm3.md`.
