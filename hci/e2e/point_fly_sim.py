#!/usr/bin/env python3
"""Measure point-and-fly in a running sim and save phone-sized screenshots.

The stack must already be up (./hci/up.sh). This undocks, flies three map
taps, rejects one keep-out tap, and writes JSON plus screenshots.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("HCI_PHONE_URL", "http://127.0.0.1:8090")
CODE_FILE = os.environ.get(
    "HCI_PAIRING_FILE",
    os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "logs", "phone_pairing.txt")
    ),
)
OUT = os.environ.get("HCI_ARTIFACTS", "/opt/cursor/artifacts")

# Fractions on the JEM map (see tests/test_point_fly.py). Three clear points
# and one keep-out. u grows +x, v grows toward -y.
TAPS = [
    ("rack-alpha", 0.674, 0.732),
    ("mid-jem", 0.651, 0.592),
    ("rack-charlie", 0.674, 0.223),
]
UNSAFE = ("keep-out", 0.070, 0.428)


def request(method, path, payload, headers):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(BASE + path, data=data, method=method)
    for key, value in headers.items():
        req.add_header(key, value)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def wait_idle(headers, timeout_s):
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        _status, body = request("GET", "/api/status", None, headers)
        last = body
        if not body.get("busy") and body.get("last_ok"):
            return body
        time.sleep(1.0)
    raise SystemExit("timed out waiting for idle: %s" % last)


def dist(a, b):
    return sum((float(a[i]) - float(b[i])) ** 2 for i in range(3)) ** 0.5


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    code = open(CODE_FILE).read().strip()
    status, paired = request("POST", "/api/pair", {"code": code}, {})
    if status != 200:
        raise SystemExit("pair failed %s %s" % (status, paired))
    headers = {
        "X-Pairing-Code": code,
        "X-Client-Id": paired["client_id"],
    }
    status, preview = request("POST", "/api/preview", {"text": "undock"}, headers)
    if status != 200:
        raise SystemExit("undock preview %s %s" % (status, preview))
    request("POST", "/api/confirm", {"preview_id": preview["preview_id"]}, headers)
    wait_idle(headers, 180)

    rows = []
    for name, u, v in TAPS:
        status, preview = request(
            "POST", "/api/preview_point", {"source": "map", "u": u, "v": v}, headers
        )
        if status != 200:
            rows.append({"name": name, "error": preview, "status": status})
            continue
        before = request("GET", "/api/status", None, headers)[1]
        request("POST", "/api/confirm", {"preview_id": preview["preview_id"]}, headers)
        after = wait_idle(headers, 240)
        pose = after.get("pose", {}).get("position")
        target = preview["target_xyz"]
        rows.append(
            {
                "name": name,
                "target": target,
                "pose": pose,
                "error_m": None if pose is None else dist(pose, target),
                "length_m": preview.get("length_m"),
                "waypoints": len(preview.get("path") or []),
                "before": before.get("pose", {}).get("position"),
                "last": after.get("last"),
            }
        )
        print(name, rows[-1]["error_m"], flush=True)

    pose_before = request("GET", "/api/status", None, headers)[1]["pose"]["position"]
    status, rejected = request(
        "POST",
        "/api/preview_point",
        {"source": "map", "u": UNSAFE[1], "v": UNSAFE[2]},
        headers,
    )
    time.sleep(1.0)
    pose_after = request("GET", "/api/status", None, headers)[1]["pose"]["position"]
    unsafe = {
        "status": status,
        "error": rejected.get("error"),
        "moved_m": dist(pose_before, pose_after),
    }
    print("unsafe", unsafe, flush=True)

    request("POST", "/api/release", {}, headers)
    time.sleep(0.3)
    shots = []
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 390, "height": 844})
            page.goto(BASE)
            page.fill("#code", code)
            page.click("#pair-form button")
            page.wait_for_selector("#app:not([hidden])")
            page.wait_for_selector("#map .keepout")
            page.screenshot(path=os.path.join(OUT, "point-fly-map.png"))
            shots.append("point-fly-map.png")
            page.evaluate(
                """() => {
                  const map = document.getElementById('map');
                  const rect = map.getBoundingClientRect();
                  map.dispatchEvent(new MouseEvent('click', {
                    clientX: rect.left + rect.width * 0.67,
                    clientY: rect.top + rect.height * 0.55,
                    bubbles: true
                  }));
                }"""
            )
            page.wait_for_selector("#readback:not([hidden])")
            page.screenshot(path=os.path.join(OUT, "point-fly-confirm.png"))
            shots.append("point-fly-confirm.png")
            page.click("#cancel")
            browser.close()
    except Exception as exc:
        shots.append("screenshots failed: %s" % exc)

    report = {"taps": rows, "unsafe": unsafe, "screenshots": shots}
    path = os.path.join(OUT, "point-fly-report.json")
    with open(path, "w") as handle:
        json.dump(report, handle, indent=2)
    print(json.dumps(report, indent=2), flush=True)
    errors = [row["error_m"] for row in rows if row.get("error_m") is not None]
    if len(errors) < 3 or status != 400 or unsafe["moved_m"] > 0.05:
        return 1
    if max(errors) > 0.5:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
