# Regression

Workspace liệt kê các môi trường (`main` cùng staging, production... với tài khoản và mật khẩu riêng) trong `oqa init`.

- `oqa regress --env NAME` chạy lại mọi script đã lưu ở môi trường đó, ghi PASS/FAIL theo từng script và môi trường (thoát với mã 1 nếu có FAIL).
- `oqa regress --matrix` và tab Scripts trên dashboard hiển thị bảng kết quả, mỗi script nối với testcase của nó.
- Script chạy tay (`node replay.mjs`, `OQA_ENV=NAME`) ghi `result.json` và thoát 0/1; `oqa results import <folder>` đưa kết quả lên dashboard.
- `--video on|fail` quay video kèm ghi chú từng bước (`fail` chỉ giữ video của bước lỗi; tắt khi dùng `--fast`).
