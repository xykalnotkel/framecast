"""Protokol signaling FrameCast Online — pesan JSON text di atas WebSocket.

Semua role (host & client) connect ke SATU endpoint WS. Server cuma RELAY:
SDP/ICE WebRTC lewat server, tapi media & data (video layar, input) jalan
LANGSUNG host<->client (P2P). Server tidak pernah melihat isi layar.

--- Alur koneksi ---
HOST:
  -> {"type":"host_register","host_id":"123456789","name":"PC-Kantor",
      "platform":"windows","pin_hash":"hex","salt":"hex"}
  <- {"type":"registered","host_id":"..."}
  <- {"type":"client_joined","client_id":"ab12cd34"}     (ada client minta masuk)

CLIENT:
  -> {"type":"client_join","host_id":"123456789","pin":"482913"}
  <- {"type":"join_ok","client_id":"...","host":{"name":..,"platform":..}}
  <- {"type":"join_fail","reason":"offline|pin_salah|..."}

RELAY (dua arah; server forward apa adanya):
  -> {"type":"signal","to":"host"|"<client_id>","payload":{...}}
  <- {"type":"signal","from":"<peer>","payload":{...}}
  payload:
    {"type":"offer"|"answer","sdp":"..."}
    {"type":"candidate","candidate":"...","sdpMid":"...","sdpMLineIndex":0}

Keepalive:  {"type":"ping"} / {"type":"pong"}

--- Input (DataChannel "input", negotiated id=0, JSON) ---
  {"type":"input","action":"mousemove","x":0.42,"y":0.33,"seq":1}
  {"type":"input","action":"mousedown"|"mouseup","button":"left|right|middle","seq":2}
  {"type":"input","action":"wheel","dy":1,"seq":3}
  {"type":"input","action":"keydown"|"keyup","code":"KeyA","seq":4}
Host balas tiap pesan ber-seq:  {"type":"echo","seq":N}
Koordinat x/y dinormalisasi 0..1 (aman beda resolusi host vs client).
"""

import hashlib
import hmac
import secrets


def new_salt():
    """Salt acak buat hash PIN (hex)."""
    return secrets.token_hex(8)


def pin_hash(pin: str, salt: str) -> str:
    """Hash PIN: sha256(salt + pin). Server cuma simpan hash -> aman walau
    database backend bocor, PIN asli tidak pernah bocor."""
    return hashlib.sha256((salt + pin).encode()).hexdigest()


def verify_pin(pin: str, salt: str, expected_hash: str) -> bool:
    """Cek PIN dengan perbandingan constant-time (anti timing attack)."""
    return hmac.compare_digest(pin_hash(pin, salt), expected_hash)


def make_host_id() -> str:
    """ID 9 digit AnyDesk-style, stabil per mesin (dari MAC + hostname + OS)."""
    import platform
    import uuid

    raw = f"{uuid.getnode()}|{platform.node()}|{platform.system()}|{platform.machine()}".encode()
    num = int(hashlib.sha1(raw).hexdigest()[:10], 16) % 1_000_000_000
    return f"{num:09d}"


def format_id(host_id: str) -> str:
    """123456789 -> '123 456 789' (biar enak dibaca)."""
    return f"{host_id[:3]} {host_id[3:6]} {host_id[6:]}"


def make_pin() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"
