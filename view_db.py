"""
view_db.py - print everything stored in the Dosely database.

    python view_db.py              # shows drugreminder.db (next to this file)
    python view_db.py other.db     # or any other SQLite file

Password hashes and long values are shortened so the output stays readable.
"""
import os
import sqlite3
import sys

here = os.path.dirname(os.path.abspath(__file__))
path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "drugreminder.db")

if not os.path.exists(path):
    sys.exit(f"No database found at {path}\nStart the app once (python app.py) so it gets created.")


def short(value, limit=26):
    text = "NULL" if value is None else str(value)
    text = text.replace("\n", " ")
    return text if len(text) <= limit else text[:limit - 3] + "..."


conn = sqlite3.connect(path)
tables = [r[0] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]

print(f"Database file: {path}\n")
for table in tables:
    columns = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    rows = conn.execute(f"SELECT * FROM {table}").fetchall()
    title = f"{table}  ({len(rows)} row{'s' if len(rows) != 1 else ''})"
    print("=" * max(len(title), 20))
    print(title)
    print("=" * max(len(title), 20))
    if not rows:
        print("(empty)\n")
        continue
    cells = [[short(v) for v in row] for row in rows]
    widths = [max(len(c), *(len(r[i]) for r in cells)) for i, c in enumerate(columns)]
    print(" | ".join(c.ljust(w) for c, w in zip(columns, widths)))
    print("-+-".join("-" * w for w in widths))
    for r in cells:
        print(" | ".join(v.ljust(w) for v, w in zip(r, widths)))
    print()
conn.close()
