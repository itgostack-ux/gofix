"""Company, zone, state, city and store — the same five, on every GoFix report.

Every report on this bench is read by someone whose view of the business is a
slice of it: a store manager, a city lead, a zonal head. Until now the GoFix
reports each chose their own subset — most offered Company alone, one offered
Zone, two offered Store — so the same question asked from two reports gave
answers cut in different shapes, and a zonal manager had no way to ask a zonal
question at all.

The five axes come off one master. ``CH Store`` carries ``company``,
``warehouse``, ``zone``, ``state`` and ``city`` together, so choosing any of
them resolves to a set of warehouses, and a report filters on the warehouse
column it already has. That keeps the geography in the master rather than
duplicated onto every repair row, and it means a store that moves zone is
correct in every report the next day.

Two things are deliberately separate:

* **What the user asked for** — the filters they set on screen.
* **What the user may see** — their own scope, from ``ch_erp15.report_scope``.

They are AND-ed. Widening a filter can never widen entitlement: a user who asks
for a zone they cannot see gets nothing, not everything. See the standing rule
in the bench notes about scope never breaking.
"""

import frappe

# The five, in the order they should appear on every report.
GEO_FILTER_FIELDS = ("company", "zone", "state", "city", "store")


def resolve_warehouses(filters) -> set | None:
    """Warehouses matching the geography the user picked.

    ``None`` means "they picked no geography", which is not the same as "no
    warehouses match" — an empty set. Collapsing the two is how a filter that
    matches nothing turns into a report showing everything.
    """
    filters = filters or {}
    picked = {f: filters.get(f) for f in ("zone", "state", "city", "store")
              if filters.get(f)}
    if not picked:
        return None

    store_filters = {}
    if picked.get("zone"):
        store_filters["zone"] = picked["zone"]
    if picked.get("state"):
        store_filters["state"] = picked["state"]
    if picked.get("city"):
        store_filters["city"] = picked["city"]
    if picked.get("store"):
        store_filters["name"] = picked["store"]
    if filters.get("company"):
        store_filters["company"] = filters["company"]

    rows = frappe.get_all("CH Store", filters=store_filters,
                          fields=["warehouse"], limit_page_length=0)
    return {r.warehouse for r in rows if r.warehouse}


def geo_conditions(filters, *, company_field=None, warehouse_field=None,
                   user=None) -> str:
    """A WHERE fragment for what was asked AND what the asker may see.

    Returns "" when nothing narrows the query. Every fragment is parameter-free
    and quoted through ``frappe.db.escape``, because these values arrive from a
    filter dialog.
    """
    filters = filters or {}
    clauses = []

    if company_field and filters.get("company"):
        clauses.append("%s = %s" % (company_field,
                                    frappe.db.escape(filters["company"])))

    if warehouse_field:
        chosen = resolve_warehouses(filters)
        if chosen is not None:
            if not chosen:
                # They asked for a slice that contains no store. Say so with a
                # clause that matches nothing rather than dropping the filter.
                clauses.append("1 = 0")
            else:
                clauses.append("%s IN (%s)" % (
                    warehouse_field,
                    ", ".join(frappe.db.escape(w) for w in sorted(chosen))))

    # And what this user is entitled to, whatever they asked for.
    try:
        from ch_erp15.ch_erp15.report_scope import scope_where_clause

        scoped = scope_where_clause(
            user,
            company_field=company_field,
            warehouse_field=warehouse_field,
        )
        if scoped:
            clauses.append("(%s)" % scoped)
    except ImportError:
        # Without the scope helper there is no entitlement layer to apply; the
        # report still honours the filters the user set.
        pass

    return (" AND " + " AND ".join(clauses)) if clauses else ""


@frappe.whitelist()
def geo_filter_options(company=None) -> dict:
    """Zones, states and cities that actually have a store, for the pickers.

    Offering the full City master (815 rows) when 61 stores exist between them
    invites a filter that returns nothing and looks broken.
    """
    store_filters = {"company": company} if company else {}
    rows = frappe.get_all("CH Store", filters=store_filters,
                          fields=["name", "zone", "state", "city"],
                          limit_page_length=0)
    return {
        "zones": sorted({r.zone for r in rows if r.zone}),
        "states": sorted({r.state for r in rows if r.state}),
        "cities": sorted({r.city for r in rows if r.city}),
        "stores": sorted({r.name for r in rows if r.name}),
    }


# ── The two documents a repair produces ──────────────────────────────────────

JOB_SHEET_FORMAT = "GoFix Job Sheet"
SERVICE_INVOICE_FORMAT = "GoFix Service Invoice"


@frappe.whitelist()
def printable_documents(service_request) -> dict:
    """What can be printed for this repair, and what cannot yet.

    Two documents, and each has a moment. The job sheet exists from the instant
    the device is taken in. The invoice exists only once the repair has actually
    been billed -- not when the work is finished, not when quality control
    passes. Printing an "invoice" for an unbilled repair hands the customer a
    document with no bill behind it, and there is no way to tell afterwards
    whether they were charged.

    Returned by the server so the POS, the Ops Hub and the Job Tracker offer the
    same two buttons under the same conditions.
    """
    sr = frappe.get_doc("Service Request", service_request)
    # Whoever cannot read the repair cannot learn from us that it was billed,
    # nor the invoice number if it was.
    sr.check_permission("read")

    names = [r.invoice for r in (sr.get("service_invoices") or []) if r.invoice]
    if sr.get("service_invoice") and sr.service_invoice not in names:
        names.append(sr.service_invoice)

    invoice = None
    if names:
        invoice = frappe.db.get_value(
            "Sales Invoice",
            {"name": ("in", names), "docstatus": 1},
            "name", order_by="posting_date desc, creation desc")

    return {
        "job_sheet": {
            "available": True,
            "doctype": "Service Request",
            "name": sr.name,
            "format": JOB_SHEET_FORMAT,
            "label": frappe._("Job Sheet"),
        },
        "invoice": {
            "available": bool(invoice),
            "doctype": "Sales Invoice",
            "name": invoice or "",
            "format": SERVICE_INVOICE_FORMAT,
            "label": frappe._("Invoice"),
            "reason": "" if invoice else frappe._(
                "Not billed yet — the invoice exists once the repair is billed, "
                "not when the work or the quality check finishes."),
        },
    }
