"""Vietnamese text that reached the dashboard decoded as Windows-1252 is repaired; correct text is left alone."""
import io
import json

from oqa import claims, events, mcp_server, textfix

GOOD = "Kiểm tra dòng item, Total và trạng thái. Sau khi tăng Quantity lên 3 và bấm Save, thiết kế Đã duyệt"


def _mojibake(text, errors_as_control=True):
    out = []
    for byte in text.encode("utf-8"):
        try:
            out.append(bytes([byte]).decode("cp1252"))
        except UnicodeDecodeError:
            out.append(chr(byte))                     # 0x81, 0x8D, 0x8F, 0x90 and 0x9D have no cp1252 character
    return "".join(out)


def test_screenshot_caption_is_repaired():
    # the "a grave" byte A0 is a no-break space; a browser draws it like a space
    assert textfix.repair_text("Kiá»ƒm tra dÃ²ng item, Total vÃ  tráº¡ng thÃ¡i") == "Kiểm tra dòng item, Total và trạng thái"


def test_every_vietnamese_letter_round_trips():
    letters = "ăâđêôơưÀÁẠẢÃĂẰẮẶẲẴÂẦẤẬẨẪĐÈÉẸẺẼÊỀẾỆỂỄÌÍỊỈĨÒÓỌỎÕÔỒỐỘỔỖƠỜỚỢỞỠÙÚỤỦŨƯỪỨỰỬỮỲÝỴỶỸàáạảãằắặẳẵầấậẩẫèéẹẻẽềếệểễìíịỉĩòóọỏõồốộổỗờớợởỡùúụủũừứựửữỳýỵỷỹ"
    for text in (GOOD, letters):
        assert textfix.repair_text(_mojibake(text)) == text


def test_lost_lead_byte_of_a_grave():
    assert textfix.repair_text("Tạo site v�  item") == "Tạo site và item"


def test_correct_and_ascii_text_is_unchanged():
    for text in (GOOD, "plain ASCII 1-3 / 3", "Ã is not followed by a continuation byte", "", "naïve café"):
        assert textfix.repair_text(text) == text


def test_repair_walks_json_values():
    value = {"a": [_mojibake("lưu"), {"b": _mojibake("và")}], "n": 3}
    assert textfix.repair(value) == {"a": ["lưu", {"b": "và"}], "n": 3}


def test_events_and_notes_are_repaired_when_read(tmp_path):
    line = json.dumps({"ts": "t", "kind": "note", "caption": _mojibake("Kiểm tra dòng item")}, ensure_ascii=False)
    (tmp_path / "events.jsonl").write_text(line + "\n", encoding="utf-8")
    assert events.read(tmp_path)[0]["caption"] == "Kiểm tra dòng item"


def test_mcp_server_reads_utf8_not_the_ansi_code_page():
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "echo", "arguments": {"text": "tăng và"}}}
    seen = []
    out = io.StringIO()
    mcp_server.serve(stdin=io.StringIO(json.dumps(request, ensure_ascii=False) + "\n"), stdout=out,
                     tools=[{"name": "echo", "description": "", "inputSchema": {"type": "object", "properties": {}}}],
                     tool_call=lambda name, args: seen.append(args["text"]) or "ok")
    assert seen == ["tăng và"]
