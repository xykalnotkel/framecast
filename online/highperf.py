#!/usr/bin/env python3
"""HIGH-PERF: capture DXGI + encoder NVENC (Windows + GPU).

Jalur ke 120+ fps (lihat docs/ARCHITECTURE.md). Modul ini cuma jalan
di Windows dengan GPU (NVIDIA buat NVENC; AMD pakai AMF, Intel pakai QSV).

  - DxgiCapture   : DXGI Desktop Duplication via `dxcam` (zero-copy GPU,
                    dirty-rect, jauh lebih cepat dari mss/GDI)
  - NvencEncoder  : H.264 hardware via FFmpeg `h264_nvenc` (PyAV),
                    preset p1 + zerolatency + CQP
  - bench()       : ukur capture+encode fps (mis. 1080p / 1440p)

Contoh benchmark (di Windows + GPU):
  python highperf.py --bench --size 1920x1080 --fps 120

Catatan: kalau NVIDIA driver / encoder nggak ada, NvencEncoder otomatis
fallback ke x264 software (tetap jalan, tapi CPU-heavy).
"""

import argparse
import time


# ============================ CAPTURE (DXGI) ============================
class DxgiCapture:
    """DXGI Desktop Duplication via dxcam — capture GPU, cepat & murah CPU."""

    name = "dxgi"

    def __init__(self, region=None, output_idx=0):
        import dxcam  # pip install dxcam (Windows only)

        self.cam = dxcam.create(output_idx=output_idx)
        if region:
            self._region = tuple(region)  # (left, top, right, bottom)
            self.cam.start(region=self._region, video_mode=False)
        else:
            self._region = None
            self.cam.start(video_mode=False)
        self.frame_index = 0

    @property
    def width(self):
        return self.cam.width if self._region is None else self._region[2] - self._region[0]

    @property
    def height(self):
        return self.cam.height if self._region is None else self._region[3] - self._region[1]

    def grab(self):
        # dxcam.get_latest_frame() balikin frame TERBARU (drop yang basi)
        frame = self.cam.get_latest_frame()
        self.frame_index += 1
        return frame

    def stop(self):
        self.cam.stop()


# ============================ ENCODER (NVENC) ============================
class NvencEncoder:
    """H.264 hardware encoder via FFmpeg h264_nvenc (PyAV). Low-latency preset."""

    codec_id = 1  # H.264

    def __init__(self, width, height, fps=120, bitrate_kbps=20000, cq=22):
        import av

        self.av = av
        self.width = width
        self.height = height
        self.fps = fps
        self.bitrate = bitrate_kbps * 1000
        try:
            codec = av.Codec("h264_nvenc", "w")
        except Exception:
            print("[nvenc] h264_nvenc tidak tersedia — fallback ke x264 software")
            codec = av.Codec("h264", "w")
        self.codec = codec
        self._fps = fps

    def _make_stream(self):
        stream = self.codec.create()
        stream.width = self.width
        stream.height = self.height
        stream.pix_fmt = "yuv420p"
        stream.framerate = self.fps
        stream.time_base = (1, self.fps)
        # low latency + constant quality (CQP) — bukan CBR (remote desktop)
        stream.options = {
            "preset": "p1",        # NVENC: paling cepat, paling rendah latency
            "tune": "ull",         # ultra low latency
            "rc": "cqp",           # constant quantizer
            "cq": str(self.cq) if hasattr(self, "cq") else "22",
            "bf": "0",             # tanpa B-frame -> minim delay
            "zerolatency": "1",
        }
        return stream

    def encode(self, frame_bgra, keyframe=False):
        """frame_bgra (H,W,4) BGRA -> bytes H.264 (Annex-B)."""
        import numpy as np

        rgb = np.ascontiguousarray(frame_bgra[..., [2, 1, 0]])
        yuv = self.av.VideoFrame.from_ndarray(rgb, format="rgb24").reformat(
            width=self.width, height=self.height, format="yuv420p"
        )
        if keyframe:
            yuv.pts = None  # paksa encoder bikin keyframe (IDR)
        packets = self.codec.encode(yuv)
        out = b""
        for pkt in packets:
            out += bytes(pkt)
        return out

    def flush(self):
        return b"".join(bytes(p) for p in self.codec.encode(None))


# ============================ BENCHMARK ============================
def bench(size, fps, seconds=5):
    """Ukur capture (dxgi) + encode (nvenc) sampai berapa fps realistis."""
    cap = DxgiCapture()
    enc = NvencEncoder(size[0], size[1], fps=fps)
    print(f"[bench] {size[0]}x{size[1]} target {fps} fps, {seconds} detik")
    n = 0
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < seconds:
        frame = cap.grab()
        if frame is None:
            continue
        data = enc.encode(frame)
        n += 1
    dt = time.perf_counter() - t0
    print(f"[bench] capture+encode: {n / dt:.1f} fps ({n} frame / {dt:.2f}s)")
    cap.stop()


def main():
    ap = argparse.ArgumentParser(description="FrameCast high-perf (DXGI+NVENC)")
    ap.add_argument("--bench", action="store_true", help="jalanin benchmark")
    ap.add_argument("--size", default="1920x1080", help="WxH")
    ap.add_argument("--fps", type=int, default=120)
    args = ap.parse_args()
    if args.bench:
        w, h = map(int, args.size.lower().split("x"))
        bench((w, h), args.fps)
    else:
        print("Gunakan: python highperf.py --bench --size 1920x1080 --fps 120")
        print("(jalankan di Windows + GPU NVIDIA/AMD/Intel)")


if __name__ == "__main__":
    main()
