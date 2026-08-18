# FrameCast — Remote Desktop dari Nol

[![Build Host (Windows .exe)](https://github.com/xykalnotkel/framecast/actions/workflows/build-host.yml/badge.svg)](https://github.com/xykalnotkel/framecast/actions/workflows/build-host.yml)
[![Build Android (APK)](https://github.com/xykalnotkel/framecast/actions/workflows/build-android.yml/badge.svg)](https://github.com/xykalnotkel/framecast/actions/workflows/build-android.yml)
[![Deploy Cloudflare Worker](https://github.com/xykalnotkel/framecast/actions/workflows/deploy-cloudflare.yml/badge.svg)](https://github.com/xykalnotkel/framecast/actions/workflows/deploy-cloudflare.yml)
[![Web client (Pages)](https://github.com/xykalnotkel/framecast/actions/workflows/pages.yml/badge.svg)](https://github.com/xykalnotkel/framecast/actions/workflows/pages.yml)

Remote desktop yang dibangun dari nol: tangkap layar, encode, kirim, decode,
tampil, plus input mouse/keyboard balik ke host. Semua kode ditulis sendiri,
tanpa library remote-desktop jadi.

> **Catatan realistis:** POC (WebSocket + JPEG) ini jalan 30–60 fps (720p)
> karena encode di CPU. Jalur ke 120+ fps ada di
> [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (DXGI + NVENC).

## Model bisnis

| Remote | Biaya | Cara masuk |
|---|---|---|
| PC / desktop | GRATIS | ID + PIN (tanpa akun) |
| HP (Android) | PREMIUM | login akun SAMA di HP & device lain, tanpa ID+PIN. Sistem deteksi model HP. Gak bisa connect kalau akun gak premium |

Backend: Cloudflare Workers + Durable Objects (gratis, tanpa kartu kredit) —
auth akun (PBKDF2), registri device, gating premium di server. Teruji
end-to-end di worker produksi: akun free ditolak akses ke HP, akun premium
connect.

**Download siap pakai (GitHub Release v0.3.1):**
- Host Windows (.exe): https://github.com/xykalnotkel/framecast/releases/download/v0.3.1/FrameCastHost.exe
- Client Android (APK): https://github.com/xykalnotkel/framecast/releases/download/v0.3.1/FrameCastClient-debug.apk
- Client Web (live): https://xykalnotkel.github.io/framecast
- Signaling backend (live): https://framecast-signal.akuntiktok76y.workers.dev

## Cara Uji (test dulu sebelum lanjut)

Semua alur di bawah sudah terverifikasi di produksi, jadi tinggal kamu
ulangi di perangkat sendiri.

### A. Remote PC (gratis) — web browser
1. Download `FrameCastHost.exe`, jalankan di PC yang mau di-remote.
2. Catat **ID 9 digit** dan **PIN 6 digit** yang muncul di jendela host.
3. Di HP/PC lain, buka `https://xykalnotkel.github.io/framecast`.
4. Pilih tab **Cepat (ID+PIN)**, masukkan ID + PIN, klik Connect.
5. Layar PC muncul; coba gerakkan mouse, scroll, dan ketik.

### B. Sistem akun + remote HP (premium)
1. Di web client, tab **Akun** → **Daftar** (email + password).
2. Login → device HP-mu otomatis terdaftar (model terdeteksi, mis.
   "Samsung SM-A525F").
3. **Uji gating:** coba connect ke HP dari akun yang masih free →
   muncul "Remote HP butuh akun PREMIUM" (ditolak server).
4. **Upgrade akun ke premium (mode test):**
   ```bash
   curl -X POST https://framecast-signal.akuntiktok76y.workers.dev/api/upgrade \
     -H "Content-Type: application/json" \
     -d '{"token":"<TOKEN_AKUN>","dev_key":"<PREMIUM_DEV_KEY>"}'
   ```
   (Token bisa diambil dari login; dev key sudah disimpan sebagai secret.
   Nanti diganti payment beneran.)
5. Connect ke HP lagi → sekarang masuk, video tampil.

### C. Host HP (Android)
1. Install `app-debug.apk`, buka, login akun.
2. Klik **Jadikan HP Host** → izinkan tangkap layar.
3. Dari device lain, login akun yang sama → HP muncul di daftar device →
   klik (akun harus premium).

> **Catatan:** alur host-HP (MediaProjection) sudah lolos compile di CI,
> tapi belum dites di HP fisik — butuh perangkat nyata untuk uji stream.

### D. Uji cepat (Python, tanpa UI)
```bash
pip install -r requirements.txt
python online/signaling_local.py &                      # terminal 1
python online/host_rtc.py --capture synthetic --pin 123456 &   # terminal 2
python online/client_rtc.py --host-id <ID> --pin 123456        # terminal 3
```

## Struktur

```
framecast/
├── server.py        # streamer (host) POC: capture -> encode -> broadcast
├── client.py        # viewer POC (pygame)
├── capture.py       # backend penangkap layar: mss & synthetic
├── encoder.py       # encoder pluggable: JPEG sekarang, NVENC nanti
├── protocol.py      # format biner frame
├── input.py         # injeksi input ke host (SendInput Windows)
├── docs/            # ARCHITECTURE.md (120fps) & ONLINE.md (sistem online)
└── online/          # sistem online v2-v3
    ├── host_rtc.py           # host: ID+PIN / akun, WebRTC, DXGI/NVENC
    ├── client_rtc.py         # client CLI (tes & benchmark)
    ├── signaling_local.py    # signaling lokal (dev)
    ├── msgproto.py           # protokol pesan
    ├── backend/              # Cloudflare Worker (auth + gating + relay)
    ├── web/                  # client browser (glassmorphism, tanpa library)
    └── android/              # client Android Kotlin (+ host HP)
```

## Opsi host

| Flag | Fungsi |
|---|---|
| `--device-type pc\|phone` | pc = gratis ID+PIN, phone = premium akun |
| `--plan free\|premium` | plan host (premium = TURN/HP diizinkan) |
| `--account-email` / `--account-password` | login akun (wajib buat host phone) |
| `--capture mss\|dxgi\|synthetic` | dxgi = DXGI Desktop Duplication (Windows, 120fps) |
| `--codec h264` | preferensi H.264 (bitrate hemat) |
| `--fps 120` | target fps |

## Backend (Cloudflare, gratis)

- `wrangler deploy` di `online/backend` → worker live.
- Endpoint: `/ws?host=<ID>` (signaling), `/api/register`, `/api/login`,
  `/api/devices`, `/api/upgrade`, `/api/host`, `/api/turn`.
- Secret worker: `PREMIUM_DEV_KEY` (upgrade test), opsional
  `TURN_KEY_ID`/`TURN_KEY_API_TOKEN` (TURN Cloudflare — butuh billing,
  jadi opsional; premium tidak bergantung TURN).

## CI (GitHub Actions)

| Workflow | Hasil |
|---|---|
| `build-host.yml` | FrameCastHost.exe (Windows) |
| `build-android.yml` | FrameCastClient-debug.apk |
| `deploy-cloudflare.yml` | deploy worker + set secret |
| `pages.yml` | deploy web client |

Semua berjalan otomatis tiap push ke `main`; artifact di-download dari tab
Actions atau Release.

## Lisensi

MIT — lihat [LICENSE](LICENSE).
