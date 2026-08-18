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

        print("=" * 46)
        print("  FrameCast Online — HOST")
        print(f"  ID   : {format_id(self.host_id)}")
        print(f"  PIN  : {pin}   (berlaku selama sesi ini)")
        print(f"  Nama : {self.args.name}")
        print("=" * 46)

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
            })
            reply = json.loads(await ws.recv())
            if reply.get("type") != "registered":
                print("Gagal daftar:", reply)
                return 1
            print(f"[signaling] terdaftar. Tunggu client connect dengan ID+PIN di atas...")

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
    ap.add_argument("--capture", choices=["mss", "synthetic"], default="mss")
    ap.add_argument("--size", default=None, help="WxH buat synthetic, mis. 640x360")
    ap.add_argument("--fps", type=int, default=60)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if args.capture == "synthetic":
        w, h = map(int, args.size.split("x")) if args.size else (640, 360)
        cap = SyntheticCapture(w, h)
    else:
        cap = MssCapture()
    args.capture = cap

    try:
        sys.exit(asyncio.run(Host(args).run()))
    except KeyboardInterrupt:
        print("\n[host] berhenti.")


if __name__ == "__main__":
    main()
