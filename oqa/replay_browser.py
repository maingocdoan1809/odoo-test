"""Run a visible replay in a browser owned by the replay process."""
import json
import os
import subprocess
from pathlib import Path

from . import browser, connect, workspace
from .secrets import SecretStoreUnavailable, default_store
from .state import GateError


def run(session, ws, initial, blocks, timings, video_dir):
    from playwright._impl._driver import compute_driver_executable

    node, cli = compute_driver_executable()
    accounts = {}
    store = default_store()
    profiles = {account["name"]: account for account in ws["profiles"]}
    for name in {initial, *(block["profile"] for block in blocks if block.get("profile"))}:
        if name not in profiles:
            raise GateError(f"Unknown workspace profile {name}.")
        try:
            password = store.get(workspace.secret_ref(ws["name"], name, ws["env"]))
        except SecretStoreUnavailable:
            password = None
        accounts[name] = {"login": profiles[name]["login"], "password": password,
                          "state": connect.storage_path(session, name) if ws["env"] == workspace.MAIN_ENV else None}
    config = {"module": str(Path(cli).parent), "url": ws["url"], "db": ws["db"],
              "initial": initial, "accounts": accounts, "blocks": blocks, **timings,
              "channel": browser.headless_channel(), "viewport": browser.VIEWPORT, "videoDir": str(video_dir) if video_dir else None}
    script = Path(__file__).with_name("web") / "replay_browser.cjs"
    child = subprocess.run([str(node), str(script)], input=json.dumps(config), capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        result = json.loads(child.stdout)
    except ValueError as error:
        raise GateError("The replay browser closed without reporting a result.") from error
    if result.get("status") not in ("PASS", "FAIL", "CANCELLED"):
        raise GateError("The replay browser returned an invalid result.")
    return result
