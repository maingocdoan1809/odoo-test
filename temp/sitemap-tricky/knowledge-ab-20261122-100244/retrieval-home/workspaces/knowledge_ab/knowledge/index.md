# Knowledge index

Related facts share an entry. Each fact keeps its own evidence and source.
kind: login | navigation | locator | data | error | fix | quirk | rule
Optional labels: action, condition, profile, state, evidence.
Never write a password, token or cookie here.

- [screen:oqa.harness.request] [kind:rule] [action:đọc] [condition:mã đơn vị vận chuyển]
  - [evidence:Kiểm tra trạng thái Submitted trên form.] Chỉ xác nhận mã vận chuyển trên bản ghi Submitted. Bản Draft trùng tên là bản đối chứng. -> TRICKY01 evidence
  - [evidence:Đối chiếu giá trị bằng tên kỹ thuật của field trong nhóm Transport.] Reference của đơn vị vận chuyển là field x_ab_carrier_reference trong nhóm Transport. Reference trong Customer là mã khách. -> TRICKY01 evidence
