"""Client FrameCast — viewer layar + pengirim input (pygame).

Jalankan di mesin penonton:
    python client.py --host 192.168.1.10 --port 9000

Alur:
    terima frame -> decode JPEG -> blit ke layar -> flip
    + kirim input lokal (mouse/keyboard) balik ke host via jalur teks terpisah.

Catatan performa:
  - pygame.display.set_mode tanpa vsync -> flip secepat frame datang
  - scaling (kalau ukuran jendela != ukuran frame) pakai pygame.transform,
    ganti dengan scaling GPU (lihat docs/ARCHITECTURE.md) untuk fullscreen 120fps
"""

import argparse
import asyncio
import json
import time

import numpy as np
import pygame
import websockets

from encoder import decode_jpeg
from protocol import unpack_frame


class Stats:
    """Statistik EMA sederhana: fps, jarak frame, frame drop, waktu decode."""

    def __init__(self):
        self.fps = 0.0
        self.frame_ms = 0.0
        self.decode_ms = 0.0
        self.dropped = 0
        self.frames = 0
        self._last_id = None
        self._t0 = time.perf_counter()

    def tick(self, frame_id, decode_ms):
        now = time.perf_counter()
        if self._last_id is not None:
            gap = frame_id - self._last_id - 1
            if gap > 0:
                self.dropped += gap
        self._last_id = frame_id
        dt = now - self._t0
        self._t0 = now
        if dt > 0:
            inst_fps = 1.0 / dt
            self.fps = 0.9 * self.fps + 0.1 * inst_fps
            self.frame_ms = 0.9 * self.frame_ms + 0.1 * (dt * 1000)
        self.decode_ms = 0.8 * self.decode_ms + 0.2 * decode_ms
        self.frames += 1


class Viewer:
    def __init__(self, uri, fullscreen=False, show_stats=True):
        self.uri = uri
        self.fullscreen = fullscreen
        self.show_stats = show_stats
        self.screen = None
        self.font = None
        self.stats = Stats()
        self.running = True

    def run(self):
        asyncio.run(self._main())

    async def _main(self):
        pygame.init()
        async with websockets.connect(
            self.uri, max_size=64 << 20, compression=None
        ) as ws:
            hello = json.loads(await ws.recv())
            w, h = hello["width"], hello["height"]
            if self.fullscreen:
                self.screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
            else:
                self.screen = pygame.display.set_mode((w, h))
            pygame.display.set_caption("FrameCast viewer")
            try:
                self.font = pygame.font.SysFont("consolas,menlo,monospace", 16)
            except Exception:
                self.font = pygame.font.Font(None, 20)

            in_q = asyncio.Queue()
            asyncio.create_task(self._input_sender(ws, in_q))
            print(f"[client] terhubung: {w}x{h} @ {self.uri}")

            while self.running:
                self._poll_input(in_q)
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5)
                except asyncio.TimeoutError:
                    print("[client] tidak ada data dari server...")
                    continue
                if isinstance(raw, str):
                    continue  # pesan kontrol dari server, abaikan
                frame = unpack_frame(raw)
                if frame is None:
                    continue
                t0 = time.perf_counter()
                img = decode_jpeg(frame["payload"])
                decode_ms = (time.perf_counter() - t0) * 1000
                self.stats.tick(frame["frame_id"], decode_ms)
                self._present(img, frame["width"], frame["height"])
                if self.show_stats:
                    self._draw_stats()
                pygame.display.flip()
        pygame.quit()

    # --- render -----------------------------------------------------------
    def _present(self, pil_img, fw, fh):
        rgb = np.asarray(pil_img)  # (H, W, 3) uint8
        surf = pygame.image.frombuffer(rgb.tobytes(), (fw, fh), "RGB")
        sw, sh = self.screen.get_size()
        if (sw, sh) != (fw, fh):
            surf = pygame.transform.scale(surf, (sw, sh))
        self.screen.blit(surf, (0, 0))

    def _draw_stats(self):
        s = self.stats
        lines = [
            f"fps {s.fps:5.1f}  frame {s.frame_ms:5.1f} ms  decode {s.decode_ms:4.1f} ms",
            f"frame ke-{s.frames}  drop jaringan {s.dropped}",
        ]
        y = 6
        for line in lines:
            surf = self.font.render(line, True, (0, 255, 0), (0, 0, 0))
            self.screen.blit(surf, (6, y))
            y += 18

    # --- input ------------------------------------------------------------
    def _poll_input(self, in_q):
        """Baca event pygame -> kirim ke host (koordinat dinormalisasi 0..1)."""
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                self.running = False
            elif ev.type == pygame.MOUSEMOTION:
                in_q.put_nowait({
                    "type": "input", "action": "mouse_move",
                    "x": ev.pos[0] / max(1, self.screen.get_width()),
                    "y": ev.pos[1] / max(1, self.screen.get_height()),
                })
            elif ev.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
                btn = {1: "left", 2: "middle", 3: "right"}.get(ev.button, "left")
                in_q.put_nowait({
                    "type": "input", "action": "mouse_button",
                    "button": btn, "down": ev.type == pygame.MOUSEBUTTONDOWN,
                })
            elif ev.type == pygame.MOUSEWHEEL:
                in_q.put_nowait({"type": "input", "action": "mouse_wheel", "delta": ev.y})
            elif ev.type in (pygame.KEYDOWN, pygame.KEYUP):
                in_q.put_nowait({
                    "type": "input", "action": "key",
                    "scancode": ev.key, "down": ev.type == pygame.KEYDOWN,
                    "unicode": ev.unicode or "",
                })

    async def _input_sender(self, ws, q):
        while True:
            ev = await q.get()
            try:
                await ws.send(json.dumps(ev))
            except Exception:
                break


def main():
    ap = argparse.ArgumentParser(description="FrameCast client (viewer)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9000)
    ap.add_argument("--fullscreen", action="store_true")
    ap.add_argument("--no-stats", action="store_true", help="sembunyikan overlay statistik")
    args = ap.parse_args()
    uri = f"ws://{args.host}:{args.port}"
    Viewer(uri, fullscreen=args.fullscreen, show_stats=not args.no_stats).run()


if __name__ == "__main__":
    main()
