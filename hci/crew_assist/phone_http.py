#!/usr/bin/env python3
"""LAN phone UI and JSON API for crew assist.

Bind address defaults to all interfaces so a phone on the same network can
open it. There is no tunnel and no public host. Commands require the pairing
code printed at startup.
"""
from __future__ import annotations

import json
import mimetypes
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import urlparse

import astrobee_cmd as ac
import hci_config
from backend_factory import open_backend
from intent_parser import load_locations
from phone_control import FallbackBackend, PhoneController, Ros1RelayStop, make_session

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.normpath(os.path.join(HERE, "..", "phone", "static"))
MAX_BODY = 6_000_000


class PhoneServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, controller: PhoneController, static_dir: str = STATIC):
        super().__init__(address, PhoneHandler)
        self.ctrl = controller
        self.static_dir = static_dir


class PhoneHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        return

    @property
    def ctrl(self) -> PhoneController:
        return self.server.ctrl

    def do_GET(self):  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/status":
            self._status()
            return
        if path == "/api/camera.png":
            self._camera()
            return
        if path == "/api/health":
            self._json(200, {"ok": True})
            return
        if path == "/api/map":
            self._map()
            return
        self._static(path)

    def do_POST(self):  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/pair":
            self._pair()
            return
        client = self._client()
        if client is None:
            return
        if path == "/api/release":
            self.ctrl.release(client)
            self._json(200, {"ok": True})
            return
        if path == "/api/stop":
            body = self._body()
            t_tap = body.get("t_tap")
            self._json(200, self.ctrl.stop(t_tap=t_tap))
            return
        if self.ctrl.role_of(client) != "controller":
            self._json(403, {"error": "view only"})
            return
        if path == "/api/preview":
            self._preview(client)
            return
        if path == "/api/preview_point":
            self._preview_point(client)
            return
        if path == "/api/confirm":
            self._confirm(client)
            return
        if path == "/api/cancel":
            self.ctrl.cancel_preview(client, str(self._body().get("preview_id", "")))
            self._json(200, {"ok": True})
            return
        if path == "/api/voice":
            self._voice(client)
            return
        self._json(404, {"error": "not found"})

    def _client(self) -> Optional[str]:
        code = self.headers.get("X-Pairing-Code") or self._cookie("hci_code")
        if not self.ctrl.check_code(code or ""):
            self._json(401, {"error": "bad pairing code"})
            return None
        return self.headers.get("X-Client-Id") or self._cookie("hci_client") or ""

    def _cookie(self, name: str) -> str:
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            if "=" not in part:
                continue
            key, value = part.strip().split("=", 1)
            if key == name:
                return value
        return ""

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            return {}
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        ctype = self.headers.get("Content-Type") or ""
        if "application/json" in ctype:
            try:
                return json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                return {}
        return {"raw": raw}

    def _json(self, code: int, payload: dict, cookies=None) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for cookie in cookies or []:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(data)

    def _pair(self) -> None:
        body = self._body()
        code = str(body.get("code") or self.headers.get("X-Pairing-Code") or "")
        if not self.ctrl.check_code(code):
            self._json(401, {"error": "bad pairing code"})
            return
        try:
            claimed = self.ctrl.claim()
        except PermissionError as exc:
            client_id = secrets.token_hex(8)
            self._json(
                200,
                {"client_id": client_id, "role": "viewer", "detail": str(exc)},
                cookies=_cookies(code, client_id),
            )
            return
        self._json(200, claimed, cookies=_cookies(code, claimed["client_id"]))

    def _status(self) -> None:
        client = self._client()
        if client is None:
            return
        payload = self.ctrl.status(client)
        payload["lines"] = list(getattr(self.ctrl, "echoes", []))[-12:]
        self._json(200, payload)

    def _map(self) -> None:
        client = self._client()
        if client is None:
            return
        import point_fly

        self._json(200, point_fly.jem_overlay())

    def _preview(self, client: str) -> None:
        text = str(self._body().get("text") or "")
        try:
            self._json(200, self.ctrl.preview(client, text))
        except PermissionError as exc:
            self._json(403, {"error": str(exc)})
        except ValueError as exc:
            self._json(400, {"error": str(exc)})

    def _preview_point(self, client: str) -> None:
        try:
            self._json(200, self.ctrl.preview_point(client, self._body()))
        except PermissionError as exc:
            self._json(403, {"error": str(exc)})
        except ValueError as exc:
            self._json(400, {"error": str(exc)})

    def _confirm(self, client: str) -> None:
        preview_id = str(self._body().get("preview_id") or "")
        try:
            self._json(200, self.ctrl.confirm(client, preview_id))
        except PermissionError as exc:
            self._json(403, {"error": str(exc)})
        except KeyError:
            self._json(404, {"error": "preview expired"})

    def _voice(self, client: str) -> None:
        body = self._body()
        wav = body.get("raw")
        if not isinstance(wav, (bytes, bytearray)):
            self._json(400, {"error": "send a mono 16-bit WAV body"})
            return
        heard = recognize_wav_bytes(bytes(wav))
        if heard is None:
            self._json(503, {"error": "voice model is not installed on this server"})
            return
        ok, why = heard
        if not ok:
            self._json(422, {"error": why})
            return
        try:
            self._json(200, self.ctrl.preview(client, why))
        except ValueError as exc:
            self._json(400, {"error": str(exc)})

    def _camera(self) -> None:
        client = self._client()
        if client is None:
            return
        png = self.ctrl.camera_png
        if not png:
            png = ac.png_bytes(
                8, 8, "mono8", 8, bytes((i * 30) % 256 for i in range(64))
            )
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(png)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(png)

    def _static(self, path: str) -> None:
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        if ".." in rel.split("/"):
            self._json(404, {"error": "not found"})
            return
        full = os.path.join(self.server.static_dir, rel)
        if not os.path.isfile(full):
            self._json(404, {"error": "not found"})
            return
        mime = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as handle:
            data = handle.read()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _cookies(code: str, client_id: str):
    return [
        "hci_code=%s; Path=/; SameSite=Lax" % code,
        "hci_client=%s; Path=/; SameSite=Lax" % client_id,
    ]


def recognize_wav_bytes(wav: bytes):
    """Return (True, phrase) or (False, reason), or None if Vosk is unavailable."""
    model = hci_config.vosk_model(
        os.path.join(HERE, "..", "models", "vosk-model-small-en-us-0.15")
    )
    if not os.path.isdir(model):
        return None
    try:
        from voice_input import VoiceRecognizer
    except Exception:
        return None
    import tempfile

    from intent_parser import load_locations as _load

    path = os.path.join(tempfile.gettempdir(), "hci-ptt-%s.wav" % secrets.token_hex(4))
    try:
        with open(path, "wb") as handle:
            handle.write(wav)
        recognizer = recognize_wav_bytes.recognizer
        if recognizer is None:
            locs = _load(os.path.join(HERE, "locations.yaml"))
            recognize_wav_bytes.recognizer = VoiceRecognizer(model, locs, min_conf=0.6)
            recognizer = recognize_wav_bytes.recognizer
        heard = recognizer.recognize_wav(path)
        ok, why = recognizer.accept(heard)
        if not ok:
            return False, why
        return True, heard.text
    except Exception as exc:
        return False, str(exc)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


recognize_wav_bytes.recognizer = None


def build_backend(mode: str, mock_move_time: float = 0.0):
    if mode == "mock":
        return open_backend("mock", mock_move_time=mock_move_time), None
    if mode == "ros2":
        primary = open_backend("ros2")
        return FallbackBackend(primary, Ros1RelayStop()), primary
    backend = open_backend(
        "ros1", node_name="hci_phone", disable_signals=False, anonymous=False
    )
    return backend, backend


def _camera_loop(ctrl: PhoneController, stop: threading.Event) -> None:
    while not stop.wait(0.4):
        try:
            import rospy
            from sensor_msgs.msg import Image
        except Exception:
            return
        if not rospy.core.is_initialized():
            return

        def on_image(msg, ctrl=ctrl):
            try:
                ctrl.camera_png = ac.png_bytes(
                    msg.width,
                    msg.height,
                    msg.encoding,
                    msg.step,
                    bytes(msg.data),
                )
            except Exception:
                return

        rospy.Subscriber("hw/cam_nav", Image, on_image, queue_size=1)
        return


def serve(controller: PhoneController, host: str, port: int) -> PhoneServer:
    server = PhoneServer((host, port), controller)
    thread = threading.Thread(
        target=server.serve_forever, name="phone-http", daemon=True
    )
    thread.start()
    return server


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend",
        choices=("ros1", "ros2", "mock"),
        default=hci_config.web_backend(),
    )
    parser.add_argument("--host", default=hci_config.web_bind())
    parser.add_argument("--port", type=int, default=hci_config.web_port())
    parser.add_argument("--code", default=hci_config.pairing_code())
    parser.add_argument("--heartbeat", type=float, default=hci_config.heartbeat_s())
    parser.add_argument("--mock-move-time", type=float, default=0.0)
    parser.add_argument("--locations", default=os.path.join(HERE, "locations.yaml"))
    args = parser.parse_args(argv)

    locations = load_locations(args.locations)
    backend, _primary = build_backend(args.backend, args.mock_move_time)
    intent_parser, session, echoes = make_session(
        backend,
        locations,
        image_dir=hci_config.image_dir(os.path.join(HERE, "images")),
    )
    ctrl = PhoneController(
        session, intent_parser, backend, code=args.code, heartbeat_s=args.heartbeat
    )
    ctrl.echoes = echoes
    stop_cam = threading.Event()
    if args.backend != "mock":
        threading.Thread(
            target=_camera_loop, args=(ctrl, stop_cam), daemon=True
        ).start()
    server = PhoneServer((args.host, args.port), ctrl)
    log_dir = hci_config.log_dir(os.path.join(HERE, "..", "logs"))
    try:
        os.makedirs(log_dir, exist_ok=True)
        with open(os.path.join(log_dir, "phone_pairing.txt"), "w") as handle:
            handle.write(ctrl.code + "\n")
    except OSError:
        pass
    print("hci phone: http://%s:%d" % (args.host, args.port), flush=True)
    print("hci phone pairing code: %s" % ctrl.code, flush=True)
    print(
        "hci phone: one controller; STOP skips read-back; dead-man %.1fs"
        % args.heartbeat,
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_cam.set()
        server.shutdown()
        ctrl.close()
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
