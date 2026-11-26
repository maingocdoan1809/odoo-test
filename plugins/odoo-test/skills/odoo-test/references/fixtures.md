# Dữ liệu test (fixture)

## Đặt tên
Dùng thuật ngữ nghiệp vụ ngắn cho `fixture_name`, ví dụ "phiếu hai dòng". Mỗi bản ghi case tạo có `--label` mô tả rõ, ví dụ "mặt hàng đối chứng". Công cụ tự thêm tiền tố `AUTO_TEST_` và hậu tố của lần chạy.

## Dữ liệu riêng của từng lần chạy
Mỗi lần chạy tự chuẩn bị dữ liệu của lần đó: tên và mã của mọi bản ghi do case tạo (kể cả gõ tay vào form) lấy từ `oqa fixture key`. Một thao tác gõ tên cố định kiểu `AUTO_TEST_x` bị từ chối, vì tên cố định va vào dữ liệu lần trước để lại (ràng buộc duy nhất, bản ghi không xóa được) và case sẽ đánh giá dữ liệu sót thay vì ứng dụng. Bước kế hoạch không ghi tên hay mã cố định, chỉ nói "bản ghi có khóa riêng của lần chạy".

## Tạo qua màn hình
Fixture tạo qua màn hình, không bao giờ bằng ORM write hay sửa DOM:
- `oqa fixture key --case ID --label NAME`: khóa riêng của lần chạy.
- `oqa fixture ui ledger|license_user|contract --case ID --key FIELD --values-json ...`: quan hệ viết `{"$ref": "existing record"}` (chỉ đọc) hoặc `{"$fixture": "key"}`; ngày tương đối viết `{"$date": -180}`.
- `oqa fixture ref MODEL --case ID --value NAME`.
- `oqa fixture cleanup --case ID [--run rXXXX] [--apply]`: dọn dữ liệu theo khóa chính xác của từng fixture đã ghi nhận.
- `oqa fixture plan <screen>`: trường, route và lệnh mẫu.
