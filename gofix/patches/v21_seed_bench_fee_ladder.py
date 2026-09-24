"""Bench fees that differ by what the device is.

v20 gave pricing a device ladder and shipped no rates down it, so every
device still quoted the same flat Rs 200 from the GOFIX-SERVICE-CHARGE item
price -- an iPhone, a MacBook and a keypad phone all charged the same to log,
test, clean and hand back.

The fee reflects how much handling the device actually takes. Apple hardware
is the clear step up at every class: pentalobe and tri-point tooling, adhesive
that has to be cut and re-laid, and parts that are serial-paired to the board,
so the bench spends materially longer on it before any repair begins. A laptop
is a teardown; a keypad phone is four screws.

    Feature Phones                    100
    Smart Phones                      200   (unchanged -- today's flat rate)
      └── iOS Phones                  400
    Tablets                           350
      └── iOS Tablets                 500
    Laptops                           500
      └── iOS Laptops                 800

Written as one rule per scope, never per model. A blank level means "any", so
1,398 Android phones need no rows at all -- they inherit Smart Phones -- and a
model that genuinely costs more is a single extra rule on top.

These carry a service_charge and no labour rate, which is what makes them
bench-fee rules: get_pricing_rule skips any rule without a labour rate, so
they can never decide what a repair costs.

Idempotent: matched on scope, so re-running updates the fee rather than
adding a second rule for the same scope. Rates are edited in the doctype
afterwards, not here.
"""

import frappe

RULE_PREFIX = "Bench Fee"

# (category, sub_category, fee)
LADDER = [
    ("Feature Phones", None, 100),
    ("Smart Phones", None, 200),
    ("Smart Phones", "Smart Phones-iOS Phones", 400),
    ("Tablets", None, 350),
    ("Tablets", "Tablets-iOS Tablets", 500),
    ("Laptops", None, 500),
    ("Laptops", "Laptops-iOS Laptops", 800),
]


def execute() -> None:
    if not frappe.db.has_column("GoFix Pricing Rule", "service_charge"):
        frappe.log_error(
            title="Bench fee ladder: service_charge column missing",
            message="Run gofix.patches.v20_model_wise_pricing first.",
        )
        return

    created = updated = skipped = 0
    for category, sub_category, fee in LADDER:
        # Only seed scopes this bench actually has. A rule pointing at a
        # category nobody stocks is a broken link waiting to confuse someone.
        if not frappe.db.exists("CH Category", category):
            skipped += 1
            continue
        if sub_category and not frappe.db.exists("CH Sub Category", sub_category):
            skipped += 1
            continue

        existing = frappe.db.get_value(
            "GoFix Pricing Rule",
            {
                "device_category": category,
                "device_sub_category": sub_category or "",
                "repair_solution": "",
                "issue_category": "",
            },
            "name",
        )
        if existing:
            if not frappe.db.get_value("GoFix Pricing Rule", existing, "service_charge"):
                frappe.db.set_value("GoFix Pricing Rule", existing, "service_charge", fee)
                updated += 1
            continue

        doc = frappe.new_doc("GoFix Pricing Rule")
        doc.update({
            "rule_name": f"{RULE_PREFIX} — {sub_category or category}",
            "is_active": 1,
            "device_category": category,
            "device_sub_category": sub_category,
            "service_charge": fee,
            "labor_rate_type": "Fixed",
            # Deliberately no labour rate: this rule prices handling the
            # device, not repairing it.
            "priority_order": 100,
        })
        doc.flags.ignore_permissions = True
        doc.insert(ignore_permissions=True)
        created += 1

    frappe.db.commit()
    print(f"Bench fee ladder: {created} created, {updated} updated, {skipped} scope(s) absent here")
