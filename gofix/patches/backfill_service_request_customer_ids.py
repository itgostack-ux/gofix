"""Backfill ch_customer_id and ch_membership_id on existing Service Request rows."""

import frappe


def execute():
    try:
        frappe.db.sql("SELECT ch_customer_id FROM `tabService Request` LIMIT 1")
    except Exception:
        return  # columns not yet installed

    cust_rows = frappe.db.sql(
        """
        SELECT name, ch_customer_id, ch_membership_id
        FROM `tabCustomer`
        WHERE ch_customer_id IS NOT NULL AND ch_customer_id != 0
        """,
        as_dict=True,
    )
    if not cust_rows:
        return

    cust_map = {r.name: r for r in cust_rows}

    rows = frappe.db.sql(
        """
        SELECT name, customer
        FROM `tabService Request`
        WHERE customer IS NOT NULL AND customer != ''
          AND (ch_customer_id IS NULL OR ch_customer_id = 0)
        """,
        as_dict=True,
    )

    for row in rows:
        cust = cust_map.get(row.customer)
        if not cust:
            continue
        frappe.db.sql(
            """
            UPDATE `tabService Request`
            SET ch_customer_id = %s, ch_membership_id = %s
            WHERE name = %s
            """,
            (cust.ch_customer_id, cust.ch_membership_id, row.name),
        )

    frappe.db.commit()
