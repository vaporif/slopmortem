#!/usr/bin/env python3
"""Filter the bundled Crunchbase 2015 export to status=='closed' rows.

Reads:  external/crunchbase-data/companies.csv
Writes: external/crunchbase-data/companies-closed.csv
"""

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "external/crunchbase-data/companies.csv"
DST = ROOT / "external/crunchbase-data/companies-closed.csv"


def main() -> None:
    n = 0
    with SRC.open() as i, DST.open("w") as o:
        reader = csv.DictReader(i)
        writer = csv.DictWriter(o, fieldnames=reader.fieldnames)
        writer.writeheader()
        for row in reader:
            if row["status"] == "closed":
                writer.writerow(row)
                n += 1
    print(f"wrote {n} rows to {DST}")


if __name__ == "__main__":
    main()
