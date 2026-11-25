"""One screen size for every picture and every replay, so a layout recorded once is the layout replayed."""
import re
from pathlib import Path

from oqa import browser

WEB = Path(__file__).resolve().parents[1] / "oqa" / "web"


def test_the_viewport_is_full_hd():
    assert browser.VIEWPORT == {"width": 1920, "height": 1080}


def test_both_replay_runners_use_the_same_viewport():
    for name in ("replay.mjs", "replay_browser.cjs"):
        text = (WEB / name).read_text(encoding="utf-8")
        found = re.search(r"viewport:\s*\{\s*width:\s*(\d+),\s*height:\s*(\d+)", text)
        assert found and (int(found.group(1)), int(found.group(2))) == (browser.VIEWPORT["width"], browser.VIEWPORT["height"]), name


def test_live_evidence_browser_takes_the_same_viewport():
    text = (Path(__file__).resolve().parents[1] / "oqa" / "live.py").read_text(encoding="utf-8")
    assert '"contextOptions": {"viewport": dict(VIEWPORT)}' in text
