"""Migrate existing plaintext device unlock secrets into the encrypted store.

The Service Request ``password`` (device PIN/password) and ``pattern`` fields
were changed from ``Data`` to ``Password``. Password-type values live encrypted
in the ``__Auth`` table, but pre-existing rows still hold cleartext in the main
table column. This patch moves each cleartext value into ``__Auth`` via
``set_encrypted_password`` and overwrites the column with a masked placeholder
so no unlock secret remains in cleartext at rest.

Idempotent: rows already holding an all-asterisk placeholder are skipped.
"""

import frappe
from frappe.utils.password import set_encrypted_password


def execute():
    # Password fields keep a column on the doctype table; bail if it's absent
    # (e.g. the field was never installed in this environment).
    try:
        rows = frappe.db.sql(
            """
            SELECT name, `password`, `pattern`
            FROM `tabService Request`
            WHERE (`password` IS NOT NULL AND `password` != '')
               OR (`pattern`  IS NOT NULL AND `pattern`  != '')
            """,
            as_dict=True,
        )
    except Exception:
        return

    migrated = 0
    for r in rows:
        for field in ("password", "pattern"):
            val = r.get(field) or ""
            # Skip empties and already-migrated masked placeholders.
            if not val or set(val) == {"*"}:
                continue
            set_encrypted_password("Service Request", r.name, val, field)
            frappe.db.sql(
                f"UPDATE `tabService Request` SET `{field}` = %s WHERE name = %s",
                ("*" * len(val), r.name),
            )
            migrated += 1

    if migrated:
        frappe.db.commit()
        frappe.logger().info(
            f"[encrypt_service_request_unlock_secrets] migrated {migrated} "
            f"unlock secret(s) to the encrypted store"
        )
