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
3. `oqa analyze --submit <file>`. Dashboard hiện case gốc và case đã phân tích; case cần input có câu hỏi và nút bỏ qua riêng. Theo dõi bằng `oqa wait --until reviews`. Xong review thì lấy lại template, đọc feedback/references và nộp lại **trong cùng workspace/session** (không gọi `session new` giữa chừng). Sau đó `oqa connect` (đăng nhập Odoo, và PgAdmin nếu workspace có khai báo; PgAdmin không kết nối được thì dừng ngay, báo người dùng chạy `oqa init` sửa), `oqa strategy`, và `oqa wait` chờ người dùng duyệt trên dashboard.
4. Giao case cho agent của chính CLI đang chạy (Claude Code Agent tool, Copilot CLI agent...): `oqa dispatch --plan` nhận các case sẵn sàng (tối đa 5; nếu workspace đã có knowledge nghiệp vụ mà các case chưa được chọn knowledge, lệnh này in bản giao việc cho MỘT agent rẻ (haiku) chọn theo nghĩa những mục mỗi case cần, agent chạy `oqa knowledge pick --file`, rồi chạy `--plan` lại; `--skip-knowledge` để bỏ bước này) và in một bản giao việc cho mỗi case; mỗi bản giao cho một agent. Agent xong thì `oqa dispatch --settle CASE` (replay script, kiểm tra kết luận có do dữ liệu sót). Script PASS mà replay lỗi: giữ nguyên kết quả live, `oqa dispatch --repair CASE` in bản giao cho một agent chỉ sửa script (không chạy lại case), rồi `--settle` lại; không sửa được mới `--settle CASE --reopen`. Người dùng tích chọn case ở dashboard (mặc định tích hết); case chưa chọn chạy tiếp được bằng dashboard hoặc `oqa run-more`, trong cùng phiên. oqa không tự chạy model nào: mọi worker và mọi việc cần model (viết lại script, sửa script, chẩn đoán) đều do sub-agent của chính host đang chạy (Claude Code, Copilot CLI...) làm. Nút trên dashboard (điều phối, giao lại, tạo lại script) chỉ ghi yêu cầu; `oqa wait` báo cho host, host giao việc cho sub-agent. Tạo lại script generic: `oqa live regenerate CASE --brief` (hoặc `all`) in bản giao cho một sub-agent; sub-agent nộp script bằng `--candidate FILE` và nhận PASS, RETRY (kèm lý do) hoặc FAIL. Công cụ không có lệnh `oqa live` (save_download, upload_csv, open_import, capture_id, goto_fixture...): `oqa tool CASE NAME --args JSON`. Case phụ thuộc chỉ chạy sau khi case tiền đề PASS. Nếu tiền đề kết thúc với kết quả khác, case phụ thuộc hiện Chưa chạy và tự được xét lại khi tiền đề mở lại. Xem [worker](references/agents.md). Khi claim của worker hết hạn, dispatch giao lại attempt đang mở cùng các bước đã ghi. Dùng `oqa live recover ID --reason <chẩn đoán>` khi cần chạy lại từ đầu. Trao đổi bằng `oqa note` / `oqa notes`.
5. Worker: `oqa live start ID`, đọc UI thật, ghi đường đi với `oqa live act --record` và kiểm tra kết quả từng bước bằng `oqa live step`. Xem [cách lưu hành động và assertion](references/replay.md#script-chạy-lại-ngày-khác). Chụp mọi hành động và trạng thái trung gian (mở form, điền trường, nút trước khi bấm, kết quả sau khi bấm) bằng `oqa live shot --caption TEXT`. Khoanh một đích nhìn thấy, duy nhất, liên quan ảnh; kiểm tra ảnh và sửa khung sai. Phần tử vắng mặt thì khoanh view cha nhỏ nhất. Bước testcase nói "xác nhận trên database" / "chụp kết quả query PgAdmin": `oqa pgadmin shot ID --n N --sql "SELECT ..."` TRƯỚC (đây là bước kế tiếp; ảnh PgAdmin thành một phần của bước), rồi ghi bước bằng `oqa live step` (khoanh bản ghi đang hiện trên màn hình, `--actual` nói kết quả truy vấn trả về gì) (chỉ truyền SQL đọc; không tự mở PgAdmin, không hỏi mật khẩu; đọc [evidence](references/evidence.md#database-evidence-pgadmin)). Kết thúc `oqa live end ID --status <status>`.
6. Trước khi báo cáo, ghi vào `oqa note --case CASE` mọi điều trong báo cáo cuối của sub-agent mà lần chạy sau cần biết (thao tác bị từ chối và cách đã xử lý, control hành xử lạ) nếu sub-agent chưa tự ghi: bản tổng kết knowledge chỉ viết từ các ghi chú này. `oqa report` và trả đường dẫn kết quả. Nếu lệnh báo còn nợ tổng kết knowledge: `oqa knowledge digest`, giao bản in ra cho MỘT agent rẻ (haiku); agent soạn nháp bằng `oqa knowledge draft --file`; nhắc người dùng sửa, chọn phạm vi từng mục và Lưu hoặc Bỏ qua ở khung "Tổng kết knowledge" trên dashboard (`oqa wait`), rồi chạy `oqa report` lại để đóng phiên. Kênh path lưu kết quả cạnh file nguồn. Nếu workbook đã đổi trước khi tạo bản sao, trả file evidence và lý do. Chạy add-in: ghi vào workbook đang mở trong Excel desktop (Windows).

Mỗi khi cần người dùng làm gì trên dashboard (duyệt, resume, trả lời, xóa dữ liệu rồi cho chạy tiếp): chạy `oqa wait` ở nền ngay, nói rõ họ cần làm gì và đưa link dashboard. Không bảo họ "nhắn em khi xong": `oqa wait` tự báo khi họ bấm. Hook mở lại tab dashboard nếu không tab nào đang mở.

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

Knowledge có hai phạm vi: `workspace` (chỉ Odoo này, mặc định) và `shared` (đúng với mọi Odoo, dùng chung mọi workspace; `oqa knowledge learn --scope shared`). Khi chạy, AI đọc cả hai, mục riêng workspace được ưu tiên. Người dùng xem, sửa, xóa, chuyển phạm vi ở tab Knowledge của dashboard. Knowledge chỉ lưu qua tổng kết cuối phiên (người dùng duyệt) hoặc khi AI đã xác minh một điều dùng lại được.

Trước khi lưu quy tắc hay cách xử lý đã xác minh, đọc `oqa knowledge search --screen MODEL --kind KIND`. Cùng điều kiện nghiệp vụ thì dùng lại entry cũ; `oqa knowledge learn` bổ sung fact, evidence hoặc nguồn còn thiếu. Lưu quy tắc kèm tham chiếu thiết kế hoặc xác nhận của người dùng; verdict của case hiện tại vẫn ghi trong kết quả của nó.

Public formats: [contract](references/contract.md). Odoo conventions: [Odoo reference](references/odoo.md).
