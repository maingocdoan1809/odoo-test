# Plan triển khai và nghiệm thu Odoo Test

## Hợp đồng của một lượt test

1. Excel add-in chuyển workbook đang mở hoặc user gửi đường dẫn. Chọn toàn bộ hoặc một tập ID. Lưu định danh workbook, sheet/dòng và lựa chọn trong session.
2. Luôn mở form workspace một lần cho yêu cầu. Bắt đầu monitor trước khi mở. User chọn/cập nhật workspace cũ hoặc tạo mới. Đọc cấu hình sau submit rồi nạp sitemap đã cache theo workspace.
3. Phân tích testcase gốc, điều kiện trước, edge case, dữ liệu tự tạo được, ràng buộc môi trường và kỳ vọng quan sát được. Khi vướng, tìm sitemap trước rồi tra lịch sử và knowledge.
4. Mở dashboard sau phân tích. Monitor sự kiện theo session. Hiện testcase gốc, phiên bản đánh giá, giải thích và badge Ready / Need confirm. Câu hỏi, câu trả lời, file và Skip gắn với từng case. Đọc lại case và tài liệu sau feedback. Chỉ đưa case đã sẵn sàng vào phạm vi duyệt cuối.
5. User duyệt phạm vi cuối. Ràng buộc phê duyệt với phiên bản kế hoạch để scope cũ không thể chạy sau thay đổi. Worker khởi động sau duyệt. Backend Claude trên máy này dùng Sonnet 5.5 và Haiku 5.5. Tối đa 5 worker case đồng thời. Một agent dữ liệu phục vụ yêu cầu lập fixture theo từng lượt, còn helper tạo dữ liệu chạy trong browser của case đã duyệt.
6. Mỗi case giữ browser playwright-cli riêng, đăng nhập đúng profile, thao tác live, ghi kỳ vọng và thực tế. Điều phối theo dõi worker, tra sitemap/lịch sử/knowledge và chuyển thông tin cụ thể khi worker kẹt. Đề xuất tối đa một lần phục hồi cho cùng lỗi khi có thông tin mới, mỗi thao tác ghi dữ liệu phải kiểm tra trạng thái trước khi thử lại.
7. Dựng script regression từ bước đã kiểm chứng. Với case thao tác trên bản ghi, script chuẩn bị bản ghi test đáp ứng điều kiện của case và dùng định danh vừa tạo cho các bước tiếp theo. Tạo bản ghi đối chiếu khi kỳ vọng cần so sánh. Case chỉ kiểm tra visual kiểm tra trực tiếp giao diện và chụp evidence, với bước chuẩn bị dữ liệu chỉ khi trạng thái hiển thị cần kiểm tra phụ thuộc vào dữ liệu. Dashboard replay gọi runner đã lưu và ghi kết quả theo attempt. ZIP gồm script, helper, fixture cần dùng và hướng dẫn chạy.
8. Ghi đúng dòng testcase và evidence. Với đường dẫn, tạo bản sao nguyên cấu trúc workbook. Với add-in, cập nhật workbook đang mở qua cơ chế ghi của add-in.

## Checklist triển khai và nghiệm thu

| Cổng | Việc cần triển khai | Kiểm tra đạt |
|---|---|---|
| Input và setup | Workspace picker, submit event, lưu lựa chọn và monitor theo session | Hai yêu cầu liên tiếp mở form đúng một lần mỗi yêu cầu. Chọn workspace cũ và tạo mới đều được. Tập case chọn đúng. Workbook thiếu cột báo sheet/dòng. |
| Rà soát | Lưu phản hồi/skip theo case và ảnh chụp phiên bản phân tích | H18 hiện Need confirm và chuyển Ready sau khi nhận BD. H19 hiện yêu cầu cho phép tạo dữ liệu. H20 được bỏ qua. Kế hoạch thay đổi cần duyệt lại. |
| Điều phối | Hàng đợi, giới hạn slot, trao đổi agent, nhật ký phục hồi | Với ít nhất 6 case ready, số worker case đang chạy không vượt 5. Mỗi case chỉ có một chủ sở hữu. Mô phỏng một worker lỗi bằng test double và chứng minh điều phối hồi phục hoặc trả lý do hữu ích. |
| Dữ liệu | Phân loại nhu cầu dữ liệu khi phân tích. Helper xử lý field bắt buộc, quan hệ và mã theo lượt | Case bấm nút nghiệp vụ, sửa, xóa hoặc đổi trạng thái thao tác trên bản ghi test do case tạo. Case visual thuần mở màn hình, kiểm tra và chụp ảnh trực tiếp. Case có fixture chạy liên tiếp hoặc song song với mã riêng. |
| Live browser | Session riêng theo case, log bước đã kiểm chứng, bằng chứng | H12 dùng đúng hai profile. Với lỗi mong đợi, ảnh và assertion ghi nhận lỗi; verdict của testcase là PASS nếu hệ thống từ chối đúng. |
| Script | Sinh script và kiểm tra hợp đồng gói export | Có assertion theo trạng thái, cấu hình server/profile và bước chuẩn bị cho case cần bản ghi. ZIP giải nén chạy được bằng runner ghi trong gói. Rà script để loại chờ cứng, thao tác dò đường, ID bản ghi cố định và secret. |
| Replay | Runner độc lập lưu report theo attempt | Replay hai lần liên tiếp trên cùng database harness. Case thao tác chọn đúng bản ghi vừa tạo ở mỗi lượt. Case visual thuần kiểm tra được giao diện trực tiếp. Tắt AI/worker khi replay. |
| Báo cáo | Ghi đúng dòng và sheet evidence, giữ bản gốc theo kênh | Đường dẫn: hash file nguồn giữ nguyên, công thức ở sheet Workbook integrity còn nguyên trong bản sao. Add-in: cập nhật workbook đang mở. Case ngoài phạm vi giữ nguyên. |
| Hiệu năng | Thu bước, thời điểm, slot, retry và lý do | Báo thời gian setup, review, live, dựng script và replay riêng. Đo lượt đầu và lượt dùng cache. Mỗi retry có nguyên nhân và thông tin mới. |

## Harness Odoo 19

Module `harness/addons/oqa_harness` có Site → Item, Site → Request → Request Line. Nó cung cấp ràng buộc required, unique, quantity/cost dương, liên kết đúng site, tính subtotal/total, archive, restrict delete, workflow và hai quyền Operator/Manager. Wizard import dùng `base_import`. Workbook `harness/odoo-harness-20.xlsx` được sinh từ `harness/cases.txt`.

- H01–H05: phép tính, rollback và ràng buộc liên model.
- H06–H09: import thành công, required, quan hệ thiếu, duplicate và import lại sau sửa.
- H10–H14: workflow, phân quyền, archive và xóa bản ghi bị tham chiếu.
- H15–H17: dữ liệu mồi trùng tên, recompute liên model và xóa dòng.
- H18–H20: nhận BD sau câu hỏi, xin phép tạo dữ liệu và skip theo case.

### Khởi động

Dùng một database harness chuyên dụng, ví dụ `oqa_harness`. Đảm bảo PostgreSQL theo cấu hình đang chạy và tài khoản Odoo được phép tạo database. Chạy từ repo bằng PowerShell. Danh sách addon lấy từ cấu hình được cung cấp và thêm thư mục harness cho riêng tiến trình.

```powershell
$odooPython = 'C:\Users\maido\odoo-venv\Scripts\python.exe'
$odooBin = 'C:\Users\maido\odoo\odoo-bin'
$cfg = 'C:\Users\maido\odoo-dev\odoo.conf'
$originalAddons = ((Get-Content -LiteralPath $cfg | Where-Object { $_ -match '^\s*addons_path\s*=' }) -split '=', 2)[1].Trim()
$addons = "$originalAddons,$((Resolve-Path harness/addons).Path)"

& $odooPython $odooBin -c $cfg --addons-path $addons -d oqa_harness -i oqa_harness --test-tags /oqa_harness --http-port 8079 --stop-after-init
& $odooPython $odooBin -c $cfg --addons-path $addons -d oqa_harness --db-filter '^oqa_harness$' --http-port 8079
```

Tạo hai user nội bộ trong database harness và gán nhóm OQA Harness Operator / OQA Harness Manager. Khai báo hai profile tên Operator / Manager trong workspace. H15 tự chuẩn bị hai item trùng tên để kiểm tra việc chọn đúng bản ghi. Workbook yêu cầu code và tên site duy nhất theo lượt. Dùng `harness/render_import.py` để tạo CSV theo mã lượt và tên site.

### Thứ tự nghiệm thu

1. Chạy test module Odoo trước. Kiểm tra import và workflow ở cấp ORM. Sau đó mở UI xác minh menu và wizard dưới cả hai profile.
2. Chạy subset H06–H09 và H18–H20 qua đường dẫn. Chỉ gửi `harness/fixtures/BD-H18.txt` khi dashboard hỏi H18. Cho phép tạo dữ liệu ở H19. Bấm bỏ qua H20. Xác nhận phạm vi cuối mới cho agent chạy.
3. Chạy đủ 20 case qua đường dẫn và add-in. Với feedback trên, phạm vi cuối có 19 case chạy và H20 bỏ qua. Các case kiểm tra lỗi import/constraint phải PASS khi lỗi quan sát khớp kỳ vọng.
4. Export script của các case đã chạy và replay hai lần trên cùng database harness. Kiểm tra H15 chọn đúng item do lượt chạy vừa tạo. Kiểm tra H18 chỉ chuẩn bị dữ liệu để đưa request tới trạng thái Submitted cần quan sát. Dùng thêm một smoke check visual trên màn hình Items: kiểm tra các cột Name, Code, Site và Cost rồi chụp evidence. Lưu version module, input, kế hoạch đã duyệt, event log, kết quả từng bước, workbook và gói export.
5. Ghi kết quả từng cổng vào checklist. Chỉ chốt khi kiểm tra UI, replay và hai chế độ ghi Excel đã có bằng chứng.

### Lệnh kiểm tra tự động trên Windows

Sau khi Odoo phục vụ database `oqa_harness` ở cổng 8079:

```powershell
.\harness\run_smoke.ps1 -OutputDirectory "$env:LOCALAPPDATA\odoo-test\acceptance"
.\harness\run_smoke.ps1 -AgentPilot
.\harness\run_smoke.ps1 -Matrix -Replay -OutputDirectory "$env:LOCALAPPDATA\odoo-test\acceptance\matrix"
```

Smoke kiểm tra browser thật, tạo hai bản ghi liên kết, bốn kịch bản import, hai lượt replay, ZIP độc lập và kết quả Excel. Agent pilot dùng một case visual riêng để kiểm tra Haiku gọi đúng các công cụ giới hạn theo case. Matrix gửi workbook thật cho Sonnet lập kế hoạch và Haiku thực thi, dùng vai người dùng giả lập để trả lời H18, cho phép H19, bỏ qua H20 và duyệt phạm vi. Có thể thêm `-Cases "H06..H09"` để chạy lại một nhóm.

## Checklist trạng thái

- [x] Có module Odoo 19, workbook 20 case và bốn fixture CSV.
- [x] Kiểm tra cú pháp Python/XML và intake đủ 20 case, gồm lọc subset và phát hiện thiếu tài liệu.
- [x] Cài module trên PostgreSQL và chạy test ORM của module.
- [x] Smoke Odoo thật với browser live, fixture Site → Item, bốn kịch bản import và hai lượt replay trên cùng database.
- [x] Chạy ZIP đã giải nén bằng runner độc lập và xác minh bản Excel nguồn không đổi khi xuất bản sao.
- [x] Smoke ghi vào Excel đang mở hai lần, giữ case ngoài phạm vi và công thức gốc.
- [x] Pilot worker Haiku thực thi case visual qua MCP và lưu evidence.
- [ ] Xác minh UI, import, phân quyền và hai lượt xác nhận.
- [ ] Chạy đủ workbook qua đường dẫn và add-in. Kiểm tra giới hạn worker và xử lý worker lỗi.
- [ ] Xuất ZIP và replay hai lần trên cùng database. Kiểm tra riêng case thao tác có fixture và visual thuần.
- [ ] Đối chiếu dữ liệu ghi Excel và thu số đo hiệu năng.

Đã khởi động container PostgreSQL hiện có và cài module lên `oqa_harness` bằng `odoo.conf`. Odoo báo 0 lỗi trên 4 test ORM. Bridge Office Partner phản hồi kiểm tra sức khỏe. Việc khởi chạy trực tiếp từ pane Office Partner cần phiên Excel kết nối để nghiệm thu riêng.

Lượt cố định `40158f38b189` trên một database có 19 PASS live và H20 được bỏ qua theo kịch bản. Hai lượt replay trên mỗi script cho 38 FAIL. Các lỗi gồm kỳ vọng lưu dưới dạng mô tả thay vì chữ hiện trên trang, thiếu fixture trong script, giá trị quan hệ sai kiểu và URL tương đối chưa được giải quyết khi chạy CLI. PASS live và PASS replay là hai cổng nghiệm thu riêng. Yêu cầu kiểm tra qua pane Office Partner đã bị từ chối trong Excel. Kiểm tra ghi kết quả trực tiếp vào workbook đang mở bằng desktop Excel đã pass.

Các bản sửa tiếp theo kiểm tra chữ hiển thị ngay lúc ghi evidence, giữ thao tác đã xác nhận trong nhật ký, liên kết fixture theo khóa và đồng bộ tiền tố khi replay. Smoke trên Odoo đã PASS hai lượt replay và runner ZIP. Hai script chẩn đoán H07 và H12 đã được sửa riêng để mở đúng wizard và chọn gợi ý tìm kiếm sau khi nó xuất hiện. Mỗi script sửa riêng đã PASS hai lượt replay trên cùng database. Kết quả đó xác minh hai đường sửa cụ thể. Cổng nghiệm thu script tự sinh cho toàn bộ workbook vẫn cần lượt chạy đầy đủ.
