"""One place that starts Playwright: the same viewport and settings for connect, run and probe, so a picture taken anywhere looks the same."""
from contextlib import contextmanager
import os
from pathlib import Path

VIEWPORT = {"width": 1920, "height": 1080}      # full HD, the same everywhere (live evidence, connect, PgAdmin, both replay runners): oqa/web/replay.mjs (which ships alone) repeats it and tests/test_viewport.py keeps them equal; replay_browser.cjs is handed it


def headless_channel():
    if os.name == "nt":
        roots = (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"))
        if any(root and (Path(root) / "Microsoft/Edge/Application/msedge.exe").is_file() for root in roots):
            return "msedge"
    return None


@contextmanager
def launch(headless=True):
    """Yield a function `new_page(storage_state=None) -> (context, page)`. Everything is closed on exit."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        channel = headless_channel() if headless else None
        browser = pw.chromium.launch(headless=headless, channel=channel)
        contexts = []

        def new_page(storage_state=None):
            context = browser.new_context(viewport=VIEWPORT, device_scale_factor=1, ignore_https_errors=True, storage_state=storage_state)
            contexts.append(context)
            return context, context.new_page()

        try:
            yield new_page
        finally:
            for context in contexts:
                try:
                    context.close()
                except Exception:
                    pass
            browser.close()
