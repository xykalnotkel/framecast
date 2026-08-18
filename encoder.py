"""Encoder frame (pluggable). Saat ini: JPEG via Pillow.

Kenapa JPEG dulu?
  - simpel, ada di mana-mana, mudah dibaca orang
  - encoder & decoder software sudah cukup untuk POC 30-60 fps resolusi kecil

Kenapa BUKAN buat 120 fps?
  - encode JPEG software di CPU ~5-25 ms/frame tergantung resolusi
  - bitrate-nya boros dibanding H.264 (bikin bandwith jadi bottleneck)
  - jalan 120 fps yang bener: H.264/AV1 lewat hardware encoder
    (NVENC/AMF/QuickSync/VAAPI) — kerangka class di bawah sengaja dibuat
    pluggable, tinggal tambah class baru (lihat docs/ARCHITECTURE.md).

Catatan JPEG untuk streaming:
  - progressive=False  : progressive butuh pass tambahan -> latency naik
  - optimize=False     : optimize = cari table Huffman -> lama, hasil hampir sama
  - subsampling=0      : 4:4:4, teks lebih tajam (penting buat remote desktop)
"""

import io

import numpy as np
from PIL import Image


def bgra_to_rgb(frame_bgra):
    """(H,W,4) BGRA -> (H,W,3) RGB (array kontigu baru)."""
    rgb = frame_bgra[..., [2, 1, 0]]
    return np.ascontiguousarray(rgb)


class JpegEncoder:
    codec_id = 0  # cocok dengan CODEC_JPEG di protocol.py

    def __init__(self, quality=80, subsampling=0):
        self.quality = quality
        self.subsampling = subsampling

    def encode(self, frame_bgra, keyframe=False):
        """Encode 1 frame -> bytes JPEG. `keyframe` dipakai codec delta nanti."""
        img = Image.fromarray(bgra_to_rgb(frame_bgra), "RGB")
        buf = io.BytesIO()
        img.save(
            buf,
            "JPEG",
            quality=self.quality,
            subsampling=self.subsampling,
            progressive=False,
            optimize=False,
        )
        return buf.getvalue()


def decode_jpeg(payload):
    """Decode bytes JPEG -> PIL Image RGB."""
    return Image.open(io.BytesIO(payload)).convert("RGB")
