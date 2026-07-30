"""The network snapshot behind the hero KPI strip.

The landing screen used to be an empty chat box, which tells a demo audience
nothing about the dataset they are about to ask questions of. This counts the
same fixtures the agents read, so the header states the situation before anyone
types.

Deliberately read-only and deterministic: it goes through `data.access` like
every other reader, adds no tool and no model call, and so costs nothing and
works with no API key. Approved write actions are layered in by `access`, so an
approved incident shows up here on the next rerun.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..data import access
from ..tools.shipment import DELAY_STATUSES

# DELAY_STATUSES is imported rather than restated on purpose. The header would
# otherwise be free to disagree with `identify_delayed_shipments`, and a KPI
# that says 18 above an agent answer that says 21 destroys trust in both.
IN_TRANSIT_STATUSES = {"in_transit", "out_for_delivery", "at_customs"}
CLOSED_INCIDENT_STATUSES = {"resolved", "closed", "cancelled"}


@dataclass(frozen=True)
class Snapshot:
    shipments: int = 0
    in_transit: int = 0
    delayed: int = 0
    damaged: int = 0
    open_incidents: int = 0
    critical_incidents: int = 0
    value_at_risk: float = 0.0
    ok: bool = True

    @property
    def disrupted(self) -> bool:
        """Whether the hero lane should show a blocked leg."""
        return bool(self.delayed or self.damaged or self.open_incidents)


def fleet_snapshot() -> Snapshot:
    """Count the network. Never raises - a broken fixture just empties the strip."""
    try:
        shipments = access.load("shipments")
        incidents = access.load("incidents")
    except Exception:  # noqa: BLE001 - the sidebar already reports data errors
        return Snapshot(ok=False)

    delayed = [
        s
        for s in shipments
        if s.get("status") in DELAY_STATUSES or (s.get("delay_hours") or 0) > 0
    ]
    damaged = [s for s in shipments if s.get("status") == "damaged"]
    open_incidents = [
        i for i in incidents if i.get("status") not in CLOSED_INCIDENT_STATUSES
    ]

    at_risk = {s["shipment_id"]: s for s in (*delayed, *damaged)}

    return Snapshot(
        shipments=len(shipments),
        in_transit=sum(1 for s in shipments if s.get("status") in IN_TRANSIT_STATUSES),
        delayed=len(delayed),
        damaged=len(damaged),
        open_incidents=len(open_incidents),
        critical_incidents=sum(
            1 for i in open_incidents if i.get("severity") == "critical"
        ),
        value_at_risk=round(sum(s.get("value_usd") or 0 for s in at_risk.values()), 2),
    )
