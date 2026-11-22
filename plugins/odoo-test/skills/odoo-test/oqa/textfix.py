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


def _fix_run(match):
    run = match.group(0)
    try:
        return _to_bytes(run).decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return run


def repair_text(text):
    if not isinstance(text, str) or text.isascii():
        return text
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
