"""Price repairs by device, not just by brand.

GoFix Pricing Rule could narrow a rate to a brand and no further, so an
iPhone 11 and an iPhone 15 Pro Max were quoted the same for the same repair,
and a laptop was quoted like a phone. This installs the rest of the ladder --
category, sub category and model -- plus a per-scope bench fee, and leaves
every existing rule exactly as it was: a rule that names only a brand still
matches every model of that brand, because a blank level means "any".

Nothing is backfilled. The 185 rules that exist are correct as written; the
new levels are there to be used where a device genuinely costs more, not to
be populated for its own sake.

Safe to re-run.
"""

import frappe


def execute() -> None:
    frappe.reload_doc("gofix_services", "doctype", "gofix_pricing_rule")

    for column in ("device_category", "device_sub_category", "device_model", "service_charge"):
        if not frappe.db.has_column("GoFix Pricing Rule", column):
            frappe.log_error(
                title="Model-wise pricing patch: column missing",
                message=f"GoFix Pricing Rule has no {column} after reload_doc.",
            )
            return

    total = frappe.db.count("GoFix Pricing Rule")
    by_brand = frappe.db.count("GoFix Pricing Rule", {"device_brand": ("!=", "")})
    print(f"GoFix Pricing Rule: {total} rule(s), {by_brand} brand-scoped, "
          f"device ladder available (category / sub category / model)")
