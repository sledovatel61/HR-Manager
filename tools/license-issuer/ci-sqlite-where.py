# -*- coding: utf-8 -*-
"""CI diagnostic: where inside an SQLite file (e.g. Edge "Web Data") does a leaked key sit?

Usage: python ci-sqlite-where.py <sqlite file>
Needles come from the environment variable HRM_DIAG_NEEDLES
("<name>|<form>|<value>" entries separated by newlines) - never from argv, so the
key does not appear in process-creation audit events.

Prints one "[sqlite-where] ..." line: table.column, matching row count and the
other (non-secret) columns of up to 3 matching rows (key text redacted, values
truncated). If no table row contains the key, the bytes are only in free pages /
the journal / WAL (deleted data that SQLite has not overwritten yet).
Never prints the key.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile


def redact(text: str, needles) -> str:
    for _name, _form, value in needles:
        text = text.replace(value, "<key>")
    return text


def main(argv) -> int:
    if len(argv) != 1:
        print("[sqlite-where] usage: ci-sqlite-where.py <sqlite file>")
        return 2
    src = argv[0]
    needles = []
    for line in os.environ.get("HRM_DIAG_NEEDLES", "").splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3 and parts[2]:
            needles.append((parts[0], parts[1], parts[2]))
    tmp = tempfile.mkdtemp(prefix="sqlw-")
    try:
        dst = os.path.join(tmp, "db")
        shutil.copyfile(src, dst)
        for ext in ("-wal", "-journal"):
            if os.path.exists(src + ext):
                shutil.copyfile(src + ext, dst + ext)
        con = sqlite3.connect(dst)
        found = []
        tables = [r[0] for r in con.execute("select name from sqlite_master where type='table'")]
        for table in tables:
            qt = '"' + table.replace('"', '""') + '"'
            cols = [r[1] for r in con.execute("pragma table_info(" + qt + ")")]
            for col in cols:
                qc = '"' + col.replace('"', '""') + '"'
                for name, form, value in needles:
                    sql = ("select * from " + qt + " where instr(cast(" + qc + " as text), ?) > 0"
                           " or instr(cast(" + qc + " as blob), cast(? as blob)) > 0")
                    try:
                        rows = con.execute(sql, (value, value)).fetchall()
                    except sqlite3.Error as exc:
                        found.append("{}.{}: query error {}".format(table, col, exc))
                        continue
                    if not rows:
                        continue
                    samples = []
                    for row in rows[:3]:
                        cells = []
                        for cname, cell in zip(cols, row):
                            if cname == col:
                                continue
                            if isinstance(cell, bytes):
                                cell = "<{} bytes>".format(len(cell))
                            cells.append("{}={}".format(cname, redact(str(cell), needles)[:60]))
                        samples.append("{" + ", ".join(cells) + "}")
                    found.append("{}.{}: {} row(s) contain the {} key ({}); rows: {}".format(
                        table, col, len(rows), name, form, " ".join(samples)))
        con.close()
        raw = open(dst, "rb").read()
        in_main = [n for n, f, v in needles if v.encode("ascii", "ignore") in raw]
        wal = dst + "-wal"
        in_wal = [n for n, f, v in needles if os.path.exists(wal) and v.encode("ascii", "ignore") in open(wal, "rb").read()]
        where = "; ".join(found) if found else "no table row contains a key (bytes only in free pages/journal/WAL = deleted data)"
        print("[sqlite-where] {}: {} | raw bytes: main file {}, WAL {}".format(
            os.path.basename(src), where, in_main or "none", in_wal or "none"))
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
