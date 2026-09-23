# Copyright (c) 2026, GoStack and contributors
"""Give Service Request room to grow again, without moving a single field.

``tabService Request`` sits at 63,460 bytes of a hard 65,535 MySQL row limit,
so the next Data, Link or Select field added to it fails the migrate. All of
that 63,460 is 114 varchar columns: Frappe maps Data/Link/Select to
``varchar(140)`` and utf8mb4 charges 4 bytes a character, so each field costs
140 * 4 + 2 = 562 bytes. Every other column type on the doctype -- int, date,
decimal, text, longtext -- contributes essentially nothing, because the table
is already ``ROW_FORMAT=Dynamic`` and text/blob store a pointer off-page.

Frappe's own answer is the ``length`` property, and the floor of 64 exists for
precisely this error -- ``frappe/database/schema.py`` cites the MariaDB
troubleshooting page next to it:

    if length:
        if coltype == "varchar":
            if cint(length) < 64: length = 64

``varchar(64)`` costs 258 bytes, so each shrunk column gives back 304.

Selects are the safest columns to do this to: the stored value is always one
of the field's own options, which are developer-controlled rather than user
input. The longest option string across all 26 Selects on this doctype is 31
characters ("Not Applicable - No Data Access") and the widest value actually
stored is 16, so 64 leaves double the headroom of the worst case that can
exist today.

Fourteen of the Selects are declared in the doctype JSON and are handled
there. The other twelve are Custom Fields, and a JSON edit does not touch
those -- this patch is why the saving is not silently halved.
"""

from __future__ import annotations

import frappe

DOCTYPE = "Service Request"
TARGET_LENGTH = 64

#: The Custom Field Selects. The JSON-declared ones carry `length` in
#: service_request.json; these have no JSON to edit.
FIELDS = (
    "repair_location_type",
    "repairability_status",
    "repair_pause_reason",
    "close_outcome",
    "loaner_status",
    "data_wipe_method",
    "imei_validation_status",
    "activation_lock_status",
    "appointment_source",
    "qc_status",
    "cost_bearer",
    "repair_outcome",
)


def execute():
    if not frappe.db.exists("DocType", DOCTYPE):
        return

    changed = []
    for fieldname in FIELDS:
        name = frappe.db.get_value(
            "Custom Field", {"dt": DOCTYPE, "fieldname": fieldname}, "name")
        if not name:
            # A site that never installed this field is not a problem.
            continue

        row = frappe.db.get_value(
            "Custom Field", name, ["fieldtype", "length"], as_dict=True) or {}
        if row.get("fieldtype") != "Select":
            # Someone re-typed the field since this patch was written. Widening
            # is what this patch is for; re-typing is not, so leave it alone.
            continue
        if (row.get("length") or 0) == TARGET_LENGTH:
            continue

        # Refuse to truncate. Strict mode would raise on save rather than
        # corrupt, but a migrate that breaks a column nobody was watching is
        # still a bad afternoon -- so check the data first.
        if frappe.db.has_column(DOCTYPE, fieldname):
            widest = frappe.db.sql(
                f"SELECT MAX(CHAR_LENGTH(`{fieldname}`)) FROM `tab{DOCTYPE}`")[0][0] or 0
            if widest > TARGET_LENGTH:
                frappe.log_error(
                    title="v10: Select too wide to shrink",
                    message=f"{DOCTYPE}.{fieldname} holds a {widest}-char value; "
                            f"left at its current width.")
                continue

        doc = frappe.get_doc("Custom Field", name)
        doc.length = TARGET_LENGTH
        doc.flags.ignore_permissions = True
        doc.save()  # saving a Custom Field is what triggers the ALTER
        changed.append(fieldname)

    if changed:
        frappe.db.commit()
    print(f"v10: shrank {len(changed)} Custom Field Select column(s) on {DOCTYPE}")
