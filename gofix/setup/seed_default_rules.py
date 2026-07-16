# Copyright (c) 2025, GoFix and contributors
# For license information, please see license.txt

"""
Seed minimal default SLA rules and approval rules for GoFix.
Idempotent — safe to run multiple times; skips entries that already exist.

Usage:
    bench --site erpnext.local execute gofix.setup.seed_default_rules.execute
"""

import frappe


# ──────────────────────────────────────────────────────────────
# SLA RULES  (catch-all + per issue-category)
# ──────────────────────────────────────────────────────────────

_SLA_RULES = [
    # catch-all — must come first (lowest priority, matched last by get_sla_rule)
    {
        "rule_name": "Default - All Categories",
        "target_hours": 24.0,
        "warning_pct": 80,
        "is_active": 1,
        "escalation_1_role": "Service Manager",
        "escalation_2_role": "System Manager",
    },
    # category-specific rules — categories match the canonical taxonomy
    # seeded by gofix.patches.seed_gofix_token_masters
    {
        "rule_name": "Screen & Display - High",
        "issue_category": "Screen & Display",
        "priority": "High",
        "target_hours": 4.0,
        "warning_pct": 75,
        "is_active": 1,
        "escalation_1_role": "Service Manager",
        "escalation_2_role": "System Manager",
    },
    {
        "rule_name": "Screen & Display - Medium",
        "issue_category": "Screen & Display",
        "priority": "Medium",
        "target_hours": 6.0,
        "warning_pct": 75,
        "is_active": 1,
        "escalation_1_role": "Service Manager",
    },
    {
        "rule_name": "Battery - Any",
        "issue_category": "Battery",
        "target_hours": 4.0,
        "warning_pct": 80,
        "is_active": 1,
        "escalation_1_role": "Service Manager",
    },
    {
        "rule_name": "Charging & Power - Any",
        "issue_category": "Charging & Power",
        "target_hours": 6.0,
        "warning_pct": 80,
        "is_active": 1,
        "escalation_1_role": "Service Manager",
    },
    {
        "rule_name": "Water Damage - High",
        "issue_category": "Water Damage",
        "priority": "High",
        "target_hours": 8.0,
        "warning_pct": 75,
        "is_active": 1,
        "escalation_1_role": "Service Manager",
        "escalation_2_role": "System Manager",
    },
    {
        "rule_name": "Physical Damage - Any",
        "issue_category": "Physical Damage",
        "target_hours": 12.0,
        "warning_pct": 80,
        "is_active": 1,
        "escalation_1_role": "Service Manager",
    },
    {
        "rule_name": "Board Diagnosis - Any",
        "issue_category": "Board Diagnosis",
        "target_hours": 48.0,
        "warning_pct": 75,
        "is_active": 1,
        "escalation_1_role": "Service Manager",
        "escalation_2_role": "System Manager",
    },
    {
        "rule_name": "Data Recovery - Any",
        "issue_category": "Data Recovery",
        "target_hours": 72.0,
        "warning_pct": 80,
        "is_active": 1,
        "escalation_1_role": "Service Manager",
    },
]


# ──────────────────────────────────────────────────────────────
# APPROVAL RULES  (one per rule_type, company-agnostic)
# ──────────────────────────────────────────────────────────────
# threshold_amount = 0 means "always require approval for this type"

_APPROVAL_RULES = [
    {
        "rule_type": "High Estimate",
        "rule_name": "High Estimate > 5,000",
        "threshold_amount": 5000,
        "approver_role": "Service Manager",
        "escalation_role": "System Manager",
        "auto_reject_hours": 48,
        "priority": 10,
        "is_active": 1,
        "description": "Require Service Manager approval for repair estimates exceeding ₹5,000.",
    },
    {
        "rule_type": "Spare Part",
        "rule_name": "Spare Part Usage > 2,000",
        "threshold_amount": 2000,
        "approver_role": "Service Manager",
        "auto_reject_hours": 24,
        "priority": 10,
        "is_active": 1,
        "description": "Require approval when spare part cost exceeds ₹2,000.",
    },
    {
        "rule_type": "Free Repair",
        "rule_name": "Free Repair - Always Approve",
        "threshold_amount": 0,
        "approver_role": "Service Manager",
        "escalation_role": "System Manager",
        "auto_reject_hours": 24,
        "priority": 10,
        "is_active": 1,
        "description": "All free repairs (spare parts used, zero invoice) require Service Manager sign-off.",
    },
    {
        "rule_type": "Discount",
        "rule_name": "Discount - Always Approve",
        "threshold_amount": 0,
        "approver_role": "Service Manager",
        "auto_reject_hours": 24,
        "priority": 10,
        "is_active": 1,
        "description": "Any discount on a service estimate requires approval.",
    },
    {
        "rule_type": "Replacement",
        "rule_name": "Replacement - Always Approve",
        "threshold_amount": 0,
        "approver_role": "Service Manager",
        "escalation_role": "System Manager",
        "auto_reject_hours": 48,
        "priority": 10,
        "is_active": 1,
        "description": "Device replacement decisions always require Service Manager approval.",
    },
    {
        "rule_type": "Write-Off",
        "rule_name": "Write-Off - Always Approve",
        "threshold_amount": 0,
        "approver_role": "System Manager",
        "auto_reject_hours": 72,
        "priority": 10,
        "is_active": 1,
        "description": "Write-off of stock or assets requires System Manager approval.",
    },
    {
        "rule_type": "Beyond Repair",
        "rule_name": "Beyond Repair - Always Approve",
        "threshold_amount": 0,
        "approver_role": "Service Manager",
        "escalation_role": "System Manager",
        "auto_reject_hours": 48,
        "priority": 10,
        "is_active": 1,
        "description": "Beyond Repair / Not Repairable decisions require Service Manager approval.",
    },
]


def _issue_category_exists(name):
    return frappe.db.exists("Issue Category", name)


def seed_sla_rules():
    created = 0
    skipped = 0
    for rule in _SLA_RULES:
        # Skip if the category referenced doesn't exist in this site
        ic = rule.get("issue_category")
        if ic and not _issue_category_exists(ic):
            print(f"  ⚠  Skipping SLA rule '{rule['rule_name']}' — Issue Category '{ic}' not found")
            skipped += 1
            continue

        if frappe.db.exists("GoFix SLA Rule", {"rule_name": rule["rule_name"]}):
            skipped += 1
            continue

        doc = frappe.new_doc("GoFix SLA Rule")
        doc.update(rule)
        doc.insert(ignore_permissions=True)
        created += 1
        print(f"  ✅ SLA Rule: {rule['rule_name']}")

    frappe.db.commit()
    print(f"\n  SLA Rules  — created: {created}, skipped: {skipped}")


def seed_approval_rules():
    created = 0
    skipped = 0
    for rule in _APPROVAL_RULES:
        if frappe.db.exists("GoFix Approval Rule", {"rule_type": rule["rule_type"], "rule_name": rule["rule_name"]}):
            skipped += 1
            continue

        doc = frappe.new_doc("GoFix Approval Rule")
        doc.update(rule)
        doc.insert(ignore_permissions=True)
        created += 1
        print(f"  ✅ Approval Rule: {rule['rule_type']} — {rule['rule_name']}")

    frappe.db.commit()
    print(f"\n  Approval Rules — created: {created}, skipped: {skipped}")


def execute():
    print("\n" + "=" * 60)
    print("  GoFix Default Rules — Seeding")
    print("=" * 60)

    print("\n── SLA Rules ──")
    seed_sla_rules()

    print("\n── Approval Rules ──")
    seed_approval_rules()

    print("\n" + "=" * 60)
    print("  Done")
    print("=" * 60 + "\n")
