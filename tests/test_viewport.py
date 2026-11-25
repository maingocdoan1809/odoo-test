"""One screen size for every picture and every replay, so a layout recorded once is the layout replayed."""
import re
from pathlib import Path

from oqa import browser

WEB = Path(__file__).resolve().parents[1] / "oqa" / "web"


def test_the_viewport_is_full_hd():
    assert browser.VIEWPORT == {"width": 1920, "height": 1080}


def test_both_replay_runners_use_the_same_viewport():
    """replay.mjs ships alone in the scripts ZIP, so it keeps its own copy; the browser runner is handed the viewport in its config."""
    found = re.search(r"viewport:\s*\{\s*width:\s*(\d+),\s*height:\s*(\d+)", (WEB / "replay.mjs").read_text(encoding="utf-8"))
    assert found and (int(found.group(1)), int(found.group(2))) == (browser.VIEWPORT["width"], browser.VIEWPORT["height"])
    assert "viewport: config.viewport" in (WEB / "replay_browser.cjs").read_text(encoding="utf-8")


def test_live_evidence_browser_takes_the_same_viewport():
    text = (Path(__file__).resolve().parents[1] / "oqa" / "live.py").read_text(encoding="utf-8")
    assert '"contextOptions": {"viewport": dict(VIEWPORT)}' in text
