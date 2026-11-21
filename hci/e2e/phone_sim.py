#!/usr/bin/env python3
"""Headless phone-viewport run against a live sim.

Tap undock, confirm, fly to rack alpha, STOP mid-flight.
Saves screenshots under HCI_ARTIFACTS (default /opt/cursor/artifacts).
"""
from __future__ import annotations

import os
import sys
import time
import urllib.request

BASE = os.environ.get("HCI_PHONE_URL", "http://127.0.0.1:8090")
CODE_FILE = os.environ.get(
    "HCI_PAIRING_FILE",
    os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "logs", "phone_pairing.txt")
    ),
)
OUT = os.environ.get("HCI_ARTIFACTS", "/opt/cursor/artifacts")


def wait_health(timeout_s: float = 180.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(BASE + "/api/health", timeout=2) as resp:
                if resp.status == 200:
                    return
        except Exception:
            time.sleep(2)
    raise SystemExit("phone UI did not come up at %s" % BASE)


def code() -> str:
    text = open(CODE_FILE).read().strip()
    if not text:
        raise SystemExit("empty pairing file %s" % CODE_FILE)
    return text


def main() -> int:
    from playwright.sync_api import sync_playwright

    os.makedirs(OUT, exist_ok=True)
    wait_health()
    pairing = code()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.goto(BASE)
        page.fill("#code", pairing)
        page.click("#pair-form button")
        page.wait_for_selector("#app:not([hidden])")
        page.screenshot(path=os.path.join(OUT, "phone-home.png"))

        page.click("button[data-command='undock']")
        page.wait_for_selector("#readback:not([hidden])")
        page.click("#confirm")
        page.wait_for_function(
            "() => document.getElementById('status').textContent.indexOf('Idle') === 0"
            " && document.getElementById('status').textContent.indexOf('undock') >= 0",
            timeout=120000,
        )

        page.click("button[data-command='go to rack alpha']")
        page.wait_for_selector("#readback:not([hidden])")
        page.locator("#readback").scroll_into_view_if_needed()
        page.screenshot(path=os.path.join(OUT, "phone-confirm.png"))
        page.click("#confirm")
        page.wait_for_function(
            "() => document.getElementById('status').textContent.indexOf('Moving') === 0",
            timeout=30000,
        )
        page.screenshot(path=os.path.join(OUT, "phone-inflight.png"))
        time.sleep(2)
        with page.expect_response("**/api/stop") as caught:
            page.click("#stop")
        body = caught.value.json()
        page.wait_for_function(
            "() => document.getElementById('status').textContent.indexOf('STOP') >= 0",
            timeout=10000,
        )
        page.screenshot(path=os.path.join(OUT, "phone-stopped.png"))
        browser.close()
    print("stop response", body, flush=True)
    print(
        "tap_to_publish_ms",
        body.get("tap_to_publish_ms"),
        "server_to_publish_ms",
        body.get("server_to_publish_ms"),
        "ack",
        body.get("ack"),
        "via",
        body.get("via"),
        flush=True,
    )
    if body.get("ack") not in ("OK", "CANCELED", "EXEC_FAILED"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
