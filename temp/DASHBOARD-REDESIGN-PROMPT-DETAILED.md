# Prompt: thiết kế lại giao diện Dashboard "Odoo Test"

Bạn là product designer + front-end engineer chuyên công cụ QA / test automation (kiểu Playwright Test UI, Cypress Cloud, TestRail, Allure TestOps). Hãy thiết kế lại **toàn bộ giao diện màn hình Dashboard** mô tả dưới đây. Giao diện hiện tại bị đánh giá là xấu: cũ kỹ, dồn nhiều thứ vào một header, nút bấm và yêu cầu từ AI nằm lẫn trong nội dung.

---

## 1. Công cụ này là gì

**Odoo Test** là một skill cho AI agent (Claude) tự động test hệ thống Odoo từ file testcase Excel.

Quy trình ở phía AI: đọc file testcase → phân tích → kết nối Odoo → lập chiến lược → **chờ người dùng duyệt** → chạy từng case trên trình duyệt thật (mỗi bước chụp ảnh có khung đỏ) → tự sửa khi lỗi → xuất file Excel bằng chứng.

Mỗi lần người dùng yêu cầu AI test một file testcase = **một session**. Một workspace (một hệ thống Odoo/dự án) có nhiều session.

**Dashboard** là trang web chạy trên máy người dùng (localhost), dùng để:
1. Theo dõi tiến độ AI đang test (cập nhật trực tiếp mỗi ~1.5 giây).
2. Xem kết quả: case nào PASS/FAIL, từng bước làm gì, ảnh bằng chứng.
3. **Trả lời / ra quyết định khi AI cần người** (xác nhận, trả lời câu hỏi, gợi ý sửa lỗi, dừng/tiếp tục).
4. Chạy lại script đã lưu (replay), quay video, xuất báo cáo Excel.

Người dùng là QA/BA/dev Odoo, thường để dashboard mở song song với cửa sổ chat AI. Ngôn ngữ giao diện: **tiếng Việt**.

---

## 2. Flow mong muốn (BẮT BUỘC theo đúng flow này)

```
[Màn 1] Dashboard = danh sách session
        │  bấm 1 session
        ▼
[Màn 2] Chi tiết session
        ┌───────────────────────┬──────────────────────────────────────────┐
        │ Trái: danh sách       │ Phải: chi tiết case đang chọn            │
        │ TESTCASE của session  │ = danh sách STEP + ảnh + thông tin khác  │
        │ (tự chọn case đầu)    │                                          │
        └───────────────────────┴──────────────────────────────────────────┘
        nút "← Dashboard" quay về Màn 1
```

- **Màn 1** không còn sidebar lịch sử như hiện nay. Danh sách session là nội dung chính của trang.
- Vào Màn 2: **tự động chọn case đầu tiên** của danh sách (nếu có case FAIL/đang chạy thì có thể ưu tiên case đó, nhưng phải luôn có một case được chọn, không để panel phải trống).
- Có thể đi thẳng vào Màn 2 bằng URL hash `#workspace/session` (đã có, giữ nguyên).
- Ngoài ra còn 2 tab cấp cao nhất đã tồn tại: **Scripts** (ma trận script × môi trường, chạy regression) và **site_map** (sơ đồ menu/màn hình Odoo). Chỉ cần đồng bộ style với giao diện mới, không thiết kế lại nội dung.

---

## 3. Màn 1 – Dashboard (danh sách session)

**Mục đích:** nhìn một lần biết session nào cần mình xử lý, session nào đang chạy, session nào xong.

**Thanh trên cùng (global):** tên "Odoo Test"; tab *Tiến độ test / Scripts / site_map*; chỉ báo trạng thái kết nối trực tiếp ("Trực tiếp · cập nhật 14:02:11" với chấm xanh nhấp nháy, hoặc "Đã xong").

**Công cụ:** bộ lọc workspace (dropdown "Tất cả workspace"), ô tìm theo tên file, lọc nhanh theo trạng thái (Cần tôi xử lý / Đang chạy / Đã xong).

**Mỗi session là một hàng/thẻ, gồm:**

| Thông tin | Ghi chú |
|---|---|
| Tên file testcase (vd `Test_DonHang.xlsx`) | tiêu đề chính |
| Workspace + thời gian bắt đầu | phụ |
| **Giai đoạn (phase)** | Phân tích → Kết nối → Xác nhận → Thực thi → Báo cáo → Xong |
| **Huy hiệu cần chú ý** | `Chờ bạn` (AI đang hỏi / chờ duyệt chiến lược / có case Need confirm) · `Bỏ dở?` (không hoạt động ≥ 6 giờ) · `Đã dừng` |
| Thanh tiến độ nhiều màu theo trạng thái case + số "x/tổng case" | PASS / FAIL / đang chạy / chưa chạy… |
| Số liệu: PASS, FAIL, tổng case | |
| Số worker đang chạy (vd 3/5) | chỉ khi đang Thực thi |
| Nút xoá session (có hộp xác nhận: "ảnh bằng chứng sẽ mất, không khôi phục") | không cho xoá khi đang chạy |

**Yêu cầu bố cục:** session `Chờ bạn` phải nổi bật nhất và nên được nhóm/ghim lên đầu ("Cần bạn xử lý (2)"), sau đó "Đang chạy", rồi "Đã xong". Trạng thái rỗng: "Chưa có phiên test nào. Hãy bảo AI test một file testcase."

---

## 4. Màn 2 – Chi tiết session

### 4.1 Thanh đầu session (gọn, không chiếm quá ~120px)
- Nút **← Dashboard**; tên file; badge giai đoạn hiện tại; badge `Đã dừng` nếu có.
- Meta (chữ nhỏ, một dòng): workspace · session id · bắt đầu lúc · phiên bản Odoo · số mục tri thức · dòng "AI đang làm gì" (`now`, vd "Đang chạy case TC-03").
- **Phase stepper** 6 bước (Phân tích / Kết nối / Xác nhận / Thực thi / Báo cáo / Xong), bước đã xong có giờ.
- **Tiến độ:** "đã có kết luận x/tổng", thanh tiến độ nhiều màu, chip số lượng theo từng trạng thái, "n đang chạy · a/5 worker".
- **Replay:** tổng Replay PASS/FAIL/NOT RUN.
- **Hành động cấp session** (gom vào một nhóm nút ở góc phải, không rải rác):
  - `Dừng test` (đỏ, chỉ khi đang Thực thi) / `Tiếp tục test` (khi đã dừng)
  - `Xuất / Cập nhật báo cáo Excel` + `Tải file Evidence (.xlsx)` (khi đã có)
  - `Tải ZIP script` (khi có case đã lưu script)
  - `Chạy lại tất cả` và `Quay video tất cả` (chỉ khi phase Báo cáo/Xong và có script)
- Cảnh báo bỏ dở: "Không có hoạt động 7 giờ nay. Bảo AI chạy `oqa status` để tiếp tục."

### 4.2 Khu vực "AI cần bạn" (QUAN TRỌNG NHẤT về UX)
Mọi yêu cầu AI đang chờ người dùng phải xuất hiện **ngay trong tầm mắt, không phải cuộn, không nằm trong mục thu gọn**. Đề xuất: một **banner/khối hành động cố định ngay dưới thanh đầu session**, màu nhấn nổi bật nhưng trung tính (không đỏ chói), có thể thu nhỏ nhưng mặc định mở. Khi có yêu cầu, có thêm chấm/đếm ở tab hoặc tiêu đề trang. Các loại yêu cầu (mỗi loại cần UI riêng):

| # | Khi nào | Nội dung | Hành động |
|---|---|---|---|
| 1 | AI hỏi người dùng (`waiting`) | câu hỏi (nhiều dòng) + case liên quan | ô nhập trả lời (cho phép dán đường dẫn tệp thiết kế/file import, AI tự đọc) · `Gửi trả lời` |
| 2 | Phase Xác nhận (`can_confirm`) | "Chiến lược chờ duyệt": xem thứ tự chạy + dữ liệu dự phòng (markdown dài, mặc định xem được ngay dạng khối mono cuộn) | `Xác nhận và bắt đầu test` (nút chính) |
| 3 | Rà soát testcase (`waiting.review`) – từng case "Need confirm" | danh sách câu hỏi của AI về case đó | ô nhập · `Gửi trả lời` · `Bỏ qua testcase`. Ở banner chỉ cần "Có N case cần xác nhận" + nút nhảy tới case đầu tiên; form trả lời nằm ở panel phải của case đó **và** có thể làm nhanh ngay trong banner |
| 4 | Case FAIL / hết số lần sửa (`can_hint`) | "AI cần gợi ý của bạn" + các gợi ý bạn đã gửi trước đó | ô nhập (vd "nút Lưu nằm trong menu Hành động…") · `Gửi và chạy lại`. Hiện ở đầu panel case, và ở danh sách trái case đó có dấu hiệu "cần gợi ý" |
| 5 | Session đã dừng (`stopped`) | "Bạn đã dừng test. Bước đang chạy được hoàn tất, AI đang đợi." | `Tiếp tục test` |
| 6 | Worker lỗi (`request.failure`) | "Lỗi worker (giờ): …" – chưa phải kết luận của case | hiện ở case; kèm nút `Yêu cầu điều phối` / `Yêu cầu giao lại` |
| 7 | Tệp tham chiếu | dán đường dẫn BD/đặc tả/thư mục cho AI đọc | ô nhập + `Thêm đường dẫn`, danh sách tệp đã thêm (tên, loại tệp/thư mục, đường dẫn, case). Là hành động phụ, đặt ở drawer/popover "Tệp tham chiếu", không chiếm chỗ chính |

Quy tắc: nội dung đang gõ (draft) **không được mất** khi dữ liệu tự cập nhật; không redraw khi người dùng đang gõ hoặc đang bôi đen văn bản; giữ nguyên vị trí cuộn.

### 4.3 Cột trái – Danh sách testcase
- Tiêu đề "Testcase (N)", nút thu gọn cột, ô tìm kiếm + lọc theo trạng thái (Tất cả / FAIL / Đang chạy / Cần xác nhận / PASS / Chưa chạy).
- Mỗi case là một dòng gọn (~56px), gồm: **mã case**, **tiêu đề** (tối đa 2 dòng), **trạng thái Live** (icon + chữ + màu; spinner khi đang chạy/sửa/xác thực), **tiến độ step** (dải ô nhỏ xanh/đỏ/xám + "x/y"), **số lần chạy**, **Replay status** (PASS/FAIL/NOT RUN) và tag `Need confirm` / `Đã bỏ qua` / `Ready`, tên agent đang giữ case (nếu có).
- Case đang chọn có highlight rõ; điều hướng được bằng bàn phím (↑/↓, Enter).
- Cột này cuộn độc lập.

### 4.4 Cột phải – Chi tiết case đang chọn
Giữ **đầy đủ thông tin như hiện tại**, nhưng sắp xếp lại theo thứ tự ưu tiên:

1. **Header case:** `mã · tiêu đề`; trạng thái Live; phân loại lỗi (Lỗi ứng dụng / Lỗi automation / Thiếu điều kiện kiểm thử); lý do; Replay status + thời lượng (`Chạy nhanh · 12s (nghỉ 3s, 40 lần gọi 5s)`) + lý do replay FAIL.
2. **Thanh hành động của case** (một hàng nút, nhóm hợp lý): `Chạy lại testcase`, `Quay video`, `Xem video`, công tắc `Chạy nhanh`, 2 ô thời gian nghỉ (sau thao tác / sau bước, ms – vô hiệu khi bật Chạy nhanh), `Yêu cầu điều phối / giao lại` (+ thông báo: đang chờ / đã giao cho worker X / bị từ chối + lý do). Chỉ hiện khi hợp lệ (replay chỉ khi case PASS/FAIL).
3. **Chips thông tin:** Profile, Tiếp từ case khác, Rõ ràng n/5, Đủ dữ kiện n/5, dòng trong Excel.
4. **Khối AI cần bạn của case** (Need confirm / Gợi ý cho AI / lỗi worker) – xem 4.2.
5. **Tab các lần chạy** (khi >1 lần): "Lần 1 · FAIL", "Lần 2 · PASS · từ bước 3".
6. **Danh sách STEP** (phần chính, chiếm nhiều diện tích nhất). Mỗi step:
   - số thứ tự / spinner; trạng thái (PASS, FAIL, RUNNING "đang chạy bước này…", TRYING "đang thử trên trang thật…", VERIFIED, PENDING "chưa chạy");
   - **mô tả thao tác** (caption);
   - **Mong đợi** (+ tag "n kiểm tra", + tag "giữ từ lần trước");
   - **Ảnh cần chụp** (evidence yêu cầu);
   - **Thực tế** (kết quả thật khi chạy, nổi bật nếu FAIL);
   - **Ảnh bằng chứng** thumbnail (viền đỏ nếu FAIL, badge "n ảnh"); bấm mở **lightbox** (giữ nguyên chức năng: ảnh khoanh đỏ ↔ ảnh gốc phím R, ←/→ chuyển ảnh, Esc đóng).
7. **Testcase gốc** (từ Excel): điều kiện tiên quyết, các bước gốc (danh sách đánh số), kết quả mong đợi. Đặt thu gọn được, hoặc tab "Testcase gốc / Các bước sau đánh giá".
8. **Phân tích của AI** (thu gọn): tình huống biên, dữ liệu có thể thiếu → phương án dự phòng, câu hỏi cần hỏi, thời gian chờ tối đa; các ghi chú sửa lỗi (Nguyên nhân / Căn cứ / Đã sửa / Hiểu lại case / Thời gian chờ / Script giữ nguyên; "đã dò trang thật" hay chưa).
9. **Lịch sử kết luận** (thu gọn): các lần kết luận bị sửa (thời gian · trạng thái · phân loại · lý do → thay bởi).

### 4.5 Khu vực "Đang chạy trực tiếp" (chỉ khi phase Thực thi)
- Chip các case đang chạy ("case TC-03 · 4/9 bước"), bấm nhảy tới case.
- "Chưa có agent nhận: case X, Y".
- **Hoạt động trực tiếp** (feed theo thời gian, mọi agent, mọi case: bắt đầu thử bước, bước thử xong/chưa đạt, đã dựng script, lần chạy kết thúc, ghi nhận sửa) và **Ghi chú chung** của các agent.
- Đặt ở một drawer/panel đáy thu gọn được, không chiếm chỗ của danh sách step.

---

## 5. Bảng trạng thái & màu (phải nhất quán toàn app)

Dùng **màu ngữ nghĩa + icon + chữ** (không chỉ dựa vào màu, để người mù màu phân biệt được):

- Case: `PASS` xanh lá · `FAIL` đỏ · `RUNNING` / `EXPLORING` (đang xác thực) / `REPAIRING` (đang sửa lỗi) – xanh dương/teal/cam + spinner · `BUILT` (chờ chạy), `PLANNED` (chờ xác thực), `NEW`, `HELD` (cần xử lý trước), `NOT EXECUTED` (chưa chạy) – xám · `PENDING` – vàng · `BLOCKED`, `STOPPED`, `INTERRUPTED` (bị bỏ dở) – xám đậm.
- Step: `PASS`, `FAIL`, `RUNNING`, `TRYING`, `VERIFIED`, `PENDING`.
- Replay: `PASS`, `FAIL`, `NOT RUN`.

---

## 6. Phong cách (theme)

- **Trung tính, chuẩn một ứng dụng test/QA chuyên nghiệp**: nền sáng xám-trắng (#f6f7f9 / #fff), viền xám nhạt, chữ xám đậm; một màu nhấn xanh dương duy nhất cho nút chính và phần chọn. Màu đỏ/xanh lá/vàng **chỉ dành cho trạng thái**.
- Phong cách gần Linear / GitHub Actions / Playwright UI: mật độ thông tin vừa phải, bo góc nhỏ (4–6px), đổ bóng tối thiểu, không gradient, không minh hoạ trang trí.
- Có **dark mode** tự theo `prefers-color-scheme`.
- Font hệ thống (Segoe UI / Inter / Roboto) – **không tải font ngoài**; font mono cho mã case, id, đoạn code, chiến lược. Chữ số dùng `tabular-nums`.
- Cỡ chữ nền 14px; tối thiểu 11px cho meta. Độ tương phản đạt WCAG AA.
- Chuyển động tối thiểu: spinner cho trạng thái đang chạy, chấm nhấp nháy cho "trực tiếp"; tôn trọng `prefers-reduced-motion`.

---

## 7. Ràng buộc kỹ thuật (để code được ghép thẳng vào dự án)

- Đầu ra: **một file HTML tĩnh duy nhất** (HTML + CSS + JS thuần, không framework, không CDN, không font/ảnh ngoài), thay thế `oqa/web/dashboard.html`.
- Server thay các placeholder sau lúc phục vụ trang, **phải giữ nguyên**: `__TOKEN__`, `__BOOT__`, `/*__FONT_FACES__*/`, `/*__SITE_MAP_CSS__*/`, `/*__SITE_MAP__*/` (script + CSS của tab site_map). Nếu mock bằng dữ liệu giả, đặt trong khối `<script>` dễ gỡ.
- Dữ liệu lấy qua API đã có (GET kèm `?t=TOKEN`, POST JSON): `/api/sessions`, `/api/workspaces`, `/api/session?ws=&id=`, `/api/image`, `/api/report`, `/api/scripts`, `/api/video`, `/api/regression`, `/api/sitemap`; hành động: `/api/confirm`, `/api/stop`, `/api/resume`, `/api/answer`, `/api/review`, `/api/hint`, `/api/refs`, `/api/replay`, `/api/regress`, `/api/dispatch-request`, `/api/delete_session`. **Không đổi API và cấu trúc dữ liệu**; chỉ đổi cách hiển thị.
- Dữ liệu session (từ `/api/session`) gồm: `file, workspace, session, phase, phases[], waiting{question,case,review}, stopped, can_stop, can_confirm, strategy, created, updated, idle, idle_hours, now, counts{}, replay_counts{}, total, workers{active,limit,cases}, free_cases[], notes[], activity[], connect{version}, knowledge, reports[], refs[]` và `cases[]` (mỗi case: `id, title, status, profile, row, category, reason, attempts[{n,status,from_step,steps[]}], planned[], explore{trying,verified,live}, source{pre,steps,expected}, repairs[], history[], request{}, can_request, can_replay, replay, replay_video, review_pending, review_skipped, questions[], hints[], can_hint, exhausted, owner, clarity, sufficiency, depends_on[], edge_cases[], data_needs[], timeout_s`).
- Dữ liệu cập nhật mỗi 1.5s (session) và 6s (danh sách): **chỉ vẽ lại khi dữ liệu đổi**, giữ vị trí cuộn của từng cột, không mất nội dung đang gõ, không vẽ lại khi người dùng đang bôi đen chữ.
- Responsive: ≥1100px hai cột cạnh nhau; <860px cột danh sách case chuyển lên trên, chi tiết phía dưới, cả trang cuộn dọc; dùng được ở 375px.
- Truy cập bàn phím đầy đủ (tab, mũi tên trong danh sách case, Enter), `aria-*` cho tab, dialog, vùng live; focus ring rõ.
- Escape mọi chuỗi động trước khi chèn HTML.

---

## 8. Cần bạn giao

1. **Mock-up HTML** (dữ liệu giả, đủ để duyệt bằng mắt) cho: Màn 1 ở các trạng thái *có session chờ bạn / đang chạy / đã xong / rỗng*; Màn 2 ở các trạng thái *đang Thực thi có agent chạy, chờ trả lời câu hỏi, chiến lược chờ duyệt, case Need confirm, case FAIL cần gợi ý, đã dừng, đã Xong có báo cáo + replay*; lightbox ảnh; light + dark; 1440px và 375px.
2. Bản chốt gồm design tokens (màu, khoảng cách, cỡ chữ, bán kính) khai báo ở `:root`.
3. Giải thích ngắn (≤10 dòng) vì sao đặt các khối "AI cần bạn" ở vị trí đó.
4. Sau khi được duyệt: chuyển thành `oqa/web/dashboard.html` hoạt động thật với API hiện có (giữ nguyên các placeholder và logic gọi API).
