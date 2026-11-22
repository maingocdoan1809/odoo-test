# Prompt: audit toàn bộ chức năng Dashboard "Odoo Test"

Bạn là QA engineer kỳ cựu. Hãy **audit toàn bộ chức năng, giao diện và độ ổn định** của Dashboard "Odoo Test" (trang web localhost của skill `oqa`) sau đợt thiết kế lại. Mục tiêu: tìm lỗi, chỗ lệch so với thiết kế, chỗ khó dùng. **Chỉ kiểm tra và báo cáo, không sửa code.**

## 0. Quy tắc an toàn

- Không xoá phiên test thật, không bấm "Chạy lại"/"Quay video"/"Chạy tất cả" trên Odoo thật khi chưa được phép. Các hành động ghi (xác nhận chiến lược, trả lời, gợi ý, dừng/tiếp tục, xoá phiên, thêm tệp tham chiếu, yêu cầu điều phối) chỉ làm trên **workspace/phiên thử** bạn tự tạo hoặc bản sao dữ liệu. Muốn chạm dữ liệu thật phải hỏi người dùng trước.
- Đường dẫn dữ liệu: `~/.odoo-test/workspaces/<ws>/sessions/<id>`. Tài liệu/mã tham chiếu: `oqa/web/dashboard.html`, `oqa/dashboard_server.py`, `oqa/view.py`, `oqa/dispatch_requests.py`, tests ở `tests/test_dashboard*.py`.
- Mọi phát hiện phải có **bằng chứng**: ảnh chụp, đoạn console/network, hoặc giá trị đo được (không đoán).

## 1. Cách chạy

```powershell
python oqa.py dashboard --no-open            # in ra URL kèm token, ví dụ http://127.0.0.1:PORT/?t=TOKEN
python oqa.py dashboard --no-open --ws <ws> --session <id>   # mở thẳng một phiên
```
Dùng trình duyệt thật (Chrome/Playwright). Mở DevTools: theo dõi Console (lỗi, cảnh báo) và Network (lỗi 4xx/5xx, request thừa) suốt quá trình. Server tự tắt sau 4 giờ.

Để có đủ trạng thái, tạo phiên thử bằng fixture của test (xem `tests/conftest.py`, `tests/test_flow.py::executing`, `tests/test_dashboard_data.py`) hoặc chạy `oqa` thật trên workspace thử: ra được phiên ở từng giai đoạn ANALYZE / CONNECT / CONFIRM / EXECUTE / REPORT / DONE, có AI đang hỏi (`session.ask(...)`), có case Need confirm, case FAIL hết lượt sửa, đã dừng, bỏ dở (>6 giờ không hoạt động), worker lỗi, có replay, có video.

## 2. Tổng quan sản phẩm (để biết "đúng" là gì)

Dashboard có 3 tab trên cùng: **Danh sách phiên**, **Scripts**, **Sơ đồ màn hình** (site_map).

**Flow chính:** Danh sách phiên → bấm một phiên → màn chi tiết: trái là danh sách testcase (tự chọn case đầu tiên), phải là chi tiết case với danh sách step. Bấm tab "Danh sách phiên" khi đang trong một phiên thì quay về danh sách. Mở thẳng bằng `#workspace/phiên` hoặc `--session`.

**Màn chi tiết phiên** gồm: header (tên file, giai đoạn, vòng tròn % đạt, 4 ô số PASS/FAIL/Đang chạy/Chưa có kết luận bấm để lọc, timeline 6 giai đoạn có animation, các menu Hoạt động / Chạy lại / Báo cáo & tệp, nút Dừng test), khung **"Cần bạn xử lý"** (mọi yêu cầu AI đang chờ người), cột testcase (tìm kiếm, lọc, thu gọn), cột chi tiết case.

Cả trang cuộn như một trang; cột testcase dính bên trái khi cuộn (màn rộng); màn hẹp (<860px) xếp dọc.

## 3. Danh mục kiểm thử

Với mỗi mục: thực hiện, so với kết quả mong đợi, ghi PASS/FAIL/N/A kèm bằng chứng.

### A. Tải trang, điều hướng, bảo mật
- A1 Mở URL có token: không trắng trang, không kẹt "đang tải…", không lỗi console. Mở URL sai/thiếu token: bị từ chối (403), không lộ dữ liệu.
- A2 Không có phiên nào: trạng thái rỗng có hướng dẫn.
- A3 Deep link `#ws/id` và `--session` vào thẳng phiên; id không tồn tại thì xử lý êm (không kẹt "Đang tải phiên…" mãi).
- A4 Tab "Danh sách phiên" ↔ Scripts ↔ Sơ đồ màn hình: chuyển được bằng chuột và bàn phím (←/→/Home/End), `aria-selected` đúng, URL cập nhật (`?tab=site_map`).
- A5 Rời sang Scripts/site_map rồi quay lại tab phiên: vẫn đúng phiên và case đã chọn, nội dung đang gõ không mất.
- A6 Chỉ báo "Trực tiếp · cập nhật hh:mm:ss": chấm xanh nhấp nháy khi phiên chưa xong, "Đã xong · …" khi DONE; ở màn danh sách hiện số phiên.
- A7 Bảo mật API: thử path traversal ở `/api/image`, `/api/report`, `/api/video`, `/api/script` (`..`, đường dẫn tuyệt đối, đuôi tệp sai) phải trả 404; POST không token bị từ chối.

### B. Danh sách phiên
- B1 Hiển thị đúng mỗi phiên: tên file, workspace, thời gian, giai đoạn, thanh tiến độ, PASS/FAIL/đang chạy/tổng, số worker.
- B2 Nhóm đúng: "Cần bạn xử lý" (AI hỏi / chờ duyệt chiến lược / có case cần xác nhận / đã dừng), "Đang chạy", "Các phiên khác". Phiên bỏ dở có nhãn "Bỏ dở?".
- B3 Lọc workspace, ô tìm theo tên file (gõ liên tục không mất focus), 4 nút lọc Tất cả / Cần bạn / Đang chạy / Đã xong; kết hợp nhiều bộ lọc; trạng thái "không có phiên khớp".
- B4 Xoá phiên: có hộp xác nhận; không cho xoá phiên đang chạy (báo lỗi rõ); xoá xong danh sách cập nhật, nếu đang mở phiên đó thì về danh sách. (Chỉ làm trên phiên thử.)
- B5 Danh sách tự làm mới (~4 giây) khi có phiên mới/đổi trạng thái, không giật, không mất chữ đang gõ trong ô tìm kiếm, không cuộn về đầu.
- B6 Tên file/workspace dài hoặc có ký tự đặc biệt (`<script>`, dấu tiếng Việt, `&`): không vỡ layout, không chạy mã (XSS).

### C. Header phiên
- C1 Tên file, badge giai đoạn (hoặc "Đã dừng"), workspace, thời gian bắt đầu, phiên bản Odoo, mã phiên, dòng "AI đang làm gì" (có chấm nhấp nháy khi đang chạy).
- C2 Vòng tròn: % đạt = PASS/tổng; các đoạn xanh/đỏ/xanh dương khớp số liệu; tổng = 0 không lỗi chia 0.
- C3 Bốn ô số khớp `counts`; bấm ô lọc danh sách testcase đúng (PASS, FAIL, Đang chạy, Chưa có kết luận), bấm lại để bỏ lọc; ô đang lọc có trạng thái nhấn; nếu cột case đang thu gọn thì tự mở.
- C4 Timeline: đúng giai đoạn hiện tại/đã xong/chưa tới, có giờ; animation vẽ một lần khi mở phiên, không lặp lại mỗi lần làm mới; vòng sáng ở giai đoạn hiện tại; vệt chạy chỉ khi phiên đang chạy, tắt khi dừng/xong; tắt hết khi bật `prefers-reduced-motion`.
- C5 Menu "Hoạt động": mở ngăn trượt bên phải (đang chạy, case chưa có agent nhận, feed hoạt động, ghi chú chung), đóng bằng nút và Esc.
- C6 Menu "Chạy lại" (chỉ giai đoạn Báo cáo/Xong và có script): Chạy lại tất cả, Quay video tất cả, tổng Replay.
- C7 Menu "Báo cáo & tệp": Xuất/Cập nhật báo cáo Excel (tự tải), Tải file Evidence, Tải ZIP script, thêm tệp tham chiếu (đường dẫn không tồn tại báo lỗi rõ; giai đoạn DONE ẩn mục này). Menu tự đóng khi bấm ra ngoài, giữ trạng thái mở khi dữ liệu làm mới.
- C8 Nút "Dừng test" chỉ hiện khi đang Thực thi; dừng xong hiện khung "Bạn đã dừng test" và nút "Tiếp tục test"; dừng hai lần báo lỗi hợp lý.
- C9 Nút/ô trong header không bị cắt chữ ở 320, 375, 768, 1024, 1280, 1920px.

### D. Khung "Cần bạn xử lý" (quan trọng nhất)
Với mỗi loại yêu cầu: có hiện ngay trong tầm mắt không, có đủ thông tin, nút gửi hoạt động, lỗi hiện đúng chỗ, hết yêu cầu thì khung biến mất:
- D1 AI hỏi người dùng: câu hỏi nhiều dòng, ô trả lời, "Gửi trả lời"; gửi trống bị chặn.
- D2 Chiến lược chờ duyệt: xem được nội dung, "Xác nhận và bắt đầu test" chuyển sang Thực thi; chiến lược bị sửa sau khi tạo thì bị từ chối.
- D3 Rà soát testcase: đếm đúng số case Need confirm, bấm tên case nhảy tới case đó (cột trái cuộn tới case); trả lời/bỏ qua trong chi tiết case.
- D4 AI cần gợi ý (case hết lượt sửa): liệt kê case, bấm nhảy tới.
- D5 Worker lỗi: liệt kê case, nội dung lỗi nằm ở chi tiết case, ghi rõ chưa phải kết luận.
- D6 Phiên bỏ dở: thông báo số giờ và cách tiếp tục (mức độ nhẹ hơn các yêu cầu khác).
- D7 **Giữ nội dung đang gõ**: gõ dở một câu trả lời, để dữ liệu tự làm mới nhiều lần (đổi dữ liệu phía server): nội dung, con trỏ, vị trí cuộn không mất; chuyển case rồi quay lại vẫn còn bản nháp.
- D8 Nhiều yêu cầu cùng lúc: bố cục không vỡ, thứ tự hợp lý, khung không che mất danh sách case ở màn thấp.

### E. Danh sách testcase (cột trái)
- E1 Vào phiên **tự chọn case đầu tiên**; case được chọn có làm nổi bật; không bao giờ để cột phải trống khi phiên có case.
- E2 Mỗi dòng: mã, tiêu đề (xuống dòng đủ, không cắt), icon + chữ trạng thái, dải ô tiến độ step + "x/y", số lần chạy, agent đang giữ case, tag (Need confirm / Cần gợi ý / Worker lỗi / Đã bỏ qua / Ready / Replay …).
- E3 Tìm kiếm (theo mã và tiêu đề) và lọc trạng thái (Tất cả, Cần bạn, FAIL, Đang chạy, PASS, Chưa xong); đếm "x/tổng" khi đang lọc; không có kết quả có thông báo.
- E4 Bàn phím: Tab vào danh sách, ↑/↓ đổi case, Enter chọn; focus không bị mất sau khi làm mới.
- E5 Nút thu gọn « / » ghi nhớ qua lần tải lại (localStorage); khi thu gọn ẩn đúng phần cần ẩn.
- E6 40, 200, 500 case: cuộn mượt, không giật khi làm mới 1,5 giây/lần, bộ nhớ không phình.
- E7 Cột dính khi cuộn trang (≥861px), cuộn nội bộ khi dài hơn màn hình; xếp dọc dưới 860px.

### F. Chi tiết case (cột phải)
- F1 Header: mã + tiêu đề, trạng thái Live, phân loại lỗi, lý do, Replay (kết quả, thời lượng, lý do FAIL), dòng meta (Profile, Tiếp từ, Rõ ràng, Đủ dữ kiện, Dòng).
- F2 Nút "Chạy lại testcase" / "Quay video" chỉ khi case PASS/FAIL; vô hiệu khi chưa có script; thông báo kết quả khi bấm; "Xem video" khi có video.
- F3 "Tuỳ chọn chạy lại": Chạy nhanh đặt hai khoảng nghỉ về 0 và khoá ô nhập; giá trị hợp lệ 0–60000 ms, giá trị sai bị trả về giá trị cũ; lưu localStorage, giữ sau khi tải lại; request `/api/replay` gửi đúng giá trị.
- F4 Yêu cầu điều phối/giao lại: bấm đúp chỉ tạo một yêu cầu; các trạng thái pending / waiting (kèm lý do) / accepted (tên worker) / rejected; hết slot worker thì vô hiệu và nói rõ.
- F5 Need confirm: câu hỏi, ô trả lời, "Gửi trả lời", "Bỏ qua testcase" (kèm lý do); xong thì khung biến mất.
- F6 Gợi ý cho AI: hiện các gợi ý đã gửi, "Gửi và chạy lại"; tiêu đề khác khi hết lượt sửa.
- F7 Tab các lần chạy (khi >1 lần): đổi lần chạy đổi danh sách step; "từ bước n" đúng.
- F8 Mỗi step: số/biểu tượng trạng thái, thao tác, Mong đợi (+ số kiểm tra, "giữ từ lần trước"), Ảnh cần chụp, Thực tế (nổi bật khi FAIL), dòng "đang chạy…/đang thử…" cho step đang chạy, ảnh chụp.
- F9 Lightbox: bấm ảnh mở đúng ảnh; phím R đổi ảnh khoanh đỏ/ảnh gốc; ←/→ chuyển ảnh (vòng); Esc đóng; focus quay về đúng chỗ; ảnh lỗi/không tải được không làm vỡ.
- F10 Phân tích của AI: tình huống biên, dữ liệu thiếu → dự phòng, câu hỏi, thời gian chờ, ghi chú sửa lỗi (nguyên nhân/căn cứ/đã sửa/hiểu lại/thời gian chờ/script giữ nguyên); tự mở khi có sửa lỗi. Lịch sử kết luận; Testcase gốc (điều kiện, các bước, mong đợi).
- F11 Chuyển qua lại giữa các case nhanh: không nhấp nháy, không hiện dữ liệu của case trước, cuộn cột phải về đầu.

### G. Hành vi trực tiếp và độ ổn định
- G1 Làm mới 1,5 giây: **không vẽ lại khi dữ liệu không đổi** (đánh dấu một phần tử, đợi vài chu kỳ, dấu vẫn còn).
- G2 Khi có dữ liệu mới: giữ vị trí cuộn của trang, cột case, cột chi tiết, khối chiến lược; không nhảy.
- G3 Đang bôi đen văn bản hoặc đang gõ: trang không bị vẽ lại; thả ra thì cập nhật.
- G4 Case chuyển trạng thái trực tiếp (PLANNED → EXPLORING → RUNNING → PASS/FAIL): icon, tiến độ, vòng tròn, ô số, timeline cập nhật nhất quán, không có hai nơi hiển thị số khác nhau.
- G5 Mất kết nối server giữa chừng (tắt `oqa dashboard`): trang xử lý êm, báo rõ, không treo, không spam lỗi console; bật lại thì tự phục hồi.
- G6 Mở hai tab cùng lúc / hai phiên khác nhau: không ghi đè trạng thái của nhau.
- G7 Để trang mở 30 phút: không rò bộ nhớ, số request ổn định.

### H. Tab Scripts
- H1 Bảng script × môi trường; chọn workspace; kết quả mỗi ô (PASS/FAIL/NOT RUN, thời điểm, video, lý do FAIL rút gọn).
- H2 Bấm tên case mở đúng phiên và case ở tab phiên.
- H3 Nút "Chạy tất cả trên <env>", "Quay video tất cả", "▶ <env>" từng case: gửi đúng tham số, thông báo, vô hiệu trong lúc chờ; tự làm mới ~5 giây khi đang ở tab. (Chỉ chạy thật khi được phép.)
- H4 Workspace chưa có script: thông báo rõ.

### I. Tab Sơ đồ màn hình (site_map)
- I1 Hiển thị nhãn tiếng Việt đúng; chọn workspace, ngôn ngữ (en/ja), lọc loại, tìm kiếm.
- I2 Sơ đồ: mở rộng nhánh, zoom +/−, vừa khung, kéo, bàn phím (+, −, Home, mũi tên), minimap; chọn màn hình hiện chi tiết (nút/trường/đường đi); workspace chưa có sitemap có thông báo.
- I3 Ô tìm kiếm không mất khi quay lại từ tab khác.

### J. Báo cáo và tải xuống
- J1 Excel Evidence mở được, đúng số case, ảnh khoanh đỏ đúng step; tên tệp có dấu tiếng Việt tải về đúng đuôi `.xlsx`.
- J2 ZIP script chứa đúng script của các case đã lưu. Video `.webm` phát được, có chú thích từng bước.

### K. Giao diện, khả năng truy cập, tương thích
- K1 Responsive ở 320, 375, 768, 860, 1024, 1280, 1440, 1920px: không cuộn ngang, không chữ bị cắt/chồng, mọi nút bấm được; ngưỡng 860px chuyển bố cục đúng.
- K2 Sáng/tối theo hệ thống: không chữ khó đọc, màu trạng thái đủ tương phản (≥4.5:1), header cùng bề mặt với trang.
- K3 Bàn phím: Tab đi hết mọi điều khiển theo thứ tự hợp lý, focus nhìn thấy rõ, không bẫy focus, lightbox và ngăn Hoạt động đóng được bằng Esc.
- K4 Trình đọc màn hình: mọi nút/ô có tên (nút chỉ có biểu tượng có `aria-label`), tab có `role/aria-selected`, vùng cập nhật trực tiếp, icon trạng thái đi kèm chữ.
- K5 Không dùng emoji, gradient, phông/script/ảnh từ ngoài; không có lỗi/cảnh báo console.
- K6 Tiếng Việt có dấu chồng (ồấẫậẳằễệổỗộờởỡợừửữựỳỵỷỹặẵẩầ) hiển thị đúng ở mọi kiểu chữ.
- K7 Trình duyệt: Chrome, Edge, Firefox (và Safari nếu có): bố cục, `color-mix`, sticky, `dvh` hoạt động.
- K8 Hiệu năng: thời gian từ mở trang đến có dữ liệu; thời gian vẽ lại khi 200+ case; không "đứng hình" khi làm mới.

### L. Hồi quy so với bản cũ
- L1 Chạy `python -m pytest -q tests/test_dashboard*.py tests/test_run_workflow.py tests/test_fixtures_ui.py` (khoảng 2–3 phút) và ghi test fail nếu có. Nếu fail, phân biệt do thay đổi giao diện hay lỗi có sẵn.
- L2 So sánh với giao diện cũ (nếu có bản trong `~/.odoo-test/backup`): liệt kê **thông tin hoặc nút từng có mà giờ không còn** (ví dụ cột Profile ở danh sách case, vị trí các nút), đánh giá có chủ ý hay thất lạc.

## 4. Ma trận trạng thái cần phủ

Chạy lại các mục C, D, E, F trên từng tình huống: (1) phiên ANALYZE chưa có case; (2) CONFIRM chờ duyệt; (3) EXECUTE nhiều agent chạy song song; (4) AI đang hỏi; (5) có case Need confirm; (6) case FAIL hết lượt sửa; (7) đã dừng; (8) bỏ dở; (9) REPORT có replay; (10) DONE đầy đủ; (11) phiên 0 case; (12) phiên 500 case; (13) dữ liệu thiếu trường (case không có steps, không có ảnh, title rỗng, ảnh 404).

## 5. Đầu ra

1. **Bảng kết quả** theo mã mục (A1…L2): PASS / FAIL / N/A (kèm lý do không kiểm được).
2. **Danh sách lỗi**, mỗi lỗi gồm: mã, mức độ (Blocker / Major / Minor / Cosmetic), các bước tái hiện, kết quả thực tế, kết quả mong đợi, bằng chứng (ảnh/console/network), phạm vi (trình duyệt, kích thước màn hình, loại phiên).
3. **Điểm mạnh/yếu về UX**: yêu cầu của AI có thật sự thấy ngay không, chỗ nào còn rối, thông tin nào bị thiếu so với bản cũ.
4. **Đề xuất ưu tiên** (tối đa 10 mục) và phần bạn **không kiểm được** cùng lý do.
