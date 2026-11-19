"""Render a CSV import fixture with a run-specific code and site name."""
import argparse
import csv
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("template", choices=sorted(p.stem for p in FIXTURES.glob("*.csv")))
    parser.add_argument("--run", required=True, help="Unique case/run prefix for item codes")
    parser.add_argument("--site", required=True, help="Display name of the site created for the case")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    with (FIXTURES / f"{args.template}.csv").open(encoding="utf-8", newline="") as source:
        rows = [[cell.format(run=args.run, site=args.site) for cell in row]
                for row in csv.reader(source)]
    with args.out.open("x", encoding="utf-8", newline="") as destination:
        csv.writer(destination).writerows(rows)


if __name__ == "__main__":
    main()
