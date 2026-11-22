# Đánh giá và đề xuất: bộ công cụ giám sát và giao phó cho AI (skill `oqa`)

Ngày: 2026-11-20. Phạm vi: đọc mã nguồn trong thư mục này (`oqa/state.py`, `oqa/dashboard_server.py`, `oqa/web/dashboard.html`, `oqa/claims.py`, `oqa/dispatch.py`, `oqa/live.py`, `hooks/`, `references/`, `ODOO-TEST-PLAN.md`, `harness/`).
Giới hạn: đây là đánh giá bằng cách đọc code, chưa chạy thử từng chức năng. Mục nào ghi "chưa thấy" nghĩa là em tìm trong code và không có, cần kiểm lại bằng cách chạy trước khi làm.

## 1. Mục tiêu

Một bộ công cụ mà user chọn được mức tin tưởng AI theo từng việc:

- Giao hẳn cho AI, chỉ xem kết quả cuối.
- Để AI chạy, user nhìn và chen vào khi cần.
- Bắt AI xin phép từng bước rủi ro.

Cùng một công cụ, user chỉnh mức này bằng cấu hình, không phải đổi cách làm việc.

## 2. Hiện trạng: đã có gì

| Nhóm | Đã có | Nơi nằm |
|---|---|---|
| Duyệt trước khi chạy | Duyệt phạm vi, gắn với mã băm của kế hoạch. Kế hoạch đổi thì phải duyệt lại | `state.confirm_current`, `/api/confirm` |
| Hỏi đáp (human in the loop) | AI hỏi, user trả lời, gửi tài liệu, bỏ qua từng case | `ask`, `review_case`, `/api/answer`, `/api/review`, `/api/refs` |
| Điều khiển khi chạy | Dừng và chạy tiếp cả phiên. Gợi ý cho một case, case đó quay lại hàng đợi với ngân sách thử lại mới | `stop`, `resume`, `hint` |
| Bàn giao giữa AI | Nhận case theo "hợp đồng thuê" 600 giây, tối đa 5 worker, chuyển case giữa worker không mất bước | `claims.py`, `live handover` |
| Chống đi tắt | Hook chặn sửa file trạng thái bằng tay, đọc cookie, tự duyệt, tự chạy script. Hook dừng chặn AI kết thúc sớm | `hooks/guard.py`, `hooks/stop.py` |
| Bằng chứng | Ảnh có khung, video có chú thích bước, Live và Replay tách riêng, lịch sử kết luận cũ không bị xoá | `events.jsonl`, `archive_result` |
| Hồi quy nhiều môi trường | Chạy lại script trên main, staging, production, bảng ma trận | `regression.py`, tab Scripts |
| Kiểm chứng bộ công cụ | Harness Odoo 19, 20 case, người dùng giả lập | `harness/` |

Điểm mạnh nhất: kỷ luật quy trình. Mọi lệnh kiểm tra phase của phiên, và những việc chỉ user được làm (duyệt, dừng, chạy tiếp) bị hook chặn không cho AI làm thay.

## 3. Khoảng trống

Mức ưu tiên: P1 làm trước vì ảnh hưởng an toàn hoặc độ tin cậy kết quả. P2 làm tiếp theo. P3 để sau.

| # | Khoảng trống | Hậu quả hiện tại | Ưu tiên |
|---|---|---|---|
| G1 | Không có mức tự chủ. Chỉ có một cổng duyệt ở đầu, sau đó AI tự làm mọi thứ | Muốn kiểm soát chặt thì phải dừng cả phiên | P1 |
| G2 | Chưa thấy cổng xin phép cho thao tác rủi ro (ghi dữ liệu, `fixture cleanup --apply`, chạy trên môi trường thật) | Một lần duyệt đầu phiên phải bao cả những việc user chưa lường | P1 |
| G3 | Chưa thấy chặn theo môi trường. `regress --env production` chạy như staging | Script có ghi dữ liệu có thể chạy nhầm lên production | P1 |
| G4 | User không sửa được kết luận của một case (đổi PASS/FAIL kèm lý do) | AI kết luận sai thì user chỉ có cách gợi ý và chạy lại | P1 |
| G5 | Dừng, chạy lại chỉ ở mức cả phiên. `hint` chỉ dùng cho case đã chạy xong | Không tạm dừng một case lỗi mà vẫn để các case khác chạy | P2 |
| G6 | Chưa thấy xem trực tiếp trình duyệt đang chạy. Chỉ có ảnh và video sau khi chạy | Không thấy AI đang làm gì lúc nó làm | P2 |
| G7 | Chưa thấy báo cho user khi cần họ (AI hỏi, case lỗi, chạy xong) | User phải mở dashboard canh | P2 |
| G8 | Chưa thấy ngân sách: thời gian tổng, số lần thử lại tổng, số lần gọi mô hình. Chỉ có `MAX_REPAIRS` và `timeout_s` từng case | Phiên có thể chạy lâu hoặc tốn mà không có hạn mức | P2 |
| G9 | Nhật ký chỉ ghi `by` ở vài sự kiện, không có "đã đổi gì, từ giá trị nào sang giá trị nào" | Khó làm hồ sơ kiểm toán | P2 |
| G10 | Phân quyền một người: ai mở được dashboard thì làm được mọi việc | Chưa tách người xem và người duyệt | P3 |
| G11 | Điều phối dựa trên lời nhắc (prompt) và hook. Worker Claude dùng `dontAsk` và chỉ cho phép công cụ `mcp__oqa` | Tốt, nhưng tầng này chưa có "danh sách việc được phép" khai báo được | P3 |

## 4. Đề xuất thiết kế

### 4.1 Mức tự chủ (giải quyết G1, G2, G3)

Một giá trị `autonomy` trong `workspace.json`, ghi đè được cho từng môi trường và từng case:

| Mức | Tên | AI được làm tự động | AI phải xin phép |
|---|---|---|---|
| A0 | Chỉ xem | Đọc, phân tích, lập kế hoạch | Mọi thao tác trên Odoo |
| A1 | Xin phép từng bước ghi | Đọc, mở màn hình, chụp ảnh | Mọi thao tác ghi: lưu, xoá, đổi trạng thái, tạo fixture, import |
| A2 | Mặc định | Mọi thao tác trên bản ghi do chính lượt này tạo (`ZZ_AUTOTEST_*`) | Thao tác trên bản ghi có sẵn, `fixture cleanup --apply`, đổi profile |
| A3 | Giao hẳn | Tất cả trong phạm vi đã duyệt | Chỉ việc nằm ngoài phạm vi đã duyệt |

Mặc định theo môi trường: `main` và `staging` dùng A2, `production` dùng A1 và cấm script có ghi dữ liệu (chỉ chạy case thuần xem).

Cách làm: mỗi lệnh ghi của worker đi qua một hàm `gate(action, case, risk)` trong `state.py`. Hàm này đọc mức tự chủ, nếu cần xin phép thì ghi sự kiện `approval_requested` và chờ `approval_granted` hoặc `approval_denied` từ dashboard. Phát hiện "bản ghi do lượt này tạo" dựa vào sự kiện `data_created` đã có.

### 4.2 Điều khiển theo case (G4, G5)

Thêm các lệnh và nút trên dashboard:

- `oqa case pause|resume|skip|rerun CASE`: tạm dừng, chạy tiếp, bỏ qua, chạy lại từng case, không ảnh hưởng case khác. Kế thừa cơ chế `stopped` hiện có nhưng đặt theo case.
- `oqa case override CASE --status PASS|FAIL --reason TEXT`: user đổi kết luận. Kết luận cũ đi vào `history` bằng `archive_result` (đã có), thêm `by=user` và lý do bắt buộc. Báo cáo Excel hiện nhãn "đã chỉnh bởi người".
- Cho `hint` dùng được cả khi case đang chạy, không chỉ sau khi xong.

### 4.3 Nhìn thấy và được báo (G6, G7)

- Xem trực tiếp: dashboard phát luồng ảnh chụp màn hình định kỳ (mỗi 1–2 giây) của trình duyệt case đang chạy, kèm nút "chen vào" để tạm dừng case đó.
- Thông báo: cấu hình `notify` gồm webhook hoặc lệnh. Gửi khi: AI hỏi, cần xin phép, case FAIL/BLOCKED, ngân sách gần hết, phiên xong. Nội dung ngắn, có đường dẫn mở thẳng case.

### 4.4 Ngân sách (G8)

Cấu hình `budget`: thời gian tối đa cả phiên, thời gian tối đa mỗi case, tổng số lần phục hồi, số worker tối đa (hiện cố định 5 trong `claims.MAX_WORKERS`). Chạm 80% thì báo, chạm 100% thì tự dừng như `stop` và ghi lý do.

### 4.5 Kiểm toán (G9, G10)

- Mọi sự kiện do người tạo đều có `by`, `at`, `before`, `after`. Thêm lệnh `oqa audit` xuất danh sách "ai đã duyệt, đổi, dừng, ghi đè gì".
- Vai trò: `viewer` (chỉ xem), `operator` (trả lời, gợi ý, dừng), `approver` (duyệt, ghi đè, cho phép rủi ro). Mỗi vai trò dùng một token riêng khi mở dashboard.

### 4.6 Khai báo việc được phép cho worker (G11)

File `policy.json` trong workspace: danh sách hành động, màn hình, mô hình được phép hoặc bị cấm. `worker_mcp.py` đọc file này và từ chối công cụ nằm ngoài. Giúp giao phó có kiểm soát mà không dựa vào lời nhắc.

## 5. Lộ trình

| Giai đoạn | Nội dung | Công | Điều kiện xong |
|---|---|---|---|
| 1 | G3 chặn môi trường, 4.1 mức tự chủ và cổng xin phép, 4.2 `override` | khoảng 3–4 ngày | Test: case ghi dữ liệu trên `production` bị chặn. A1 dừng chờ duyệt rồi chạy tiếp sau khi bấm cho phép |
| 2 | 4.2 điều khiển từng case, 4.4 ngân sách | khoảng 2–3 ngày | Tạm dừng một case, 4 case còn lại vẫn chạy. Chạm ngân sách thì phiên tự dừng |
| 3 | 4.3 xem trực tiếp và thông báo | khoảng 3 ngày | Nhận thông báo khi AI hỏi. Thấy ảnh trực tiếp trễ dưới 3 giây |
| 4 | 4.5 kiểm toán và vai trò, 4.6 `policy.json` | khoảng 3 ngày | `oqa audit` liệt kê đủ. Token `viewer` không bấm được nút duyệt |

Công ước lượng sơ bộ, chưa tính viết test. Nên mở rộng `harness/` thêm case cho từng mức tự chủ trước khi làm, để có chỗ nghiệm thu.

## 6. Rủi ro khi triển khai

- Cổng xin phép làm chậm lượt chạy. Giảm bằng cách cho duyệt theo nhóm ("cho phép mọi thao tác trên bản ghi `ZZ_AUTOTEST_*` của case này").
- Hook cũ và hook của plugin cùng chạy hai lần (đã ghi trong `CLAUDE.md`). Hàm `gate` phải chạy lặp lại được mà không xin phép hai lần.
- Thêm sự kiện mới thì `events.py`, báo cáo `report.py` và `view.py` đều phải biết. Cần thêm test `tests/test_dashboard*.py` cho từng sự kiện.
- Thay đổi skill phải làm trong thư mục này, chạy test, rồi mới `python3 oqa.py publish`.

## 7. Nhờ xác nhận

1. Mức mặc định cho `production` là A1 và cấm ghi dữ liệu, có đúng ý chưa.
2. Có cần phân vai (G10) ngay, hay một người dùng là đủ ở giai đoạn đầu.
3. Cho phép bắt đầu giai đoạn 1 theo bảng ở mục 5 chứ.
