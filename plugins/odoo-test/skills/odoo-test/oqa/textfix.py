"""Repair Vietnamese (and other non-ASCII) text that was UTF-8 but got decoded as Windows-1252 on its way in.

A worker's note such as "tăng lên và" arrives as "tÄƒng lÃªn vÃ\xa0" when a stdio pipe or a console is read with the ANSI code page.
`repair` turns each such run back into what was typed and leaves correct text alone, so the dashboard, the Excel report and the notes show it properly."""
import re

_SPECIAL = "€‚ƒ„…†‡ˆ‰Š‹ŒŽ‘’“”•–—˜™š›œžŸ"
# one UTF-8 lead byte (C2-F4 seen through cp1252) followed by its continuation bytes (80-BF seen through cp1252)
_RUN = re.compile("(?:[Â-ô][\u0080-¿" + _SPECIAL + "]{1,3})+")
_LOST_LEAD = "� "       # the lead byte of "à" (C3 A0) was dropped: only the replacement character and the no-break space are left


def _to_bytes(text):
    out = bytearray()
    for ch in text:
        try:
            out += ch.encode("cp1252")
        except UnicodeEncodeError:
            if "\u0080" <= ch <= "\u009f":
                out.append(ord(ch))
            else:
                raise
    return bytes(out)


def _plausible(ch):
    """A character mis-decoded text really holds: Latin letters and their accents (Vietnamese included) and common punctuation. Anything else (Cyrillic, IPA...)
    means the run was correct text that only happens to look like UTF-8 bytes, such as "CÓ" followed by a closing quote."""
    code = ord(ch)
    return (0xA0 <= code <= 0x24F or 0x300 <= code <= 0x36F or 0x1E00 <= code <= 0x1EFF
            or 0x2000 <= code <= 0x206F or 0x20A0 <= code <= 0x20CF or code in (0x2122, 0x2116))


def _fix_run(match):
    run = match.group(0)
    try:
        fixed = _to_bytes(run).decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return run
    return fixed if all(_plausible(ch) for ch in fixed) else run


def _decoded_elsewhere(text):
    """True when the text holds a character Windows-1252 cannot produce (such as "ạ" or "Đ"): it was read as UTF-8, so a run inside it is real text."""
    for ch in text:
        if ch.isascii() or ord(ch) == 0xFFFD or 0x80 <= ord(ch) <= 0x9F:
            continue
        try:
            ch.encode("cp1252")
        except UnicodeEncodeError:
            return True
    return False


def repair_text(text):
    if not isinstance(text, str) or text.isascii():
        return text
    if not _decoded_elsewhere(text):
        text = _RUN.sub(_fix_run, text)
    return text.replace(_LOST_LEAD, "à") if _LOST_LEAD in text else text


def repair(value):
    """`repair_text` for every string inside a JSON-like value."""
    if isinstance(value, str):
        return repair_text(value)
    if isinstance(value, list):
        return [repair(v) for v in value]
    if isinstance(value, dict):
        return {k: repair(v) for k, v in value.items()}
    return value


_CONTROL = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|[\x00-\x08\x0b\x0c\x0e-\x1f]")


def strip_control(text):
    """Terminal colour codes and other control characters: a browser tool's output carries them, and a workbook refuses a cell that holds one."""
    return _CONTROL.sub("", text) if isinstance(text, str) else text
