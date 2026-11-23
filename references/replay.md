# Replay và script lưu

## Live và Replay là hai kết quả riêng
Live PASS/FAIL là lần chạy tạo ra evidence. Replay PASS/FAIL/CANCELLED/NOT RUN (`oqa live replay CASE|all`) hiển thị riêng trên dashboard và trong Excel. Replay có cửa sổ hiển thị bước hiện tại và tự đóng khi xong. Đóng cửa sổ đó là hủy replay và dừng các case còn lại của lượt. Chế độ nhanh (`--fast`, hoặc công tắc "Chạy nhanh" trên dashboard) đặt cả hai khoảng dừng xem về 0.

## Tốc độ
Chỉnh trên dashboard hoặc `oqa live replay ID --action-delay-ms 1000 --step-delay-ms 2000`. Mặc định 700 ms sau mỗi hành động và 1500 ms sau mỗi bước kiểm tra; 0 là tắt khoảng dừng đó. Không đưa các khoảng dừng xem này vào code hành động đã lưu. `timeout_s` vẫn là thời gian chờ tối đa cho một điều kiện của ứng dụng, không điều khiển tốc độ xem. `run start` lặp lại dùng lại phiên duyệt đang chờ khớp; chỉ dùng `--new-session` cho một lần chạy riêng.

## Script chạy lại ngày khác
Mỗi case khai báo `replay_records`: danh sách `{record, how, detail}` cho mọi bản ghi case tạo hoặc dùng, với `how` là `run_key` (khóa riêng của lần chạy từ `oqa fixture key`), `captured` (id lưu vào biến bằng `capture_id`, `detail` là tên biến, dùng `$OQA_ID(tên)`) hoặc `existing_ref` (`detail` là text của một tham chiếu đã khai báo). Case không dùng bản ghi nào thì ghi lý do vào `replay_records_none_reason`. Trình kiểm tra chỉ xét khai báo này, không đọc câu chữ của bước; script có sống qua lần chạy sau hay không do lần replay kiểm chứng.

Để ghép script tự động, mỗi bước thêm `--verified-code <file> --shows <chữ nhìn thấy mong đợi>`. Chỉ đưa hành động thuộc đường đi cuối cùng, dùng chờ quan sát được.
