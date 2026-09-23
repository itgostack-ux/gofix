"""One place to resolve the device a test ticket is booked in against.

Service Request requires device_category, device_brand and device_model on a
new ticket -- a repair cannot be routed to a technician without knowing what
the device is. Several recipes predate that rule and each grew its own copy of
the same workaround: copy the device off "a real repair", i.e.

    SELECT device_item, device_item_name, brand, device_brand, device_model
    FROM `tabService Request`
    WHERE IFNULL(device_model,'') <> '' AND IFNULL(device_brand,'') <> ''

which finds nothing here, because **no Service Request on this site carries
device_brand or device_model** -- not one. So the fields were never set, and
intake refused every ticket those recipes tried to raise.

The device taxonomy lives in the item master, so resolve it there instead:
CH Model gives the model and its Brand, and the model's CH Sub Category gives
the CH Category the gate asks for. Ordered by name so every run picks the same
device.
"""

from __future__ import annotations

import frappe


def resolve_device_triple() -> dict | None:
    """Return {device_category, device_brand, device_model}, or None."""
    rows = frappe.db.sql(
        """
        SELECT m.name AS device_model, m.brand AS device_brand,
               sc.category AS device_category
        FROM `tabCH Model` m
        JOIN `tabBrand` b ON b.name = m.brand
        JOIN `tabCH Sub Category` sc ON sc.name = m.sub_category
        WHERE IFNULL(m.brand, '') <> '' AND IFNULL(sc.category, '') <> ''
        ORDER BY m.name LIMIT 1
        """,
        as_dict=True,
    )
    return rows[0] if rows else None


def apply_intake_mandatories(sr):
    """Set what booking a device in requires, and return the doc.

    Also takes the data-loss acknowledgement, which is a consent the counter
    has to record. Leaves every other field the caller set alone.
    """
    device = resolve_device_triple()
    if device:
        sr.device_category = device["device_category"]
        sr.device_brand = device["device_brand"]
        sr.device_model = device["device_model"]
    sr.data_backup_disclaimer = 1
    return sr
