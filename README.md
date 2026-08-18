# FrameCast — Remote Desktop dari Nol (POC)

[![Build Host (Windows .exe)](https://github.com/xykalnotkel/framecast/actions/workflows/build-host.yml/badge.svg)](https://github.com/xykalnotkel/framecast/actions/workflows/build-host.yml)
[![Build Android (APK)](https://github.com/xykalnotkel/framecast/actions/workflows/build-android.yml/badge.svg)](https://github.com/xykalnotkel/framecast/actions/workflows/build-android.yml)
[![Deploy Cloudflare Worker](https://github.com/xykalnotkel/framecast/actions/workflows/deploy-cloudflare.yml/badge.svg)](https://github.com/xykalnotkel/framecast/actions/workflows/deploy-cloudflare.yml)
[![Web client (Pages)](https://github.com/xykalnotkel/framecast/actions/workflows/pages.yml/badge.svg)](https://github.com/xykalnotkel/framecast/actions/workflows/pages.yml)

Remote desktop low-latency yang dibangun **dari nol**: tangkap layar → encode →
kirim → decode → tampil, plus input mouse/keyboard balik ke host. Semua kode di
repo ini ditulis sendiri, tanpa library remote-desktop jadi (WebSocket dipakai
cuma sebagai transport POC — nanti diganti UDP/WebRTC, lihat roadmap).

> ⚠️ **Harapan yang realistis**: POC ini jalan 30–60 fps (720p) — itu wajar,
> karena encode JPEG di CPU. Jalur ke **120+ fps** ada di
> [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) (hardware encoder, DXGI,
> UDP/WebRTC, GPU decode). Kode POC ini sengaja diarsitekturkan pluggable
> biar tiap tahap bisa di-upgrade satu-satu tanpa rombak total.

## 🔥 Yang baru: sistem ONLINE (v2) — ID + PIN, gratis, dari mana saja

Folder **`online/`** berisi versi AnyDesk-style + sistem akun. **Model bisnis:**

| Remote | Biaya | Cara masuk |
|---|---|---|
| **PC / desktop** | GRATIS | ID + PIN (tanpa akun) |
| **HP (Android)** | **PREMIUM** | login akun SAMA di HP & device lain, tanpa ID+PIN — deteksi model HP, gak bisa connect kalau akun gak premium |

Koneksi **WebRTC P2P** (video & input langsung host↔client). Backend
**Cloudflare Workers + Durable Objects (gratis, tanpa kartu kredit)** — auth
akun (PBKDF2), registri device, gating premium di server. Sudah teruji
end-to-end di worker produksi (free→HP ditolak, premium→HP connect).

**Download siap pakai (GitHub Release v0.2.0):**
- 🖥 **Host Windows (.exe):** https://github.com/xykalnotkel/framecast/releases/download/v0.2.0/FrameCastHost.exe
- 📱 **Client Android (APK):** https://github.com/xykalnotkel/framecast/releases/download/v0.2.0/FrameCastClient-debug.apk
- 🌐 **Client Web (live):** https://xykalnotkel.github.io/framecast
- ☁️ **Signaling backend (live):** https://framecast-signal.akuntiktok76y.workers.dev

```
online/
├── host_rtc.py           # HOST: ID+PIN -> capture -> WebRTC -> terima input
├── client_rtc.py         # client CLI (tes & benchmark)
├── signaling_local.py    # signaling server lokal (dev)
├── msgproto.py           # protokol pesan (ID+PIN, relay, input)
├── backend/              # Cloudflare Worker (produksi gratis)
├── web/                  # client browser (tanpa library)
└── android/              # client Android Kotlin
```

**Panduan lengkap (deploy, TURN/CGNAT, matriks backend gratis, roadmap):
[`docs/ONLINE.md`](docs/ONLINE.md)**

Quickstart online (lokal, buat nyoba):
```bash
cd framecast
python online/signaling_local.py &          # terminal 1: signaling
python online/host_rtc.py --capture synthetic --pin 123456 &   # terminal 2: host
python online/client_rtc.py --host-id <ID> --pin 123456        # terminal 3: client
```
(Untuk layar asli, hilangkan `--capture synthetic`. Produksi: `wrangler deploy`
di `online/backend`, ganti `SIGNALING_URL` di web & Android, bungkus host
dengan PyInstaller — semua langkahnya di `docs/ONLINE.md`.)

## Struktur

```
framecast/
├── server.py        # streamer (host): capture → encode → broadcast + inject input
├── client.py        # viewer (penonton): decode → tampil (pygame) + kirim input
├── capture.py       # backend penangkap layar: mss (asli) & synthetic (tes)
├── encoder.py       # encoder pluggable: JPEG sekarang, NVENC nanti
├── protocol.py      # format biner frame (header 23 byte + payload)
├── input.py         # injeksi input ke host: SendInput Windows (ctypes)
└── tests/
    └── headless_test.py  # tes end-to-end tanpa layar (jalan di CI/headless)
```

## Quickstart

```bash
# 1. install (Python 3.10+)
pip install -r requirements.txt

# 2. di mesin HOST (yang layarnya mau di-share) — jalanin dari folder framecast
python server.py --fps 120 --quality 75

# 3. di mesin CLIENT (penonton) — IP host sesuai jaringan kamu
python client.py --host 192.168.1.10 --port 9000
```

Catatan: buka port `9000` di firewall host. WebSocket bisa lewat proxy HTTP —
buat akses internet, paling gampang pasang WireGuard/Tailscale dulu.

## Opsi server

| Flag | Default | Fungsi |
|---|---|---|
| `--host` | `0.0.0.0` | bind address |
| `--port` | `9000` | port WebSocket |
| `--fps` | `60` | target fps capture (coba `120` di mesin kencang) |
| `--quality` | `80` | kualitas JPEG 1–100 (makin tinggi makin boros bandwidth) |
| `--capture` | `mss` | `mss` = layar asli, `synthetic` = frame tes |
| `--size` | — | ukuran frame synthetic, mis. `1280x720` |
| `--monitor` | `1` | nomor monitor (mss) |
| `--region` | — | tangkap sebagian layar: `left,top,width,height` (mss) |

## Opsi client

| Flag | Fungsi |
|---|---|
| `--host` / `--port` | alamat server |
| `--fullscreen` | fullscreen (scaling GPU via pygame) |
| `--no-stats` | sembunyikan overlay fps/drop |

## Tes headless (tanpa layar)

```bash
python tests/headless_test.py 60 1280x720   # target 60fps @ 720p
python tests/headless_test.py 120 640x360   # target 120fps @ 360p
```

Hasil terakhir yang terverifikasi (sandbox):

| Tes | Hasil |
|---|---|
| 1280×720 @ 60 | 55.4 fps terkirim, decode 5.0 ms/frame |
| 640×360 @ 120 | 117.4 fps, 0 frame drop, decode 1.3 ms/frame |

## Cara kerja singkat

1. **Capture thread** di server ambil frame terus-menerus, cuma nyimpen frame
   **terbaru** — yang telat di-drop (data basi lebih buruk daripada frame hilang).
2. **Pump asyncio** encode frame terbaru (di thread, biar event loop responsif),
   broadcast ke semua client. Client lambat dilewati, bukan nahan yang lain.
3. **Protocol biner** (23-byte header + payload) di atas WebSocket; pesan kontrol
   (input) lewat jalur teks terpisah biar input nggak kehalang frame besar.
4. **Client** decode JPEG → blit → flip tanpa vsync, plus kirim input lokal
   (koordinat dinormalisasi 0..1) balik ke host.

## Naik ke 120+ fps? Baca ini dulu

[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — blueprint lengkap: DXGI
Desktop Duplication, NVENC/AMF/QuickSync, UDP/RTP & WebRTC, GPU decode,
flip-model present, RawInput, budget latency, dan roadmap v0→v5 bertahap.
Singkatnya: **ganti encoder ke NVENC dulu** (v1), itu yang membuka pintu 120fps.
