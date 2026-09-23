# Copyright (c) 2026, GoStack and contributors
"""Service Request must keep room to grow.

MySQL caps a row at 65,535 bytes for everything that is not a BLOB/TEXT, and
this doctype had crept to 63,460 -- three fields from the ceiling, at which
point the next Data, Link or Select added to it fails the migrate with
"Row size too large". That is a bad way to find out, because it surfaces
during a deploy rather than during development.

All of the 63,460 was 114 varchar columns at 562 bytes each (varchar(140) in
utf8mb4). The table is already ROW_FORMAT=Dynamic, so text and longtext cost
a pointer and contribute nothing -- the varchars are the whole story.

These tests hold two lines:

  * a hard budget with real headroom, so the ceiling is hit in CI rather than
    in a deploy, and
  * the Selects stay narrow, because that is where the headroom came from and
    a Customize Form save is all it takes to widen one back to 140.
"""

from __future__ import annotations

import unittest

import frappe

DOCTYPE = "Service Request"
TABLE = f"tab{DOCTYPE}"

#: MySQL's hard limit for non-BLOB columns.
HARD_LIMIT = 65535
#: Keep this much clear. ~17 fields of room: enough that a normal feature does
#: not have to think about it, tight enough that drift is caught early.
REQUIRED_HEADROOM = 8000
#: Frappe's floor for a varchar (frappe/database/schema.py cites the MariaDB
#: row-size troubleshooting page next to it).
VARCHAR_FLOOR = 64


def _varchar_bytes():
    rows = frappe.db.sql(
        """SELECT column_name, character_maximum_length
             FROM information_schema.columns
            WHERE table_schema = DATABASE() AND table_name = %s
              AND data_type = 'varchar'""", (TABLE,), as_dict=True)
    return {r.column_name: r.character_maximum_length * 4 + 2 for r in rows}


class TestServiceRequestRowSize(unittest.TestCase):
    def test_the_row_has_room_left(self):
        used = sum(_varchar_bytes().values())
        headroom = HARD_LIMIT - used
        self.assertGreaterEqual(
            headroom, REQUIRED_HEADROOM,
            f"Service Request varchars use {used} of {HARD_LIMIT} bytes, leaving "
            f"{headroom}. Below {REQUIRED_HEADROOM} the doctype is close enough to "
            "the ceiling that a new field will fail the migrate. Give a wide field "
            "a `length`, or move a section to a detail doctype — do not just raise "
            "this number.",
        )

    def test_the_table_still_uses_the_row_format_that_makes_text_cheap(self):
        """In Compact, a text column burns 768 inline bytes instead of a pointer."""
        fmt = frappe.db.sql(
            """SELECT row_format FROM information_schema.tables
                WHERE table_schema = DATABASE() AND table_name = %s""", (TABLE,))
        self.assertEqual(
            (fmt[0][0] or "").upper(), "DYNAMIC",
            "row format is no longer Dynamic — every text/longtext column on the "
            "doctype now counts against the row limit too",
        )

    def test_the_selects_did_not_drift_back_to_140(self):
        """The headroom came from here, and Customize Form can undo it."""
        widths = _varchar_bytes()
        meta = frappe.get_meta(DOCTYPE)
        wide = [
            d.fieldname for d in meta.fields
            if d.fieldtype == "Select"
            and widths.get(d.fieldname, 0) > (VARCHAR_FLOOR * 4 + 2)
        ]
        self.assertEqual(
            wide, [],
            f"these Selects are wider than varchar({VARCHAR_FLOOR}) again: {wide}. "
            "A Select only ever stores one of its own options; set length=64 on "
            "the docfield (or the Custom Field) rather than spending 562 bytes.",
        )

    def test_no_select_option_could_ever_overflow_its_column(self):
        """The shrink is only safe while the options stay short."""
        widths = _varchar_bytes()
        meta = frappe.get_meta(DOCTYPE)
        for d in meta.fields:
            if d.fieldtype != "Select" or not d.options:
                continue
            chars = (widths.get(d.fieldname, 0) - 2) // 4
            if not chars:
                continue
            longest = max((len(o.strip()) for o in d.options.split("\n")), default=0)
            self.assertLessEqual(
                longest, chars,
                f"{d.fieldname} is varchar({chars}) but its longest option is "
                f"{longest} chars — saving that option would raise 'Data too long'",
            )

    def test_stored_values_fit_their_columns(self):
        """Belt and braces against a value that predates the shrink."""
        widths = _varchar_bytes()
        cols = [c for c, b in widths.items() if (b - 2) // 4 <= VARCHAR_FLOOR]
        if not cols or not frappe.db.count(DOCTYPE):
            self.skipTest("nothing narrow to check, or no rows on this site")
        parts = ", ".join(f"MAX(CHAR_LENGTH(`{c}`)) AS `{c}`" for c in cols)
        row = (frappe.db.sql(f"SELECT {parts} FROM `{TABLE}`", as_dict=True) or [{}])[0]
        for col in cols:
            allowed = (widths[col] - 2) // 4
            used = row.get(col) or 0
            self.assertLessEqual(
                used, allowed,
                f"{col} holds a {used}-char value but is varchar({allowed})",
            )
