"""Backend penangkap layar (pluggable).

Dua backend:
  - MssCapture        : tangkap layar sungguhan, cross-platform (lib `mss`)
  - SyntheticCapture  : hasilkan frame sintetis (buat tes headless / tanpa layar)

Antarmuka:
  capture.grab() -> ndarray uint8 shape (H, W, 4) urutan BGRA
  capture.width / capture.height / capture.region

CATATAN PENTING UNTUK 120+ FPS:
mss di Windows lewat GDI BitBlt — cukup untuk POC ini (~30-60 fps 1080p),
tapi BUKAN jalur 120 fps. Untuk 120+ fps ganti backend ini dengan
DXGI Desktop Duplication API (Windows) — detail lengkap di docs/ARCHITECTURE.md.
"""

import math
import time

import numpy as np


class BaseCapture:
    name = "base"

    @property
    def width(self):
        raise NotImplementedError

    @property
    def height(self):
        raise NotImplementedError

    @property
    def region(self):
        """Daerah layar yang ditangkap: (left, top, width, height)."""
        return (0, 0, self.width, self.height)

    def grab(self):
        """Ambil 1 frame. Wajib return array BARU tiap kali dipanggil
        (biar aman dibaca thread lain sementara thread ini ambil frame berikutnya)."""
        raise NotImplementedError


class MssCapture(BaseCapture):
    """Tangkap layar pakai `mss`.

    Wajib ada display (Windows/macOS/Linux desktop). Di Windows memakai GDI
    BitBlt — cepat untuk POC, tapi jalan ke 120 fps butuh DXGI (lihat docs).
    """

    name = "mss"

    def __init__(self, region=None, monitor_index=1):
        import mss

        self.sct = mss.mss()
        mon = self.sct.monitors[monitor_index]
        self._region = region or {
            "left": mon["left"],
            "top": mon["top"],
            "width": mon["width"],
            "height": mon["height"],
        }

    @property
    def width(self):
        return self._region["width"]

    @property
    def height(self):
        return self._region["height"]

    @property
    def region(self):
        r = self._region
        return (r["left"], r["top"], r["width"], r["height"])

    def grab(self):
        raw = self.sct.grab(self._region)
        return np.asarray(raw)  # BGRA, HxWx4


class SyntheticCapture(BaseCapture):
    """Frame buatan: gradasi bergerak + bola + jam digital.

    Guna:
      - tes pipeline end-to-end di mesin tanpa layar (headless)
      - ukur fps maksimum yang bisa dicapai pipeline sebelum nyentuh layar
      - jam digital di pojok bisa dipakai ngukur glass-to-glass latency
        (foto host + client bersamaan pakai kamera HP, bandingin jamnya)
    """

    name = "synthetic"

    def __init__(self, width=1280, height=720):
        self._w = width
        self._h = height
        self._t0 = time.perf_counter()
        self.frame_index = 0

    @property
    def width(self):
        return self._w

    @property
    def height(self):
        return self._h

    def grab(self):
        from PIL import Image, ImageDraw

        t = time.perf_counter() - self._t0
        self.frame_index += 1

        img = Image.new("RGB", (self._w, self._h))
        d = ImageDraw.Draw(img)

        # gradasi vertikal (berubah pelan biar encoder ada kerjaan)
        for y in range(0, self._h, 8):
            v = int(30 + 225 * y / max(1, self._h - 1))
            d.rectangle([0, y, self._w, min(self._h, y + 8)], fill=(v, v // 2, 255 - v))

        # bola merah bergerak (pola gerak buat uji kelancaran)
        r = 40
        cx = int((self._w - 2 * r) * (0.5 + 0.5 * math.sin(t * 2.2)))
        cy = int((self._h - 2 * r) * (0.5 + 0.5 * math.cos(t * 1.7)))
        d.ellipse([cx, cy, cx + 2 * r, cy + 2 * r], fill=(255, 90, 90), outline=(255, 255, 255))

        # nomor frame + jam (jam dipakai ngukur glass-to-glass latency)
        now_ms = f"{time.time() % 1 * 1000:03.0f}"
        d.text((10, 10), f"FrameCast synth #{self.frame_index}", fill=(255, 255, 255))
        d.text((10, 30), time.strftime("%H:%M:%S") + f".{now_ms}", fill=(255, 255, 255))

        rgb = np.asarray(img)
        bgra = np.empty((self._h, self._w, 4), dtype=np.uint8)
        bgra[..., 0] = rgb[..., 2]  # B
        bgra[..., 1] = rgb[..., 1]  # G
        bgra[..., 2] = rgb[..., 0]  # R
        bgra[..., 3] = 255
        return bgra
