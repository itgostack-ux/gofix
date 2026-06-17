"""Migrate CH Customer Address city/state/pincode fields from free-text Data to proper
Link fields pointing at CH State, CH City, CH Pincode masters.

Before this patch the three fields were plain Data (free text). After the DocType
change they are Link fields, so stored values must match master document names:

  state   — CH State.name == state_name (autoname by field)  → values typically
             already match ("Karnataka", "Tamil Nadu", etc).
  pincode — CH Pincode.name == pincode digit string            → already match.
  city    — CH City.name == "{state}-{city_name}"             → values stored as
             "Bangalore" must become "Karnataka-Bangalore"; this needs a lookup.

The patch:
  1. Validates state and pincode values against their masters (no-op for already-matching).
  2. Converts city values by looking up CH City where city_name=<current_value> and
     state=<row_state>.  On no match the cell is left blank with a log entry so the
     user can fill it from the dropdown — no data is destroyed.
  3. Populates the new city_name and state_code fields via fetch if they are empty.

Idempotent: safe to re-run; rows whose city already matches a CH City key are skipped.
"""

import frappe
from frappe.utils import cstr


def execute():
    if not frappe.db.table_exists("tabCH Customer Address"):
        return  # DocType not yet installed

    # Build lookup maps from masters
    # CH State: name == state_name (safe to compare directly)
    valid_states = set(frappe.db.sql_list("SELECT name FROM `tabCH State` WHERE disabled=0"))

    # CH Pincode: name == pincode string
    valid_pincodes = set(frappe.db.sql_list("SELECT name FROM `tabCH Pincode`"))

    # CH City: (state, city_name) → CH City name  (format: "{state}-{city_name}")
    city_rows = frappe.db.sql(
        "SELECT name, state, city_name FROM `tabCH City` WHERE disabled=0",
        as_dict=True,
    )
    # Primary: exact name match (for rows already migrated)
    city_names_set = {r.name for r in city_rows}
    # Secondary: (state, city_name_lower) → CH City name
    city_lookup: dict[tuple, str] = {}
    for r in city_rows:
        key = (cstr(r.state).strip().lower(), cstr(r.city_name).strip().lower())
        city_lookup[key] = r.name

    # CH State → state_code map for back-filling
    state_code_map: dict[str, str] = dict(
        frappe.db.sql("SELECT name, state_code FROM `tabCH State`")
    )

    rows = frappe.db.sql(
        """SELECT name, city, state, pincode, city_name, state_code
           FROM `tabCH Customer Address`""",
        as_dict=True,
    )

    for row in rows:
        updates: dict[str, str] = {}

        # ── state ────────────────────────────────────────────────────────────
        raw_state = cstr(row.state).strip()
        if raw_state and raw_state not in valid_states:
            # Attempt case-insensitive match
            match = next(
                (s for s in valid_states if s.lower() == raw_state.lower()), None
            )
            if match:
                updates["state"] = match
            else:
                frappe.log_error(
                    title="CH Customer Address migration: unknown state",
                    message=f"Row {row.name}: state '{raw_state}' not found in CH State master.",
                )

        effective_state = updates.get("state", raw_state)

        # ── city ─────────────────────────────────────────────────────────────
        raw_city = cstr(row.city).strip()
        if raw_city and raw_city not in city_names_set:
            # Needs conversion: look up by (state, city_name)
            lookup_key = (effective_state.lower(), raw_city.lower())
            ch_city_name = city_lookup.get(lookup_key)

            if not ch_city_name:
                # Try without state constraint (in case state was blank)
                city_only = {
                    c_name.lower(): c_name
                    for (_s, c_name_lower), c_name in city_lookup.items()
                    if c_name.lower() == raw_city.lower()
                }
                ch_city_name = next(iter(city_only.values()), None) if len(city_only) == 1 else None

            if ch_city_name:
                updates["city"] = ch_city_name
            else:
                # Cannot map — blank it so it's a valid empty Link, not a broken one
                updates["city"] = ""
                frappe.log_error(
                    title="CH Customer Address migration: city not mapped",
                    message=(
                        f"Row {row.name}: city '{raw_city}' (state='{effective_state}') "
                        f"has no matching CH City entry. Field cleared; please re-select."
                    ),
                )

        # ── pincode ──────────────────────────────────────────────────────────
        raw_pincode = cstr(row.pincode).strip()
        if raw_pincode and raw_pincode not in valid_pincodes:
            # Pincode doesn't exist in master; clear so Link is not broken
            updates["pincode"] = ""
            frappe.log_error(
                title="CH Customer Address migration: unknown pincode",
                message=f"Row {row.name}: pincode '{raw_pincode}' not in CH Pincode master. Field cleared.",
            )

        # ── back-fill city_name and state_code if missing ────────────────────
        effective_city = updates.get("city", raw_city)
        if not cstr(row.city_name).strip() and effective_city in city_names_set:
            city_row = next((r for r in city_rows if r.name == effective_city), None)
            if city_row:
                updates["city_name"] = city_row.city_name

        if not cstr(row.state_code).strip() and effective_state in state_code_map:
            sc = state_code_map.get(effective_state)
            if sc:
                updates["state_code"] = sc

        if updates:
            frappe.db.set_value(
                "CH Customer Address", row.name, updates, update_modified=False
            )

    frappe.db.commit()
    print(f"CH Customer Address migration complete. {len(rows)} rows processed.")
