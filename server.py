"""Server FrameCast — streamer layar (host).

Jalankan di mesin yang layarnya mau di-share:
    python server.py --fps 120 --quality 75

Pipeline:
    thread capture -> simpan frame TERBARU saja (frame basi di-drop)
    task asyncio   -> encode (di thread eksekutor) -> broadcast ke semua client
    client lambat  -> frame-nya di-drop, tidak menahan client lain

Kenapa drop, bukan antre panjang? Untuk latency rendah, data basi lebih buruk
daripada frame hilang — client harus selalu lihat kondisi layar TERBARU.
"""

import argparse
import asyncio
import json
import threading
import time

import websockets

import input as input_inject
from capture import MssCapture, SyntheticCapture
from encoder import JpegEncoder
from protocol import TYPE_DELTA, TYPE_KEYFRAME, pack_frame


class FrameSource:
    """Thread penangkap layar. Hanya menyimpan frame termutakhir."""

    def __init__(self, capture, fps):
        self.capture = capture
        self.interval = 1.0 / fps
        self._frame = None
        self._frame_id = 0
        self._lock = threading.Lock()
        self.event = threading.Event()
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="capture")

    def start(self):
        self._thread.start()

    def stop(self):
        self._running = False
        self.event.set()

    def _loop(self):
        while self._running:
            t0 = time.perf_counter()
            frame = self.capture.grab()
            with self._lock:
                self._frame = frame
                self._frame_id += 1
            self.event.set()
            # pacing: tidur sisa waktu supaya tidak lempar lebih cepat dari
            # target fps. Kalau mesin lebih lambat -> serap apa adanya.
            elapsed = time.perf_counter() - t0
            sleep = self.interval - elapsed
            if sleep > 0:
                time.sleep(sleep)

    def latest(self):
        """Ambil frame terbaru: (frame_id, frame) atau None. Reset event."""
        with self._lock:
            if self._frame is None:
                return None
            self.event.clear()
            return self._frame_id, self._frame


class FrameCastServer:
    def __init__(self, capture, encoder, fps, keyframe_interval=120):
        self.source = FrameSource(capture, fps)
        self.encoder = encoder
        self.keyframe_interval = keyframe_interval
        self.clients = {}  # ws -> antrean keluar per client
        self.stats = {"frames_encoded": 0, "drops": 0}

    async def run(self, host, port):
        self.source.start()
        print(
            f"[server] capture={self.source.capture.name} "
            f"{self.source.capture.width}x{self.source.capture.height}, "
            f"fps target={1 / self.source.interval:.0f}, codec={self.encoder.__class__.__name__}"
        )
        print(f"[server] listen ws://{host}:{port}  (Ctrl+C untuk berhenti)")
        async with websockets.serve(
            self.handle,
            host,
            port,
            max_size=64 << 20,   # frame JPEG bisa besar
            compression=None,    # matikan permessage-deflate: hemat latency & CPU
        ):
            await asyncio.gather(self.pump(), asyncio.Future())

    async def handle(self, ws):
        """Satu koneksi client: kirim hello, terima input, layani frame."""
        hello = {
            "type": "hello",
            "protocol": "FC01",
            "width": self.source.capture.width,
            "height": self.source.capture.height,
        }
        out_q = asyncio.Queue(maxsize=2)  # buffer kecil: 2 frame cukup, sisanya drop
        writer = asyncio.create_task(self._writer(ws, out_q))
        self.clients[ws] = out_q
        try:
            await ws.send(json.dumps(hello))
            async for raw in ws:
                if isinstance(raw, str):  # pesan kontrol (input) -> teks
                    self._on_control(json.loads(raw))
        except Exception:
            pass
        finally:
            self.clients.pop(ws, None)
            writer.cancel()

    async def _writer(self, ws, out_q):
        """Kirim frame dari antrean client. Mati kalau koneksi rusak."""
        try:
            while True:
                msg = await out_q.get()
                await ws.send(msg)
        except Exception:
            pass

    def _on_control(self, msg):
        if msg.get("type") == "input":
            input_inject.inject(msg)

    async def pump(self):
        """Encode frame terbaru sekali, broadcast ke semua client."""
        while True:
            # tunggu frame baru (di thread, biar event loop tetap responsif)
            await asyncio.to_thread(self.source.event.wait)
            item = self.source.latest()
            if item is None:
                continue
            fid, frame = item
            keyframe = fid % self.keyframe_interval == 0
            payload = await asyncio.to_thread(self.encoder.encode, frame, keyframe)
            msg = pack_frame(
                fid,
                TYPE_KEYFRAME if keyframe else TYPE_DELTA,
                self.encoder.codec_id,
                frame.shape[1],  # width
                frame.shape[0],  # height
                self.encoder.quality,
                0,
                payload,
            )
            self.stats["frames_encoded"] += 1
            if self.stats["frames_encoded"] % 300 == 0:
                print(
                    f"[server] {self.stats['frames_encoded']} frame dikirim, "
                    f"client={len(self.clients)}, drop(client lambat)={self.stats['drops']}"
                )
            for ws, q in list(self.clients.items()):
                try:
                    q.put_nowait(msg)
                except asyncio.QueueFull:
                    self.stats["drops"] += 1  # client lambat -> drop frame


def parse_size(s):
    w, h = s.lower().split("x")
    return int(w), int(h)


def main():
    ap = argparse.ArgumentParser(description="FrameCast server (streamer layar)")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=9000)
    ap.add_argument("--fps", type=int, default=60, help="target fps (coba 120 di mesin kencang)")
    ap.add_argument("--quality", type=int, default=80, help="kualitas JPEG 1-100")
    ap.add_argument("--capture", choices=["mss", "synthetic"], default="mss")
    ap.add_argument("--size", default=None, help="WxH untuk synthetic, mis. 1280x720")
    ap.add_argument("--monitor", type=int, default=1, help="nomor monitor (mss)")
    ap.add_argument("--region", default=None, help="left,top,width,height (mss)")
    ap.add_argument("--keyframe-interval", type=int, default=120)
    args = ap.parse_args()

    if args.capture == "synthetic":
        w, h = parse_size(args.size or "1280x720")
        capture = SyntheticCapture(w, h)
    else:
        region = None
        if args.region:
            l, t, w, h = map(int, args.region.split(","))
            region = {"left": l, "top": t, "width": w, "height": h}
        capture = MssCapture(region=region, monitor_index=args.monitor)

    encoder = JpegEncoder(quality=args.quality)
    server = FrameCastServer(
        capture, encoder, fps=args.fps, keyframe_interval=args.keyframe_interval
    )
    try:
        asyncio.run(server.run(args.host, args.port))
    except KeyboardInterrupt:
        server.source.stop()
        print("\n[server] berhenti.")


if __name__ == "__main__":
    main()
