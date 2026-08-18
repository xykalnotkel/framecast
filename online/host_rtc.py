#!/usr/bin/env python3
"""HOST FrameCast Online — jalankan di PC yang layarnya mau di-remote.

  python host_rtc.py                      # ID & PIN dibuat otomatis (ala AnyDesk)
  python host_rtc.py --pin 123456 --name "PC-Kantor"

Alur:
  1. daftar ke server signaling  ->  dapat ID 9 digit + PIN 6 digit
  2. client connect pakai ID+PIN (via backend gratis, Web/Android/Windows)
  3. WebRTC P2P: host kirim track video layar + terima input via DataChannel

Jalur media & input 100% P2P (host<->client langsung), backend cuma pertemukan.
Host melayani banyak client BERGANTIAN: tiap client_joined -> PeerConnection
baru (RTCPeerConnection tidak bisa dipakai ulang setelah closed).

Catatan produksi: capture mss (GDI) cukup untuk tes; 120fps pakai DXGI +
NVENC (lihat docs/ARCHITECTURE.md). Bungkus jadi .exe dengan PyInstaller:
  pyinstaller --onefile --name FrameCastHost online/host_rtc.py
"""

import argparse
import asyncio
import json
import sys
import time
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # root framecast/

import numpy as np
import websockets
from aiortc import (
    RTCIceCandidate,
    RTCPeerConnection,
    RTCSessionDescription,
    VideoStreamTrack,
)
from aiortc.mediastreams import VideoFrame

from capture import MssCapture, SyntheticCapture
from input import inject as input_inject
from msgproto import format_id, make_host_id, make_pin, new_salt, pin_hash

CLOCK_RATE = 90000  # timebase standar WebRTC


def prefer_h264(pc):
    """Beri tahu aiortc: prefer H.264 daripada VP8 (bitrate jauh lebih hemat).
    Best-effort — kalau API nggak tersedia, biarkan default."""
    try:
        from aiortc.rtcrtpparameters import RTCRtpCodecParameters
        from aiortc.rtcrtpsender import RTCRtpSender

        caps = RTCRtpSender.getCapabilities("video")
        h264 = [c for c in caps.codecs if c.mimeType.lower() == "video/h264" and c.parameters.get("profile-level-id") != "42001f"]
        if not h264:
            h264 = [c for c in caps.codecs if c.mimeType.lower() == "video/h264"]
        if h264:
            for t in pc.getTransceivers():
                t.setCodecPreferences(h264[:2])
            print("[video] codec preference: H.264")
    except Exception as e:
        print(f"[video] prefer_h264 skipped: {e}")


class ScreenTrack(VideoStreamTrack):
    """VideoStreamTrack yang menyuntikkan frame layar ke WebRTC."""

    def __init__(self, capture, fps=60):
        super().__init__()
        self.cap = capture
        self.interval = 1.0 / fps
        self._last = 0.0
        self.kind = "video"

    async def recv(self):
        while True:
            now = time.time()
            wait = self.interval - (now - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.time()

            bgra = await asyncio.to_thread(self.cap.grab)  # (H,W,4) BGRA
            rgb = np.ascontiguousarray(bgra[..., [2, 1, 0]])  # -> (H,W,3) RGB
            frame = VideoFrame.from_ndarray(rgb, format="rgb24")
            frame.pts = int(self._last * CLOCK_RATE)
            frame.time_base = Fraction(1, CLOCK_RATE)
            return frame


class Host:
    def __init__(self, args):
        self.args = args
        self.host_id = args.host_id or make_host_id()
        self.client_id = None
        self.pc = None
        self.dc = None
        self.ws = None
        self.account_token = None
        self.account_email = None

    # ---------- login akun (opsional) ----------
    async def login_account(self):
        """Login dengan akun (email+password) -> token. Host terdaftar sebagai
        device milik akun; client yang login akun sama bisa connect tanpa PIN."""
        if self.args.account_token:
            self.account_token = self.args.account_token
        elif self.args.account_email and self.args.account_password:
            import urllib.request

            api = self.args.signaling.replace("ws://", "http://").replace("wss://", "https://")
            api = api.split("/ws")[0]
            req = urllib.request.Request(
                f"{api}/api/login",
                data=json.dumps({
                    "email": self.args.account_email,
                    "password": self.args.account_password,
                    "device": {
                        "type": self.args.device_type,
                        "model": self.args.model,
                        "platform": sys.platform,
                        "name": self.args.name,
                    },
                }).encode(),
                headers={"Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(req, timeout=15) as r:
                    data = json.loads(r.read())
                self.account_token = data["token"]
                self.account_email = data.get("email")
                print(f"[akun] login OK: {self.account_email} (plan {data.get('plan', '?')})")
            except Exception as e:
                print(f"[akun] login GAGAL ({e}) — lanjut mode PIN saja")
                self.account_token = None

    # ---------- input dari client ----------
    def on_input(self, raw):
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        if msg.get("type") != "input":
            return
        seq = msg.get("seq")
        if seq is not None and self.dc is not None:
            try:
                self.dc.send(json.dumps({"type": "echo", "seq": seq}))
            except Exception:
                pass
        ok = input_inject(msg)
        if not ok and self.args.verbose:
            print(f"[input] {msg.get('action')} (stub/platform)")

    # ---------- PeerConnection per sesi client ----------
    async def setup_pc(self):
        """Buat RTCPeerConnection BARU (tutup yang lama kalau ada).
        Dipanggil tiap kali ada client_joined baru."""
        if self.pc is not None:
            try:
                await self.pc.close()
            except Exception:
                pass
            print("[p2p] sesi sebelumnya ditutup, siap untuk client baru")

        pc = RTCPeerConnection()
        self.pc = pc
        if self.args.codec == "h264":
            prefer_h264(pc)
        track = ScreenTrack(self.args.capture, self.args.fps)
        pc.addTrack(track)

        self.dc = pc.createDataChannel("input", negotiated=True, id=0)
        self.dc.on("message")(self.on_input)

        @pc.on("icecandidate")
        async def on_ice(candidate):
            if candidate is None or self.client_id is None:
                return
            await self.signal({
                "type": "candidate",
                "candidate": candidate.candidate,
                "sdpMid": candidate.sdpMid,
                "sdpMLineIndex": candidate.sdpMLineIndex,
            })

        @pc.on("connectionstatechange")
        def on_state():
            print(f"[p2p] state: {pc.connectionState}")
            if pc.connectionState in ("failed", "closed"):
                print("[p2p] koneksi selesai — host siap menerima client baru.")

    # ---------- signaling ----------
    async def send(self, obj):
        await self.ws.send(json.dumps(obj))

    async def signal(self, payload):
        await self.send({"type": "signal", "to": self.client_id, "payload": payload})

    async def run(self):
        salt = new_salt()
        pin = self.args.pin or make_pin()
        ph = pin_hash(pin, salt)
        if self.args.device_type == "phone":
            pin = ""  # HP remote tidak pakai PIN — murni akun + premium

        print("=" * 46)
        print("  FrameCast Online — HOST")
        print(f"  ID   : {format_id(self.host_id)}")
        print(f"  Tipe : {self.args.device_type.upper()}  ({self.args.model or 'auto'})")
        if self.args.device_type == "pc":
            print(f"  PIN  : {pin}   (berlaku selama sesi ini — akses GRATIS)")
        else:
            print(f"  PIN  : -   (HP remote = login akun PREMIUM, tanpa PIN)")
        print(f"  Nama : {self.args.name}")
        print("=" * 46)

        # login akun (opsional; wajib buat host HP)
        await self.login_account()

        async with websockets.connect(
            f"{self.args.signaling}?host={self.host_id}",
            max_size=1 << 20,
            compression=None,
        ) as ws:
            self.ws = ws
            await self.send({
                "type": "host_register",
                "host_id": self.host_id,
                "name": self.args.name,
                "platform": sys.platform,
                "pin_hash": ph,
                "salt": salt,
                "plan": self.args.plan,
                "device_type": self.args.device_type,
                "model": self.args.model,
                "account_token": self.account_token,
            })
            reply = json.loads(await ws.recv())
            if reply.get("type") != "registered":
                print("Gagal daftar:", reply)
                return 1
            print(f"[signaling] terdaftar. Tunggu client connect dengan ID+PIN "
                  f"(PC, gratis) atau akun sama (HP, premium)...")

            async for raw in ws:
                msg = json.loads(raw)
                t = msg.get("type")
                if t == "client_joined":
                    # client baru -> siapkan koneksi P2P baru
                    await self.setup_pc()
                    self.client_id = msg["client_id"]
                    print(f"[signaling] client {self.client_id} minta koneksi "
                          f"(P2P lewat NAT; STUN/TURN diurus WebRTC)")
                elif t == "signal":
                    await self.handle_signal(msg["payload"])
                elif t == "bye":
                    print("[signaling] host digantikan sesi lain. keluar.")
                    break

        if self.pc is not None:
            await self.pc.close()
        return 0

    async def handle_signal(self, payload):
        ptype = payload.get("type")
        if ptype == "offer":
            await self.pc.setRemoteDescription(
                RTCSessionDescription(sdp=payload["sdp"], type="offer")
            )
            answer = await self.pc.createAnswer()
            await self.pc.setLocalDescription(answer)
            # kandidat ICE baru ada setelah setLocalDescription ->
            # wajib kirim pc.localDescription.sdp (bukan answer.sdp)
            await self.signal({"type": "answer", "sdp": self.pc.localDescription.sdp})
        elif ptype == "candidate":
            await self.pc.addIceCandidate(
                RTCIceCandidate(
                    candidate=payload["candidate"],
                    sdpMid=payload.get("sdpMid"),
                    sdpMLineIndex=payload.get("sdpMLineIndex"),
                )
            )


def main():
    ap = argparse.ArgumentParser(description="Host FrameCast Online (WebRTC)")
    ap.add_argument("--signaling", default="ws://127.0.0.1:9010",
                    help="URL server signaling (lokal: ws://host:9010, "
                         "produksi: wss://<worker>.workers.dev/ws)")
    ap.add_argument("--host-id", default=None)
    ap.add_argument("--pin", default=None)
    ap.add_argument("--name", default="PC-Kantor")
    ap.add_argument("--device-type", choices=["pc", "phone"], default="pc",
                    help="pc = remote GRATIS (ID+PIN). phone = remote PREMIUM "
                         "(login akun sama, tanpa PIN).")
    ap.add_argument("--model", default="", help="nama model device (mis. HP: 'Samsung SM-A525F')")
    ap.add_argument("--plan", choices=["free", "premium"], default="free",
                    help="premium = client dapat TURN relay (Cloudflare) & fitur "
                         "high-perf. free = P2P saja (hosting biasa).")
    ap.add_argument("--account-email", default=None, help="login akun (opsional; wajib buat host phone)")
    ap.add_argument("--account-password", default=None, help="password akun")
    ap.add_argument("--account-token", default=None, help="token akun langsung (skip login)")
    ap.add_argument("--capture", choices=["mss", "synthetic", "dxgi"], default="mss",
                    help="dxgi = DXGI Desktop Duplication (Windows, high-perf, "
                         "120fps). mss = GDI (default). synthetic = frame tes.")
    ap.add_argument("--size", default=None, help="WxH buat synthetic, mis. 640x360")
    ap.add_argument("--fps", type=int, default=60)
    ap.add_argument("--codec", choices=["vp8", "h264"], default="vp8",
                    help="h264 = preferensi H.264 (bitrate hemat). vp8 = default.")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if args.capture == "synthetic":
        w, h = map(int, args.size.split("x")) if args.size else (640, 360)
        cap = SyntheticCapture(w, h)
    elif args.capture == "dxgi":
        try:
            from highperf import DxgiCapture
            cap = DxgiCapture()
            print("[capture] DXGI Desktop Duplication aktif (high-perf)")
        except Exception as e:
            print(f"[capture] dxgi tidak bisa dipakai ({e}) — fallback ke mss")
            cap = MssCapture()
    else:
        cap = MssCapture()
    args.capture = cap

    try:
        sys.exit(asyncio.run(Host(args).run()))
    except KeyboardInterrupt:
        print("\n[host] berhenti.")


if __name__ == "__main__":
    main()
