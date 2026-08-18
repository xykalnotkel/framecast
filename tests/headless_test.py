"""Tes end-to-end headless (tanpa layar/GPU).

Menjalankan server sungguhan (subprocess, capture sintetis) lalu client tes
menerima N frame, decode JPEG, dan mengukur fps / frame drop / waktu decode.

Cara pakai:
    python tests/headless_test.py [fps_target] [WxH]
    python tests/headless_test.py 60 1280x720
    python tests/headless_test.py 120 640x360
"""

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # harus SEBELUM import modul framecast lokal

import websockets

from encoder import decode_jpeg
from protocol import unpack_frame


async def test_client(port, n_frames=120, timeout=20):
    uri = f"ws://127.0.0.1:{port}"
    async with websockets.connect(uri, max_size=64 << 20, compression=None) as ws:
        hello = json.loads(await ws.recv())
        print(f"  hello: {hello}")
        assert hello["protocol"] == "FC01"
        assert hello["width"] > 0 and hello["height"] > 0

        ids = []
        dec_total = 0.0
        t0 = time.perf_counter()
        for _ in range(n_frames):
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            if isinstance(raw, str):
                continue
            f = unpack_frame(raw)
            assert f is not None, "frame tidak bisa di-parse"
            ids.append(f["frame_id"])
            t = time.perf_counter()
            img = decode_jpeg(f["payload"])
            dec_total += time.perf_counter() - t
            assert img.size == (f["width"], f["height"]), "ukuran frame tidak cocok"

        elapsed = time.perf_counter() - t0
        fps = (n_frames - 1) / elapsed if elapsed > 0 else 0
        gaps = [ids[i + 1] - ids[i] for i in range(len(ids) - 1) if ids[i + 1] - ids[i] > 1]
        print(
            f"  OK: {n_frames} frame dalam {elapsed:.2f}s -> {fps:.1f} fps terkirim, "
            f"gap frame (drop jaringan)={gaps[:5]}, "
            f"decode rata-rata={dec_total / n_frames * 1000:.2f} ms/frame"
        )
        return fps, len(gaps)


async def main():
    fps_target = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    size = sys.argv[2] if len(sys.argv) > 2 else "1280x720"
    port = 9911

    proc = subprocess.Popen(
        [
            sys.executable, str(ROOT / "server.py"),
            "--capture", "synthetic", "--size", size,
            "--fps", str(fps_target), "--quality", "70",
            "--port", str(port),
        ],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        # tunggu server siap (bisa connect & terima hello)
        ready = False
        for _ in range(100):
            try:
                async with websockets.connect(
                    f"ws://127.0.0.1:{port}", open_timeout=1, max_size=1 << 20
                ) as ws:
                    await ws.recv()
                ready = True
                break
            except Exception:
                await asyncio.sleep(0.1)
        if not ready:
            out = proc.stdout.read() if proc.stdout else ""
            raise RuntimeError(f"server gagal nyala:\n{out}")

        print(f"== tes fps_target={fps_target} size={size} ==")
        await test_client(port, n_frames=120)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    asyncio.run(main())
