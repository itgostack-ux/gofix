# Copyright (c) 2026, GoStack and contributors

"""Where to get a GoFix spare from, in the order a repair chain would look.

A technician who needs a part does not care which warehouse it lives in — they
care how fast it reaches the bench. That gives a natural search order, and it is
the same one every multi-site service operation uses:

  1. **The bench itself.** Stock at the repair location is fitted today.
  2. **The zone hub.** The regional forward-stocking point. Same-day or next-day.
  3. **The main hub.** The company's central spare pool. Next-day.
  4. **Any other store holding one.** Slower and it strips a sibling branch, but
     an idle part on a shelf beats buying a second one and beats a customer
     waiting on a supplier.
  5. **Procurement.** Only when the part genuinely does not exist in the network.

Only free stock counts at every tier. A part already promised to another ticket
is not available, however healthy the Bin looks — see :func:`available_qty`.

Hub tiers are configuration, not code: a hub is a ``CH Store`` with ``is_hub``
set, and the central pool is ``Company.master_hub_warehouse`` (falling back to
``GoFix Settings.default_parts_warehouse``). When none of that is configured the
tiers are simply empty and the search flattens to "anywhere in the company",
which is still correct — just without the routing preference. Tier labels are
reported so the caller can say WHERE a part is coming from rather than only that
it was found.
"""

import frappe
from frappe import _
from frappe.utils import flt

# Search tiers, best first. The order is the whole point of this module.
TIER_BENCH = "bench"
TIER_ZONE_HUB = "zone_hub"
TIER_MAIN_HUB = "main_hub"
TIER_ZONE_STORE = "zone_store"
TIER_CITY_STORE = "city_store"
TIER_OTHER_STORE = "other_store"

TIER_LABELS = {
	TIER_BENCH: _("Repair location"),
	TIER_ZONE_HUB: _("Zone hub"),
	TIER_MAIN_HUB: _("Main hub"),
	TIER_ZONE_STORE: _("Store in the same zone"),
	TIER_CITY_STORE: _("Store in the same city"),
	TIER_OTHER_STORE: _("Other store"),
}

_TIER_RANK = {
	TIER_BENCH: 0,
	TIER_ZONE_HUB: 1,
	TIER_MAIN_HUB: 2,
	TIER_ZONE_STORE: 3,
	TIER_CITY_STORE: 4,
	TIER_OTHER_STORE: 5,
}


def available_qty(item_code: str, warehouse: str) -> float:
	"""Free quantity of `item_code` at `warehouse`.

	``Bin.actual_qty`` minus everything already promised to a ticket. Standard
	Stock Reservation Entries do not accept a Service Request as a voucher, so
	the SR spare line IS the commitment record while the Bin stays the quantity
	authority. A part reserved or issued against another ticket is somebody
	else's part and must never be offered here.
	"""
	return flt(_bin_qty(item_code, warehouse)) - flt(reserved_qty(item_code, warehouse))


def _bin_qty(item_code: str, warehouse: str) -> float:
	return flt(
		frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": warehouse}, "actual_qty")
	)


def reserved_qty(item_code: str, warehouse: str, exclude_sr: str | None = None) -> float:
	"""Quantity of `item_code` at `warehouse` already committed to tickets.

	A ticket's effective warehouse follows the device: once it is transferred to
	a service centre it reserves that hub's stock, not its origin store's.
	"""
	return flt(frappe.db.sql(
		"""
		SELECT COALESCE(SUM(sl.qty), 0)
		FROM `tabSR Spare Line` sl
		JOIN `tabService Request` sr ON sr.name = sl.parent
		WHERE sl.spare_item = %(item)s
		  AND sl.status IN ('Reserved', 'Issued')
		  AND sl.parenttype = 'Service Request'
		  AND (%(skip)s = '' OR sr.name != %(skip)s)
		  AND (
			CASE
				WHEN sr.transfer_status IN ('In Transit', 'Received at Service Center')
					AND COALESCE(sr.transferred_to_store, '') != ''
				THEN sr.transferred_to_store
				ELSE COALESCE(NULLIF(sr.current_location, ''), sr.source_warehouse)
			END
		  ) = %(wh)s
		""",
		{"item": item_code, "wh": warehouse, "skip": exclude_sr or ""},
	)[0][0])


def get_spare_availability(item_code: str, warehouse: str) -> dict:
	"""Bin / reserved / free breakdown for one item at one warehouse."""
	actual = _bin_qty(item_code, warehouse)
	reserved = reserved_qty(item_code, warehouse)
	return {
		"actual_qty": actual,
		"reserved_qty": reserved,
		"available_qty": actual - reserved,
	}


# ── locating the tiers ───────────────────────────────────────────────────────

def _store_for_warehouse(warehouse: str) -> dict | None:
	"""The CH Store a warehouse belongs to, matching bins to their store group.

	A store owns several bins (Sellable, Damaged, Demo…), so the match is made
	against both the store's own warehouse and its warehouse group, then by
	walking up the warehouse tree for anything that names neither.
	"""
	if not warehouse:
		return None

	row = frappe.db.get_value(
		"CH Store", {"warehouse": warehouse},
		["name", "zone", "city", "company", "is_hub"], as_dict=True,
	)
	if row:
		return row

	seen, node = set(), warehouse
	while node and node not in seen:
		seen.add(node)
		row = frappe.db.get_value(
			"CH Store", {"warehouse_group": node},
			["name", "zone", "city", "company", "is_hub"], as_dict=True,
		) or frappe.db.get_value(
			"CH Store", {"warehouse": node},
			["name", "zone", "city", "company", "is_hub"], as_dict=True,
		)
		if row:
			return row
		node = frappe.db.get_value("Warehouse", node, "parent_warehouse")
	return None


def _hub_warehouses(filters: dict) -> list:
	"""Warehouses of the hub stores matching `filters`, skipping disabled ones."""
	rows = frappe.get_all(
		"CH Store",
		filters={**filters, "is_hub": 1, "disabled": 0},
		fields=["warehouse"],
	)
	return [r.warehouse for r in rows if r.warehouse]


def resolve_zone_hub(warehouse: str) -> str | None:
	"""The hub serving the zone this warehouse sits in, if one is configured."""
	store = _store_for_warehouse(warehouse)
	if not store or not store.get("zone"):
		return None
	for hub in _hub_warehouses({"zone": store.zone}):
		if hub != warehouse:
			return hub
	return None


def resolve_main_hub(company: str, warehouse: str | None = None) -> str | None:
	"""The company's central spare pool.

	``Company.master_hub_warehouse`` is the intended setting; GoFix's own
	``default_parts_warehouse`` is honoured as a fallback so a site that only
	configured the app still gets a central tier. Last resort is a hub store in
	the same city.
	"""
	if company:
		master = frappe.db.get_value("Company", company, "master_hub_warehouse")
		if master:
			return master

	parts_wh = frappe.db.get_single_value("GoFix Settings", "default_parts_warehouse")
	if parts_wh:
		return parts_wh

	store = _store_for_warehouse(warehouse) if warehouse else None
	if store and store.get("city"):
		for hub in _hub_warehouses({"city": store.city}):
			if hub != warehouse:
				return hub
	return None


def _tier_for(warehouse: str, bench: str, zone_hub: str | None, main_hub: str | None,
              bench_store: dict | None) -> str:
	if warehouse == bench:
		return TIER_BENCH
	if zone_hub and warehouse == zone_hub:
		return TIER_ZONE_HUB
	if main_hub and warehouse == main_hub:
		return TIER_MAIN_HUB

	store = _store_for_warehouse(warehouse)
	if store and bench_store:
		if store.get("zone") and store.get("zone") == bench_store.get("zone"):
			return TIER_ZONE_STORE
		if store.get("city") and store.get("city") == bench_store.get("city"):
			return TIER_CITY_STORE
	return TIER_OTHER_STORE


def find_spare_sources(item_code: str, bench_warehouse: str, company: str | None = None,
                       qty: float = 0, exclude_sr: str | None = None) -> list:
	"""Every warehouse holding free stock of `item_code`, best source first.

	Driven off Bin, so only warehouses that actually carry the part are
	considered — the search cost does not grow with the number of stores that
	have never stocked it. Rows with no free quantity are dropped: a Bin full of
	parts promised to other tickets is not a source.

	Returns rows of ``{warehouse, tier, tier_label, available_qty, actual_qty,
	reserved_qty, sufficient}``, ordered by tier and then by free quantity, so
	the first row that is ``sufficient`` is the one to take.
	"""
	if not item_code:
		return []

	company = company or (
		frappe.db.get_value("Warehouse", bench_warehouse, "company") if bench_warehouse else None
	)

	conditions = ["b.item_code = %(item)s", "b.actual_qty > 0", "IFNULL(w.disabled, 0) = 0",
	              "IFNULL(w.is_group, 0) = 0"]
	params = {"item": item_code}
	if company:
		conditions.append("w.company = %(company)s")
		params["company"] = company

	rows = frappe.db.sql(
		f"""
		SELECT b.warehouse, b.actual_qty
		FROM `tabBin` b
		JOIN `tabWarehouse` w ON w.name = b.warehouse
		WHERE {' AND '.join(conditions)}
		""",
		params, as_dict=True,
	)

	zone_hub = resolve_zone_hub(bench_warehouse) if bench_warehouse else None
	main_hub = resolve_main_hub(company, bench_warehouse)
	bench_store = _store_for_warehouse(bench_warehouse) if bench_warehouse else None

	sources = []
	for row in rows:
		reserved = reserved_qty(item_code, row.warehouse, exclude_sr=exclude_sr)
		free = flt(row.actual_qty) - reserved
		if free <= 0:
			continue
		tier = _tier_for(row.warehouse, bench_warehouse, zone_hub, main_hub, bench_store)
		sources.append({
			"warehouse": row.warehouse,
			"tier": tier,
			"tier_label": TIER_LABELS.get(tier, tier),
			"actual_qty": flt(row.actual_qty),
			"reserved_qty": reserved,
			"available_qty": free,
			"sufficient": free >= flt(qty) if qty else True,
		})

	# Best tier first; within a tier take the fullest shelf, so one transfer
	# covers the need instead of picking several stores clean.
	sources.sort(key=lambda s: (_TIER_RANK.get(s["tier"], 99), -s["available_qty"]))
	return sources


def best_source(item_code: str, bench_warehouse: str, qty: float,
                company: str | None = None, exclude_sr: str | None = None) -> dict | None:
	"""The nearest warehouse that can cover `qty` on its own, or None."""
	for source in find_spare_sources(item_code, bench_warehouse, company, qty, exclude_sr):
		if source["sufficient"]:
			return source
	return None


def is_gofix_spare(item_code: str) -> bool:
	"""True when the item is a catalogued repair spare.

	Sourcing and the auto-approved requisition are scoped to these. Anything
	else is ordinary stock and keeps the standard purchase approval gate.
	"""
	return bool(
		item_code
		and frappe.db.exists("Solution Spare Mapping", {"spare_item": item_code})
	)
