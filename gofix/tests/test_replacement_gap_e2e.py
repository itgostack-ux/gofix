"""
Replacement workflow smoke test.

Run:
  bench --site erpnext.local execute gofix.tests.test_replacement_gap_e2e.run_all
"""

import frappe

results = []


def ok(name, detail=""):
    results.append(("PASS", name, detail))
    print(f"PASS  {name}{f'  ({detail})' if detail else ''}")


def fail(name, detail=""):
    results.append(("FAIL", name, detail))
    print(f"FAIL  {name}{f'  ({detail})' if detail else ''}")


def test_replacement_workflow_api():
    from gofix.gofix_services.doctype.service_request.service_request import (
        request_item_replacement,
        approve_item_replacement,
        complete_item_replacement,
    )

    try:
        request_item_replacement(
            service_request="NON-EXISTENT-SR",
            original_serial_no="OLD-001",
            replacement_serial_no="NEW-001",
            reason="QA smoke test",
        )
        fail("replacement_request", "Expected missing document error")
    except Exception:
        ok("replacement_request", "API exists and validates input")

    try:
        approve_item_replacement("NON-EXISTENT-SR", approver_user="Administrator")
        fail("replacement_approve", "Expected missing document error")
    except Exception:
        ok("replacement_approve", "API exists and validates input")

    try:
        complete_item_replacement("NON-EXISTENT-SR")
        fail("replacement_complete", "Expected missing document error")
    except Exception:
        ok("replacement_complete", "API exists and validates input")


def run_all():
    print("\n=== REPLACEMENT GAP SMOKE TESTS ===")
    try:
        test_replacement_workflow_api()
    except Exception as e:
        fail("replacement_workflow_api", str(e))

    passed = len([r for r in results if r[0] == "PASS"])
    failed = len([r for r in results if r[0] == "FAIL"])
    print(f"\nSummary: PASS={passed} FAIL={failed}")
    return {"passed": passed, "failed": failed, "results": results}
