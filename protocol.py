"""Wire protocol FrameCast — format biner frame di atas WebSocket.

Header (23 byte, big-endian):
  magic    4s  b"FC01"
  frame_id I   nomor frame (naik terus, buat deteksi frame drop)
  ftype    B   0 = keyframe, 1 = delta
  codec    B   0 = JPEG
  width    H   lebar (px)
  height   H   tinggi (px)
  quality  B   0-100 (hint ke decoder)
  flags    I   cadangan
  plen     I   panjang payload (byte)

Desain: header kecil + binary di atas WebSocket. Pesan text (JSON) khusus
untuk kontrol (hello, input) — terpisah dari aliran frame biar input
tidak kehalang frame besar.
"""

import struct

MAGIC = b"FC01"
HEADER = struct.Struct(">4sIBBHHBII")
HEADER_SIZE = HEADER.size  # 23

TYPE_KEYFRAME = 0
TYPE_DELTA = 1

CODEC_JPEG = 0


def pack_frame(frame_id, frame_type, codec, width, height, quality, flags, payload):
    header = HEADER.pack(
        MAGIC, frame_id, frame_type, codec, width, height, quality, flags, len(payload)
    )
    return header + payload


def unpack_frame(buf):
    """Parsing biner -> dict frame, atau None kalau belum lengkap."""
    if len(buf) < HEADER_SIZE:
        return None
    magic, frame_id, frame_type, codec, width, height, quality, flags, plen = (
        HEADER.unpack_from(buf, 0)
    )
    if magic != MAGIC:
        raise ValueError("magic tidak cocok — bukan frame FrameCast")
    if len(buf) < HEADER_SIZE + plen:
        return None
    return {
        "frame_id": frame_id,
        "frame_type": frame_type,
        "codec": codec,
        "width": width,
        "height": height,
        "quality": quality,
        "flags": flags,
        "payload": buf[HEADER_SIZE : HEADER_SIZE + plen],
    }
