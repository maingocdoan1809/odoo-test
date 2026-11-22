# Prompt: thiết kế lại Dashboard "Odoo Test" (bản tự do sáng tạo)

Bạn là một product designer giỏi, đồng thời viết được front-end. Hãy thiết kế lại giao diện Dashboard dưới đây. Giao diện hiện tại bị chê là xấu và rối. Tôi **không** áp sẵn bố cục: hãy tự quyết định cách trình bày, miễn giữ đúng các điều "Bắt buộc". Mọi thứ ở mục "Tự do" là của bạn, hãy táo bạo và có chủ kiến.

## Sản phẩm

**Odoo Test** là skill cho AI agent tự động test Odoo từ file testcase Excel. AI phân tích file, lập chiến lược, **chờ người dùng duyệt**, rồi chạy từng case trên trình duyệt thật (mỗi bước chụp ảnh có khung đỏ), tự sửa khi lỗi, cuối cùng xuất Excel bằng chứng. Mỗi lần test một file = một **session**; nhiều session thuộc một **workspace** (một hệ thống Odoo).

**Dashboard** là trang web localhost mà người dùng (QA/BA/dev Odoo) mở cạnh cửa sổ chat với AI. Nó dùng để: theo dõi AI đang test (cập nhật trực tiếp ~1,5 giây), xem kết quả từng case/step/ảnh, **trả lời khi AI cần người**, chạy lại script, quay video, xuất báo cáo. Giao diện tiếng Việt.

## Bắt buộc (không thương lượng)

**Flow:**
1. Màn Dashboard hiển thị **danh sách session**.
2. Bấm một session → màn chi tiết: **danh sách testcase bên trái**, **danh sách step bên phải** của case đang chọn. Vào là **tự chọn case đầu tiên**.
3. Có đường quay lại danh sách session. Vẫn giữ 2 tab Scripts và site_map (chỉ cần đồng bộ style).

**Thông tin phải có (không được bỏ, cách bày tuỳ bạn):**
- *Mỗi session:* tên file, workspace, thời gian, giai đoạn (Phân tích → Kết nối → Xác nhận → Thực thi → Báo cáo → Xong), tiến độ theo trạng thái case, số PASS/FAIL/tổng, worker đang chạy (vd 3/5), dấu hiệu "đang chờ bạn" / "bỏ dở" / "đã dừng", xoá session.
- *Đầu session chi tiết:* tên file, giai đoạn, "AI đang làm gì", phiên bản Odoo, tiến độ + số lượng theo trạng thái, tổng Replay PASS/FAIL/NOT RUN, hành động cấp session: Dừng/Tiếp tục test, Xuất & tải báo cáo Excel, tải ZIP script, Chạy lại tất cả / Quay video tất cả.
- *Mỗi case trong danh sách trái:* mã, tiêu đề, trạng thái Live, tiến độ step (x/y), số lần chạy, trạng thái Replay, tag Need confirm / Đã bỏ qua / Ready, agent đang giữ case.
- *Chi tiết case (phải):* trạng thái + phân loại lỗi + lý do; Replay (kết quả, thời lượng, lý do FAIL); hành động: Chạy lại, Quay video, Xem video, Chạy nhanh + thời gian nghỉ, Yêu cầu điều phối/giao lại; Profile, Tiếp từ case khác, điểm Rõ ràng/Đủ dữ kiện, dòng Excel; các lần chạy (tab lần 1/2/3); **mỗi step**: số thứ tự, thao tác, trạng thái, Mong đợi, Ảnh cần chụp, Thực tế, số kiểm tra, "giữ từ lần trước", ảnh bằng chứng (bấm phóng to: ảnh khoanh đỏ ↔ ảnh gốc, ←/→, Esc); Testcase gốc (điều kiện, các bước, kết quả mong đợi); Phân tích của AI (tình huống biên, dữ liệu thiếu→dự phòng, câu hỏi, thời gian chờ, ghi chú sửa lỗi); Lịch sử kết luận.
- *Đang chạy trực tiếp:* các case đang chạy, case chưa có agent nhận, feed hoạt động của mọi agent, ghi chú chung.

**Mọi yêu cầu AI cần người phải thấy ngay, không phải tìm.** Gồm: (1) AI đặt câu hỏi, người trả lời (có thể dán đường dẫn tệp); (2) chiến lược chờ duyệt → nút Xác nhận và bắt đầu test; (3) case Need confirm: trả lời hoặc bỏ qua; (4) case FAIL/hết lượt sửa: nhập gợi ý để AI chạy lại; (5) đã dừng → Tiếp tục; (6) worker lỗi; (7) thêm tệp tham chiếu cho AI. Vị trí, hình thức, mức nổi bật: bạn quyết, nhưng người dùng không được bỏ sót một yêu cầu nào, và nội dung đang gõ không được mất khi dữ liệu tự cập nhật.

**Theme:** trung tính, chuẩn ứng dụng test/QA chuyên nghiệp (tham khảo tinh thần Playwright UI, Linear, GitHub Actions, Allure). Màu đỏ/xanh/vàng chỉ dùng cho trạng thái, và trạng thái phải có icon/chữ chứ không chỉ màu. Có light + dark theo hệ thống.

**Kỹ thuật:** một file HTML thuần (không framework, CDN, font ngoài) thay `oqa/web/dashboard.html`; giữ nguyên placeholder `__TOKEN__`, `__BOOT__`, `/*__FONT_FACES__*/`, `/*__SITE_MAP_CSS__*/`, `/*__SITE_MAP__*/`; không đổi API và cấu trúc dữ liệu; chỉ vẽ lại khi dữ liệu đổi, giữ vị trí cuộn; dùng được trên màn hình rộng lẫn 375px; bàn phím + aria đầy đủ; escape chuỗi động.

## Tự do sáng tạo (bạn quyết)

- Bố cục: danh sách session dạng bảng, thẻ, timeline hay kanban; độ rộng, tỉ lệ hai cột, cột trái có thu gọn không; đầu session cố định hay cuộn; "AI cần bạn" là banner, thanh dưới, drawer, hộp thoại hay "inbox" riêng.
- Cách thể hiện step: timeline, thẻ, bảng; ảnh nhỏ hay lớn, xem cạnh nhau, so sánh Mong đợi ↔ Thực tế.
- Cách trình bày tiến độ và trạng thái: thanh, vòng, ô vuông, biểu đồ nhỏ; icon set tự vẽ bằng SVG.
- Bảng màu cụ thể, kiểu chữ, bo góc, khoảng cách, độ dày thông tin (gọn hay thoáng), micro-interaction, phím tắt, tìm kiếm/lọc nhanh, trạng thái rỗng, loading, lỗi.
- Bất kỳ ý tưởng nào giúp người dùng **nhìn một lần biết tình hình** và **xử lý yêu cầu của AI nhanh nhất**, kể cả thứ tôi chưa nghĩ tới. Nếu thấy một yêu cầu ở trên đang cản trở trải nghiệm, hãy nêu rõ lý do và đề xuất thay thế.

## Cần bạn giao

1. **Hai hoặc ba hướng thiết kế khác nhau rõ rệt** (mỗi hướng một mock-up HTML với dữ liệu giả), mỗi hướng nói ngắn gọn tư tưởng của nó. Ít nhất Màn 1 và Màn 2 ở trạng thái đang chạy có yêu cầu AI chờ bạn.
2. **Khuyến nghị một hướng**, nêu lý do ngắn gọn.
3. Sau khi tôi chọn hướng: hoàn thiện đủ trạng thái (rỗng, đang chạy, chờ xác nhận, Need confirm, FAIL cần gợi ý, đã dừng, đã xong, lightbox, dark, mobile) rồi chuyển thành `dashboard.html` chạy thật với API hiện có.
