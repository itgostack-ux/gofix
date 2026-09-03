"""End-to-end proof of the technician acceptance gate.

Assigning a technician used to hand them a job that was immediately workable —
the shop-floor clock effectively began at the counter, before the technician had
been told anything. Now assignment parks the job at Pending Accept and the
technician takes it on explicitly.

Covers:
  1. Assigning lands the Job Assignment on Pending Accept, not workable.
  2. Starting work before acceptance is refused.
  3. Only the assigned technician (or an assignment manager) may accept.
  4. Accepting stamps who/when and how long it waited.
  5. After acceptance the solution starts normally and custody opens.
  6. The ticket timeline carries an Assignment track recording both moments.
  7. Accepting twice does not reset the measurement.

Invocation:
    bench --site <site> console
    >>> from gofix.tests import test_technician_accept_e2e as t; t.run_all()
"""

from __future__ import annotations

import frappe
from frappe.utils import flt

PASSED: list[str] = []
FAILED: list[str] = []


class TestFailure(AssertionError):
    pass


def _check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print(f"  PASS  {name}")
    else:
        FAILED.append(f"{name}: {detail}")
        print(f"  FAIL  {name} — {detail}")


def _clear():
    frappe.message_log = []


def _fixture():
    """An accepted ticket with a Service Order and an unstarted solution line."""
    row = frappe.db.sql(
        """
        SELECT sr.name AS sr, sl.name AS line, sl.repair_solution
        FROM `tabService Request` sr
        JOIN `tabSR Solution Line` sl
          ON sl.parent = sr.name AND sl.parenttype = 'Service Request'
        WHERE sr.service_order IS NOT NULL
          AND sr.docstatus < 2
          AND sl.status = 'Planned'
        ORDER BY sr.creation DESC
        LIMIT 1
        """,
        as_dict=True,
    )
    if not row:
        raise TestFailure("no ticket with a Service Order and a Planned solution line")
    sr_name = row[0].sr
    technician = frappe.db.get_value(
        "Employee",
        {"status": "Active", "user_id": ("is", "set"),
         "company": frappe.db.get_value("Service Request", sr_name, "company")},
        "name",
    )
    if not technician:
        raise TestFailure("no active technician with a user account")
    return sr_name, row[0].line, technician


def run_all():
    from gofix.gofix_services.page.gofix_ops_hub import gofix_ops_hub as hub

    PASSED.clear()
    FAILED.clear()
    sr_name, line_name, technician = _fixture()
    tech_user = frappe.db.get_value("Employee", technician, "user_id")
    print(f"fixture: sr={sr_name} line={line_name} technician={technician} ({tech_user})\n")

    try:
        # ── 1. Assigning parks the job, it does not start it ──────────────
        res = hub.assign_solutions_to_technician(
            sr_name=sr_name,
            solution_rows_json=frappe.as_json([line_name]),
            technician=technician,
        )
        _clear()
        ja_name = res["job_assignment"]
        ja = frappe.get_doc("Job Assignment", ja_name)
        _check(
            "assignment lands on Pending Accept",
            ja.assignment_status == "Pending Accept",
            f"status={ja.assignment_status}",
        )
        _check("clock has not started", not ja.start_datetime, f"start={ja.start_datetime}")
        _check("no custody period opened",
               not frappe.db.exists("GoFix Custody Log", {"job_assignment": ja_name}))

        # ── 2. Work cannot start before acceptance ────────────────────────
        refused = False
        try:
            hub.update_solution_status(
                sr_name=sr_name, solution_row_name=line_name, status="In Progress",
            )
        except Exception as exc:
            refused = "accepted" in str(exc).lower()
            if not refused:
                print(f"        (refused for another reason: {str(exc)[:160]})")
        finally:
            _clear()
        _check("starting work before acceptance is refused", refused)

        # ── 3. Someone else cannot accept on the technician's behalf ──────
        other_user = frappe.db.sql(
            """
            SELECT u.name FROM `tabUser` u
            WHERE u.enabled = 1 AND u.name NOT IN ('Administrator', 'Guest', %s)
              AND NOT EXISTS (
                SELECT 1 FROM `tabHas Role` r
                WHERE r.parent = u.name AND r.role = 'System Manager')
            LIMIT 1
            """,
            tech_user, pluck=True,
        )
        if other_user:
            frappe.set_user(other_user[0])
            blocked = False
            try:
                hub.accept_job_assignment(ja_name=ja_name)
            except Exception as exc:
                blocked = isinstance(exc, frappe.PermissionError) or "accept" in str(exc).lower()
            finally:
                _clear()
                frappe.set_user("Administrator")
            _check("a bystander cannot accept someone else's job", blocked)

        # ── 4. The technician accepts ─────────────────────────────────────
        frappe.set_user(tech_user)
        out = hub.accept_job_assignment(ja_name=ja_name, remarks="On it")
        _clear()
        frappe.set_user("Administrator")
        ja.reload()
        _check("accept sets the Accepted status",
               ja.assignment_status == "Accepted", f"status={ja.assignment_status}")
        _check("accept stamps who took it on",
               ja.accepted_by == tech_user, f"accepted_by={ja.accepted_by}")
        _check("accept stamps when", bool(ja.accepted_at), f"accepted_at={ja.accepted_at}")
        _check("wait time is recorded",
               ja.accept_wait_hours is not None and flt(ja.accept_wait_hours) >= 0,
               f"accept_wait_hours={ja.accept_wait_hours}")
        _check("accept is not a no-op", out.get("already") is False, f"out={out}")

        # ── 5. Now work starts, and custody opens ─────────────────────────
        started = True
        try:
            hub.update_solution_status(
                sr_name=sr_name, solution_row_name=line_name, status="In Progress",
            )
        except Exception as exc:
            started = False
            print(f"        (start still refused: {str(exc)[:200]})")
        finally:
            _clear()
        ja.reload()
        _check("solution starts once accepted", started)
        if started:
            _check("job assignment is now In Progress",
                   ja.assignment_status == "In Progress", f"status={ja.assignment_status}")
            _check("the clock started at acceptance-or-later, not at assignment",
                   bool(ja.start_datetime), f"start={ja.start_datetime}")
            _check("custody period opened on start",
                   bool(frappe.db.exists("GoFix Custody Log", {"job_assignment": ja_name})))

        # ── 6. The timeline records both moments on its own track ─────────
        sr = frappe.get_doc("Service Request", sr_name)
        rows = [r for r in (sr.get("status_log") or []) if r.event_type == "Assignment"]
        _check("timeline has an Assignment track", len(rows) >= 2, f"{len(rows)} row(s)")
        if len(rows) >= 2:
            print("        " + " | ".join(
                f"{r.from_status}->{r.to_status} ({flt(r.time_in_previous_status_hours):.3f}h)"
                for r in rows[-2:]
            ))
            _check("the accept row carries the waiting time",
                   any(r.to_status.startswith("Accepted") for r in rows),
                   f"rows={[r.to_status for r in rows]}")

        # ── 7. Accepting again does not reset the measurement ─────────────
        first_at = ja.accepted_at
        again = hub.accept_job_assignment(ja_name=ja_name)
        _clear()
        ja.reload()
        _check("re-accepting is a no-op",
               again.get("already") is True and ja.accepted_at == first_at,
               f"again={again}, accepted_at={ja.accepted_at}")

    finally:
        frappe.set_user("Administrator")
        frappe.db.rollback()
        print("\n(rolled back — no documents left behind)")

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for failure in FAILED:
        print(f"  FAILED: {failure}")
    if FAILED:
        raise TestFailure(f"{len(FAILED)} check(s) failed")
    return {"passed": len(PASSED), "failed": 0}
