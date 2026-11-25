---
name: odoo-test
description: Run Odoo testcase workbooks with workspace selection, per-case review, live Playwright evidence, saved replay scripts and Excel results. Use for Odoo testcase requests or /odoo-test.
---

# odoo-test

Use `python "<folder containing this SKILL.md>/oqa.py" <command>`, abbreviated as `oqa`. Keep the workspace and session ID from intake and pass them explicitly to worker commands.

## Ngôn ngữ

- Bước testcase, kết quả mong đợi, phân tích, câu hỏi và quan sát do AI viết: tiếng Việt.
- Text hiển thị trong Odoo: trích nguyên văn, đúng ngôn ngữ gốc của giao diện.
- Mỗi bước replay được: điền `captions.en` và `captions.ja`, ngắn gọn, tương đương hành động tiếng Việt.
- `--caption` bổ sung trên ảnh chụp: dùng Evidence caption language của workspace.
- Mỗi case phân tích có tiêu đề tiếng Việt ngắn nêu hành vi được kiểm tra, ví dụ "Chặn số lượng âm khi lưu phiếu". Giữ ID của workbook để truy vết.

## Căn cứ: BD (thiết kế) > testcase > app

- Mỗi bước phân tích ghi `oracle` = `{source: bd|testcase, ref, quote}`.
- `bd`: trích đúng đoạn BD (ref là tài liệu đã đánh dấu ok kèm mục).
- `testcase`: chỉ khi không BD nào bao phủ điểm đó; case điền `bd_searched` (đã tìm BD nào, vì sao không bao phủ).
- App sai so với BD, hoặc sai so với testcase khi không có BD, là FAIL. `oqa live end` ghi căn cứ vào lý do.
- Testcase mâu thuẫn BD: ghi `testcase_conflicts` `[{step, testcase, bd, ref}]`, bước đó chạy theo BD, người dùng được cảnh báo khi nộp phân tích và trong strategy.
- Hành vi hiện tại của app không bao giờ là căn cứ.

## Workbook run

1. `oqa run start --file <workbook> [--cases <selection>]` chạy nền. Lệnh mở workspace picker và trả về khi người dùng lưu. Dùng `--channel addin` khi gọi từ Excel. Thiếu phụ thuộc thì xem `oqa doctor`.
2. `oqa analyze --template`. Đọc `_context.sitemap` (route khả dĩ) và `_context.knowledge` (validation, điều kiện, evidence, lỗi đã biết) của từng case; kiểm tra workbook và tài liệu trích dẫn trước khi nhận một quy tắc. Mở rộng bước thành hành động và kiểm tra quan sát được trên UI (bước xác nhận trên database vẫn giữ nguyên là một bước, bằng chứng là ảnh PgAdmin), mỗi bước có mô tả `evidence` và control/vùng cần khoanh. Đọc [evidence](references/evidence.md). Thiếu hoặc mơ hồ route/quy tắc: [case retrieval](references/contract.md#case-retrieval).
3. `oqa analyze --submit <file>`. Dashboard hiện case gốc và case đã phân tích; case cần input có câu hỏi và nút bỏ qua riêng. Theo dõi bằng `oqa wait --until reviews`. Xong review thì lấy lại template, đọc feedback/references và nộp lại **trong cùng workspace/session** (không gọi `session new` giữa chừng). Sau đó `oqa connect`, `oqa strategy`, và `oqa wait` chờ người dùng duyệt trên dashboard.
4. Chạy `oqa dispatch` nền (backend worker Claude: tối đa 5 case cho Haiku, Sonnet hỗ trợ khi cần phục hồi), hoặc với worker do host quản lý: `oqa claim --agent WORKER --case ID --packet`. Case phụ thuộc chạy sau case tiền đề. Xem [worker](references/agents.md). Worker dừng: `oqa live recover ID --reason <chẩn đoán>` rồi giao lại một lần. Trao đổi bằng `oqa note` / `oqa notes`.
5. Worker: `oqa live start ID`, đọc UI thật, thao tác, ghi từng bước bằng `oqa live step`. Chụp mọi hành động và trạng thái trung gian (mở form, điền trường, nút trước khi bấm, kết quả sau khi bấm) bằng `oqa live shot --caption TEXT`. Khoanh một đích nhìn thấy, duy nhất, liên quan ảnh; kiểm tra ảnh và sửa khung sai. Phần tử vắng mặt thì khoanh view cha nhỏ nhất. Bước testcase nói "xác nhận trên database" / "chụp kết quả query PgAdmin": ghi bước bằng `oqa live step` (khoanh bản ghi đang hiện trên màn hình), rồi `oqa pgadmin shot ID --n N --sql "SELECT ..."` (chỉ truyền SQL đọc; không tự mở PgAdmin, không hỏi mật khẩu; đọc [evidence](references/evidence.md#database-evidence-pgadmin)). Kết thúc `oqa live end ID --status <status>`.
6. `oqa report` và trả đường dẫn kết quả. Chạy bằng path: lưu bản sao cạnh file nguồn. Chạy add-in: ghi vào workbook đang mở trong Excel desktop (Windows).

## Kết luận

- FAIL: app khác kỳ vọng; cần `--control` (phần tử đã thao tác và cách xác minh đó là đúng phần tử).
- Lỗi script, selector, trình duyệt hay thiếu điều kiện tiên quyết là BLOCKED/PENDING, cần `oqa live diagnose CASE --category automation|precondition ...` trước. Không đóng case chỉ vì không thấy selector hay hết thời gian.
- Mã thông báo (ERR_xxx) nằm trong tài liệu thiết kế: so text thông báo theo ngôn ngữ UI (`oqa case context` liệt kê), không đòi mã hiện trên màn hình.
- PASS phải hiện giá trị mà kỳ vọng nêu (số lượng, ngày, nội dung), không phải nhãn lúc nào cũng có.
- Chuyển case đang chạy cho worker khác mà không mất bước hay ngân sách phục hồi: `oqa live handover CASE --reason ... [--to WORKER]`, worker mới chạy `oqa live start CASE`.

## Dữ liệu và replay (quy tắc không được bỏ)

- Mọi tên/mã bản ghi do case tạo lấy từ `oqa fixture key`. Gõ tên cố định kiểu `AUTO_TEST_x` bị từ chối; bước kế hoạch không ghi tên hay mã cố định.
- Fixture tạo qua màn hình, không bằng ORM write hay sửa DOM. Chi tiết và lệnh: [fixtures](references/fixtures.md). `fixture_name` dùng thuật ngữ nghiệp vụ ngắn; công cụ tự thêm `AUTO_TEST_` và hậu tố.
- Kế hoạch phải sống được qua lần chạy lại. Mỗi case khai báo `replay_records` (hoặc `replay_records_none_reason`). Chi tiết, tốc độ, kết quả Live/Replay: [replay](references/replay.md).
- Chạy lại script trên môi trường khác: [regression](references/regression.md).

## Knowledge

Trước khi lưu quy tắc hay cách xử lý đã xác minh, đọc `oqa knowledge search --screen MODEL --kind KIND`. Cùng điều kiện nghiệp vụ thì dùng lại entry cũ; `oqa knowledge learn` bổ sung fact, evidence hoặc nguồn còn thiếu. Lưu quy tắc kèm tham chiếu thiết kế hoặc xác nhận của người dùng; verdict của case hiện tại vẫn ghi trong kết quả của nó.

Public formats: [contract](references/contract.md). Odoo conventions: [Odoo reference](references/odoo.md).
