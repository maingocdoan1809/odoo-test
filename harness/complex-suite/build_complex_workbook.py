"""Build the 10-case complex workbook for the oqa_harness module."""
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

ROOT = Path(__file__).resolve().parent

CASES = [
    ("T01", "Export import-compatible, sửa file, import cập nhật", "Manager", [
        'Tạo một site tên duy nhất; từ "t01-seed.csv" tạo bản chạy (token {run}, {site}) rồi import 3 item (Name, Code, Site, Cost = 5, 8, 13).',
        "Ở list Items lọc theo tiền tố code của lần chạy, chọn cả 3 item, Export ở chế độ \"I want to update data (import-compatible)\" với các cột External ID, Name, Code, Site, Cost và tải file.",
        "Sửa file đã tải (không đổi External ID): cost item 1 từ 5 thành 6, item 2 từ 8 thành 0, item 3 từ 13 thành 15.",
        "Import file đã sửa, bấm Test trước và đọc kết quả; sau đó sửa cost 0 thành 9 và import lại.",
        "Mở lại list Items theo cùng bộ lọc và kiểm tra số item cùng cost của từng item.",
        "Tạo request ở site đó với item 1 số lượng 2, lưu và đọc tổng.",
    ], "Export tải được file có 3 dòng và cột External ID. Lần import đầu bị chặn vì cost không dương và không item nào bị cập nhật. Sau khi sửa vẫn chỉ có 3 item (không tạo mới), cost là 6, 9, 15. Request có tổng 12."),
    ("T02", "Import 6 dòng nhiều loại lỗi, sửa từng vòng", "Manager", [
        "Tạo site tên duy nhất và một item đối chứng có code riêng (dùng làm token {existing}) cost 3.",
        'Từ "t02-mixed.csv" tạo bản chạy (token {run}, {site}, {existing}) và mở wizard Import trên Items, map đủ 4 cột.',
        "Bấm Test và ghi lại lỗi theo từng dòng (cost bằng 0, cost âm, cost là chữ, site không tồn tại, code trùng item đối chứng).",
        "Thực hiện Import (không chỉ Test) và kiểm tra list Items theo tiền tố code: dòng hợp lệ có được lưu không.",
        "Sửa lần lượt từng lỗi trong file (mỗi vòng Test lại), cho đến khi Test sạch; code trùng đổi thành code mới, site sai đổi thành site thật.",
        "Import bản sạch rồi lọc list theo tiền tố code, đồng thời mở item đối chứng.",
    ], "Mọi dòng lỗi đều được báo bằng thông báo đọc được, không lô nào được lưu một phần. Sau cùng có đúng 6 item mới đúng site cùng item đối chứng còn nguyên cost 3 (tổng 7 mã theo tiền tố)."),
    ("T03", "Import Request nhiều dòng và chặn sai ràng buộc", "Manager", [
        'Tạo hai site tên duy nhất; từ "t03-items.csv" tạo bản chạy (token {run}, {site}, {site2}) và import 3 item (A cost 7, B cost 11 ở site 1; C cost 4 ở site 2).',
        'Từ "t03-requests-bad.csv" tạo bản chạy và import vào Requests (map Name, Site, Lines/Item theo Code, Lines/Quantity): REQ1 có 2 dòng hợp lệ, REQ2 dùng item C của site 2.',
        "Đọc lỗi của wizard, kiểm tra list Requests theo tiền tố: REQ1 có được lưu không.",
        'Dùng "t03-requests-fixed.csv" (REQ2 đổi sang item A) và import; mở REQ1 và REQ2, ghi subtotal từng dòng và tổng.',
        'Import "t03-requests-dup.csv" (REQ3 có item A hai lần) và đọc kết quả; kiểm tra REQ3 trong list.',
    ], "Lần import đầu báo item không thuộc site của request và không lưu cả REQ1. Lần sửa lưu REQ1 (21, 22, tổng 43) và REQ2 (7, tổng 7). Lần dup báo item chỉ xuất hiện một lần trong request và REQ3 không tồn tại."),
    ("T04", "Export Requests đã lọc, cột one2many và trạng thái", "Manager", [
        "Tạo site, hai item (cost 6 và 9) và ba request R1, R2, R3 mỗi request có ít nhất 1 dòng; R1 để Draft, R2 gồm 2 dòng rồi Submit, R3 Submit rồi Approve.",
        "Ở list Requests lọc theo tiền tố của lần chạy, chọn chỉ R2 và R3 rồi Export (không chọn import-compatible) với các cột Name, Site, State, Total, Lines/Item, Lines/Quantity, Lines/Subtotal; tải file CSV.",
        "Mở file và đối chiếu từng dòng với màn hình form của R2 và R3.",
        "Quay lại list, chọn lại cả ba request, Export tất cả cột như trên và đếm số request khác nhau trong file.",
    ], "File chỉ có R2 và R3 (không có R1). State ghi Submitted và Approved, tổng và subtotal khớp form, R2 chiếm 2 dòng dữ liệu. File thứ hai chứa đủ R1, R2, R3."),
    ("T05", "Phục hồi tuần tự nhiều lỗi ràng buộc trong một request", "Manager", [
        "Tạo hai site A, B; ở A có item a1 (cost 4) và a2 (cost 6); ở B có item b1 (cost 5).",
        "Tạo request ở site A, thêm a1 số lượng 2, lưu và đọc tổng.",
        "Thêm dòng a1 lần nữa rồi lưu; đọc lỗi, bỏ dòng đó. Thêm dòng b1 rồi lưu; đọc lỗi. Đổi dòng b1 thành a2 số lượng 0 rồi lưu; đọc lỗi. Đổi số lượng thành 5 và lưu.",
        "Đổi site của request sang B và lưu; đọc lỗi rồi bỏ thay đổi.",
        "Submit, Reset to Draft, đổi số lượng a1 thành 10, Submit lại, tải lại trang.",
    ], "Ba lỗi lần lượt: item chỉ một lần mỗi request, item phải thuộc site của request, quantity phải dương. Sau khi lưu a2×5 tổng 38. Đổi site sang B bị từ chối, site vẫn là A. Cuối cùng trạng thái Submitted với tổng 70 sau khi tải lại."),
    ("T06", "Ràng buộc tham chiếu, archive và dùng lại mã", "Manager", [
        "Tạo site S, item I (code duy nhất, gọi là X) thuộc S và request R có một dòng dùng I.",
        "Thử xóa item I rồi thử xóa site S; đọc lỗi từng lần.",
        "Archive item I; mở lại R và kiểm tra dòng; tạo request mới và mở danh sách chọn item để xem I có xuất hiện không.",
        "Tạo item mới với code X (cùng mã của I đã archive) rồi lưu; đọc kết quả.",
        "Xóa dòng trong R, xóa R, bật bộ lọc Archived và xóa I; sau đó tạo lại item với code X và xóa site S.",
    ], "Xóa I và xóa S đều bị chặn vì đang được tham chiếu. Sau archive, R vẫn còn dòng và I không được gợi ý khi chọn item. Tạo item mới trùng X bị báo Item code must be unique dù I đã archive. Khi hết tham chiếu, xóa I thành công, code X dùng lại được và xóa S thành công."),
    ("T07", "Hai vai trò: import, đổi giá khi đang chờ duyệt", "Operator, Manager", [
        'Manager: tạo site tên duy nhất; từ "t07-items.csv" tạo bản chạy và import 2 item (cost 10 và 20).',
        "Operator: tạo request ở site đó, thêm item 1 số lượng 2 và item 2 số lượng 1, lưu rồi Submit; ghi tổng.",
        "Manager: mở item 1, đổi cost từ 10 sang 15 và lưu.",
        "Operator: mở lại chính request đó, đọc tổng, trạng thái và các nút hiện có.",
        "Manager: mở request, Approve; sau đó cả Manager và Operator tải lại request và đọc nút hiện có.",
    ], "Tổng ban đầu 40. Sau khi đổi giá, tổng của request đang Submitted tự tính lại thành 50. Operator không thấy nút Approve. Sau khi Manager Approve, cả hai vai trò thấy Approved và không còn nút Submit, Approve hay Reset to Draft."),
    ("T08", "Biên giá trị cost", "Manager", [
        "Tạo site, item biên 1 có cost 1 và item biên 2 có cost 2147483647.",
        "Tạo request với item biên 2 số lượng 1, lưu và đọc tổng.",
        "Tăng số lượng lên 3 rồi lưu.",
        "Tải lại request và kiểm tra dữ liệu.",
    ], "Hệ thống xử lý hợp lý và không làm mất dữ liệu."),
    ("T09", "Phân quyền Operator và khóa sửa khi đã duyệt", "Operator, Manager", [
        "Operator: tạo site tên duy nhất.",
        "Operator: tạo item thuộc site đó.",
        "Operator: tạo request có một dòng rồi Submit.",
        "Manager: Approve request rồi mở lại.",
        "Manager: đổi số lượng của dòng trong request đã Approved rồi lưu.",
    ], "Operator bị từ chối khi tạo site (Access Error). Với request đã Approved, dòng ở trạng thái chỉ đọc và không lưu được thay đổi."),
    ("T10", "Dữ liệu mã cố định trong workbook, chạy lặp lại", "Manager", [
        'Tạo site tên "Fixed Site" mã "SITE-FIX".',
        'Tạo item tên "Widget Fixed" mã "SKU-001" cost 5 thuộc site đó.',
        'Tạo request tên "REQ-FIX" ở site đó, thêm dòng SKU-001 số lượng 4, lưu rồi Submit.',
        'Kiểm tra dòng hiển thị "[SKU-001] Widget Fixed", tổng 20 và trạng thái Submitted.',
    ], 'Dòng hiển thị "[SKU-001] Widget Fixed", tổng 20, trạng thái Submitted.'),
]


def main():
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Testcases"
    sheet.append(("ID", "Title", "Role", "Steps", "Expected Result", "Status", "Evidence"))
    for case_id, title, role, steps, expected in CASES:
        sheet.append((case_id, title, role, "\n".join(f"{n}. {s}" for n, s in enumerate(steps, 1)), expected, None, None))
        sheet.row_dimensions[sheet.max_row].height = 150
    for column, width in zip("ABCDEFG", (10, 38, 18, 80, 70, 20, 20)):
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
    workbook.save(ROOT / "odoo-harness-complex-10.xlsx")


if __name__ == "__main__":
    main()
