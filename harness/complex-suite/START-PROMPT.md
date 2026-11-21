Dùng skill `odoo-test` để chạy workbook testcase sau trên Odoo harness (module `oqa_harness`, DB `oqa_harness`, http://127.0.0.1:8079).

- Workbook: `C:\Users\maido\odoo-test\harness\complex-suite\odoo-harness-complex-10.xlsx` (sheet Testcases, 10 case T01–T10)
- Thư mục tham chiếu và file CSV đầu vào: `C:\Users\maido\odoo-test\harness\complex-suite\fixtures` (đặt làm reference dir của workspace)
- Workspace: `oqa_harness_20` (profile Manager và Operator). Nếu workspace hoặc đăng nhập không dùng được thì dừng lại và báo tôi, đừng tự tạo tài khoản.
- Trong file CSV, `{run}`, `{site}`, `{site2}`, `{existing}` là chỗ giữ chỗ để bạn thay bằng giá trị riêng của lần chạy (có tiền tố test của workspace).

Làm đúng quy trình của skill, từ run start đến report. Tôi sẽ review từng case và duyệt strategy trên dashboard, nên hãy dừng chờ tôi ở các bước skill yêu cầu, và nêu rõ câu hỏi nếu có case chưa đủ rõ để chạy. Không đọc, không mở thư mục `C:\Users\maido\oqa-oracle`. Không sửa mã của skill hoặc module harness trong lúc chạy. Báo cáo trung thực: case nào FAIL, BLOCKED hay bị bỏ qua thì ghi đúng như quan sát được, kèm lý do, đừng làm cho kết quả khớp với kỳ vọng ghi trong workbook.
