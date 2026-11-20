#!/usr/bin/env python3
"""Set nav_cam_rate in an Astrobee simulation.config without touching other keys.

The copy under astrobee/config in git is NASA's file and is not edited.
This runs inside the HCI container against the image's ASTROBEE_CONFIG_DIR.
"""
from __future__ import annotations

import re
import sys

_LINE = re.compile(r"^(?P<pre>\s*nav_cam_rate\s*=\s*)[0-9.]+;", re.M)
_RATE = re.compile(r"^[0-9]+(\.[0-9]+)?$")


def patch_text(text: str, rate: str) -> str:
    if not _RATE.match(rate):
        raise ValueError("rate must be a number, got %r" % (rate,))
    new, n = _LINE.subn(lambda m: "%s%s;" % (m.group("pre"), rate), text, count=1)
    if n != 1:
        raise ValueError("nav_cam_rate assignment not found")
    return new


def patch_file(path: str, rate: str) -> bool:
    with open(path) as handle:
        text = handle.read()
    updated = patch_text(text, rate)
    if updated == text:
        return False
    with open(path, "w") as handle:
        handle.write(updated)
    return True


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print("usage: patch_nav_cam.py <simulation.config> <rate>", file=sys.stderr)
        return 2
    try:
        changed = patch_file(args[0], args[1])
    except (OSError, ValueError) as exc:
        print("patch_nav_cam: %s" % exc, file=sys.stderr)
        return 1
    print("patch_nav_cam: %s rate=%s changed=%s" % (args[0], args[1], changed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
