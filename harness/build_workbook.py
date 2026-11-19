"""Build the input workbook from the reviewable case matrix."""
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

ROOT = Path(__file__).resolve().parent


def main():
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Testcases"
    sheet.append(("ID", "Title", "Role", "Steps", "Expected Result", "Status", "Evidence"))
    for line in (ROOT / "cases.txt").read_text(encoding="utf-8").splitlines():
        case_id, title, role, sequence, expected = line.split("|")
        steps = "\n".join(f"{n}. {step}" for n, step in enumerate(sequence.split(";"), 1))
        sheet.append((case_id, title, role, steps, expected, None, None))
        sheet.row_dimensions[sheet.max_row].height = 100
    for column, width in zip("ABCDEFG", (10, 35, 15, 66, 62, 20, 20)):
        sheet.column_dimensions[column].width = width
    for row in sheet.iter_rows():
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            if cell.row == 1:
                cell.font = Font(bold=True)
    sheet.freeze_panes = "D2"
    sheet.auto_filter.ref = sheet.dimensions
    marker = workbook.create_sheet("Workbook integrity")
    marker.append(("Preserve this worksheet", "Formula"))
    marker.append(("Write-back control", "=1+1"))
    workbook.save(ROOT / "odoo-harness-20.xlsx")


if __name__ == "__main__":
    main()
