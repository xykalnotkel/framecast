# Arsitektur Remote Desktop 120+ FPS — dari Nol

> Dokumen ini jawaban teknis dari pertanyaan: *"gimana bikin remote desktop low-latency
> 120+ fps dari nol, gratis?"* POC yang jalan ada di repo ini; dokumen ini blueprint
> untuk naik dari POC (30-60 fps) ke kelas Parsec/Moonlight (120+ fps).

---

## 1. Ringkasan eksekutif

120+ fps low-latency BUKAN soal nambah setting di aplikasi — ini soal milih
teknologi yang tepat di **enam tahap pipeline**, dari tangkap layar sampai pixel
muncul di monitor client. Satu tahap aja salah (misal: pakai TCP, atau pakai
encode CPU), sisanya percuma.

| Tahap | POC ini (sudah jalan) | Versi 120+ fps |
|---|---|---|
| Capture | `mss` (GDI BitBlt) | DXGI Desktop Duplication (Windows) / PipeWire (Linux) |
| Encode | JPEG software (Pillow) | H.264/AV1 hardware: NVENC / AMF / QuickSync / VAAPI |
| Transport | WebSocket (TCP) | UDP + RTP custom, atau WebRTC (SRTP + FEC) |
| Decode | Pillow (CPU) | GPU: D3D11VA / VAAPI / VideoToolbox |
| Present | pygame blit | Flip-model, tanpa vsync, scaling GPU |
| Input | pygame → SendInput | RawInput + SendInput, jalur terpisah prioritas tinggi |

**Kesimpulan angka:** di LAN, 1080p120 dengan stack di atas bisa ditekan ke
**±8–15 ms glass-to-glass** (dari gerakan mouse di host sampai pixel muncul di
client). Parsec di jaringan bagus ada di kisaran itu.

**Semua komponennya gratis:** NVENC sudah bebas lisensi sejak 2021 (NVIDIA
mencabut pembatasan per-kartu), FFmpeg LGPL/GPL, WebRTC BSD, Windows SDK/Mac SDK
gratis, dan semua API platform (DXGI, VideoToolbox, VAAPI) gratis dipakai.

---

## 2. Ekspektasi jujur (biar nggak kecewa)

- **120 fps ≠ 120 gambar beda tiap detik.** Layar jarang berubah penuh tiap
  8.3 ms. Remote desktop profesional pakai kombinasi: encode full frame saat
  berubah banyak + **delta/dirty-rectangle** saat berubah sedikit. FPS tinggi
  penting buat **kelancaran gerak kursor & scrolling**, bukan buat nge-stream
  video film.
- **Angka yang harus kamu kejar** (LAN, 1080p):
  - frame interval 120 fps = **8.33 ms**. Ini batas budget total per frame.
  - glass-to-glass target: **< 15 ms** (bagus), **< 10 ms** (kelas Parsec).
- **Internet** beda cerita: tambah RTT (5–50 ms), plus butuh FEC/retransmit &
  NAT traversal (STUN/TURN). Fokus dulu LAN, internet belakangan.

---

## 3. Pipeline besar

```
HOST (server/streamer)                          CLIENT (viewer)
┌─────────────────────────────┐                ┌──────────────────────────────┐
│ 1. CAPTURE                  │                │ 4. DECODE                    │
│    DXGI Desktop Duplication │                │    D3D11VA (GPU) → texture   │
│    → texture GPU (zero-copy)│                │ 5. PRESENT                   │
│ 2. ENCODE                   │   transport    │    flip-model, no vsync      │
│    NVENC (GPU) → H.264 NAL  │◄──────────────►│    scaling GPU               │
│ 3. TRANSPORT                │  UDP/RTP       │ 6. INPUT (jalur terbalik)    │
│    fragment → UDP, seq, FEC │   + kontrol    │    RawInput → JSON/biner →   │
│    keyframe-on-loss         │                │    SendInput (di host)       │
└─────────────────────────────┘                └──────────────────────────────┘
```

Pola threading yang dipakai (sudah diterapkan di POC, tinggal disalin):
- **Capture thread** — ambil frame terus, simpan yang TERBARU, buang yang basi.
- **Encode thread/queue** — encode frame terbaru; kalau belum kelar, frame baru
  nunggu (jangan numpuk antrean — itu sumber latency).
- **Network thread** — kirim secepat mungkin, drop kalau client lambat.
- **Decode+present thread** (client) — satu frame di layar, satu di decode,
  satu di jaringan (triple-buffer 1-frame, bukan antrean panjang).

Prinsip emas: **antrean = latency**. Semua antrean di pipeline ini maksimal
1–2 slot, sisanya drop. Client lebih baik lihat kondisi terbaru daripada
frame lama yang lengkap.

---

## 4. Tahap 1: CAPTURE (dari mana pixel-nya)

### Windows (jalan utama buat 120fps)
- **DXGI Desktop Duplication API** — ini satu-satunya cara yang bener.
  - `CreateDesktopDuplication` → `AcquireNextFrame` (blokir maks 1 frame
    interval) → dapat `IDXGIResource` (texture GPU) + `DirtyRects` (daerah
    yang berubah — gratis! langsung dipakai buat delta encoding).
  - **Kunci zero-copy:** jangan `Map()` ke CPU. Texture GPU-nya langsung
    diserahkan ke NVENC sebagai input (`NV_ENC_INPUT_RESOURCE_TYPE_DIRECTX`).
    Pixel nggak pernah nyentuh CPU → hemat bandwidth memori & latency.
  - Update region (dirty rect) tinggal di-kirim → encoder cuma encode bagian
    itu (region encoding, lihat §5).
- Kenapa bukan GDI/`mss`/`BitBlt`? GDI jalan lewat CPU + compositor, kena
  tearing, dan nggak ngasih dirty rect. Cukup buat POC, mentok di ~60fps.
- Detail implementasi: butuh D3D11 device + swapchainless context. Contoh
  referensi: kode open-source `desktop-duplication` (C++), `dxgi-capture` di
  GitHub, atau binding `d3d11` + `winapi` kalau kamu pakai Rust.

### Linux
- **Wayland:** PipeWire + `xdg-desktop-portal` (`ScreenCast`). Wajib untuk
  Wayland; hasilnya stream DMABUF (GPU) — bisa di-encode VAAPI langsung.
- **X11:** `XShmGetImage` / `MIT-SHM` (cepat, shared memory, ~1-2 ms) atau
  `XGetImage` (lambat). DRM/KMS langsung kalau kamu kontrol display-nya sendiri.
- Encode hardware: **VAAPI** (Intel/AMD), NVENC (NVIDIA).

### macOS
- **ScreenCaptureKit** (macOS 12.3+): capture GPU, kasih frame + metadata,
  langsung bisa masuk VideoToolbox (H.264/HEVC hardware).

### Yang pasti salah
Menangkap pakai screenshot API biasa (gambar statis), cropping dari video
loopback, atau polling `GetPixel` — itu semua jalan ke latency puluhan ms.

---

## 5. Tahap 2: ENCODE

### Kenapa JPEG mati di 120fps
- Encode JPEG software ~5–25 ms/frame di CPU (terbukti di POC: 720p cuma
  tembus ~55 fps karena encode).
- Bitrate JPEG boros: 1080p quality 80 ≈ 2–6 MB/frame → 60 fps = **1–3 Gbps**.
  Nggak ada jaringan rumah yang kuat.
- **H.264/HEVC/AV1** 20–50× lebih hemat: 1080p120 H.264 CQP 22 ≈ **15–40 Mbps**.

### Pilih codec
| Codec | Encode hw | Decode hw | Catatan |
|---|---|---|---|
| H.264 | NVENC/AMF/QSV/VAAPI | semua GPU | standar emas remote desktop (kompatibilitas maks) |
| HEVC/H.265 | sama | mayoritas GPU ≥2016 | lebih hemat bitrate, paten bikin rumit |
| AV1 | RTX 40/50, Intel Arc, RX 7000 | sama | kualitas/bitrate terbaik, tapi decode hw belum universal |

Mulai dari **H.264**. Rendah latency, semua GPU bisa decode.

### Setting encoder yang BENER buat low-latency (ini sering dilupakan)
- **Preset low-latency**: NVENC `P1`/`P2` (bukan `P7`/quality), AMF
  `Quality vs Speed` → speed, x264 `preset ultrafast + tune zerolatency`.
- **B-frame = 0** (bikin delay + butuh reorder).
- **Lookahead = 0** (lookahead menambah latency sama banyaknya).
- **IDR/keyframe interval pendek** (mis. tiap 1–2 detik) — biar resync cepat
  saat frame hilang di UDP.
- **Rate control**: pakai **CQP/CRF** (quality constant), bukan CBR/VBR —
  remote desktop itu gerakan mendadak, CBR bikin blur pas gerak.
- **SPS/PPS** dikirim sekali saat koneksi + diulang di tiap keyframe.
- Kalau mau efisien: **region encoding** — pecah layar jadi blok 64×64,
  hash tiap blok, encode cuma blok yang berubah (inilah kenapa desktop
  streaming 1080p bisa cuma 5–15 Mbps). DXGI udah kasih dirty rect gratis.

### Cara akses hardware encoder (semua gratis)
1. **FFmpeg sebagai library/subprocess** — cara termudah naik level dari POC.
   `ffmpeg -f rawvideo ... -c:v h264_nvenc -preset p1 -tune ll -b:v 0 -cq 22 ...`
   Di Python bisa via `av` (PyAV, binding resmi FFmpeg). Ini upgrade v1 di roadmap.
2. **NVENC SDK langsung** (C++/Rust FFI) — kontrol penuh, zero-copy dari
   texture DXGI, buat yang mau serius.
3. **AMF (AMD) / MFX (Intel) / VAAPI** — setara NVENC buat vendor masing-masing.

---

## 6. Tahap 3: TRANSPORT

### TCP vs UDP — ini keputusan paling menentukan
- **TCP (yang dipakai POC via WebSocket):** reliabel, tapi ada **head-of-line
  blocking** — 1 paket hilang = semua paket setelahnya nunggu retransmit.
  Buat video real-time itu bencana: frame nggak pernah "telat", mereka cuma
  nyangkut. Ini alasan WebRTC & game streaming pakai UDP.
- **UDP + RTP (cara dari nol):** bikin sendiri, kontrol total:
  - Fragment frame H.264 jadi paket ≤ ~1200 byte (hindari fragmentasi IP MTU 1500).
  - Tiap paket: `frame_id` + `seq` + flag `start/end of frame`.
  - **Jitter buffer di client maks 1 frame** (8.3 ms) — reorder paket, lewatin
    frame telat.
  - **Loss handling**: kalau ada paket hilang → minta **keyframe baru** ke host
    (host encode ulang IDR). Jangan retransmit paket lama — basi.
  - **FEC** (Reed-Solomon/ULP FEC) buat internet: kirim paritas, client bisa
    perbaiki loss kecil tanpa minta keyframe.

### WebRTC (pilihan "gratis & mateng")
WebRTC itu library open-source yang nge-encapsulasi semua di atas: UDP + SRTP
(encrypted) + FEC + retransmit cerdas + jitter buffer + NAT traversal
(STUN/TURN/ICE) + congestion control (GCC). Kamu tetap "bikin dari nol" di
lapisan aplikasi (capture, encode via NVENC, UI, input), tapi nggak perlu
nulis ulang transport. Kalau target kamu internet (bukan cuma LAN), **pakai
WebRTC** — menulis NAT traversal sendiri itu proyek berbulan-bulan.
- C++: libwebrtc (resmi, berat). Python: `aiortc`. Rust: `webrtc-rs`.
- Catatan: WebRTC decode via API-nya sendiri; pastikan pipeline kamu bisa
  nyambung ke hardware decode.

### Aturan transport buat remote desktop
- Video & input **pisah jalur** (input prioritas tertinggi, dikirim duluan).
- Kirim frame **sesegera mungkin**, jangan nunggu batch.
- Bandwidth control: turunkan resolusi/bitrate saat jaringan macem, jangan
  nurunin fps dulu.

---

## 7. Tahap 4: DECODE (client)

- Windows: **D3D11 Video Acceleration** (`ID3D11VideoDecoder`) atau
  Media Foundation → hasilnya texture GPU. Rust: crate `d3d11va`/`media-foundation`.
- Linux: VAAPI (mpv style), macOS: VideoToolbox (keluar `CVPixelBuffer`).
- **Jangan decode ke CPU lalu upload balik ke GPU** — itu buang 2× bandwidth
  memori. Decode langsung ke texture, render texture itu.
- Latency decode hw: **< 1–2 ms** untuk 1080p120.

---

## 8. Tahap 5: PRESENT (pixel muncul di layar client)

- Windows: pakai **DXGI flip model** (`DXGI_SWAP_EFFECT_FLIP_DISCARD`) dengan
  buffer 2–3, present dengan flag **`DXGI_PRESENT_NO_VSYNC`** — render frame
  segera begitu datang, jangan nunggu refresh tick (vsync = +8–16 ms latency).
  Kalau mau anti-tearing, present paksa pas vblank (`DO_NOT_WAIT` + timing).
- Fullscreen **exclusive/borderless** + scaling GPU (jangan scaling CPU seperti
  `pygame.transform.scale`).
- Linux: SDL2/EGL + KMSDRM, `SDL_HINT_RENDER_VSYNC=0`. macOS: Metal `CAMetalLayer`.
- Prinsip: **jangan pernah antre 2+ frame di present.** Satu di layar, satu
  ready, sisanya drop.

---

## 9. Tahap 6: INPUT (jalur balik)

- **Tangkap (client):** Windows pakai **Raw Input** (`RegisterRawInputDevices`,
  event `WM_INPUT`) — polling 1000 Hz tanpa lewat window manager; mouse
  gerakan dibaca delta-nya. Jangan pakai `GetCursorPos` polling (kasar).
  Linux: `evdev`/libinput. macOS: `CGEventTap`.
- **Kirim:** koordinat **dinormalisasi 0..1** (kayak POC ini) biar beda
  resolusi host/client nggak masalah. Rate-limit mouse ke ~125–250 Hz,
  **coalescing** gerakan (kirim posisi terakhir aja per slot waktu).
- **Inject (host):** Windows `SendInput` (sudah ada di POC, ctypes) — cukup
  buat mayoritas aplikasi & game. Keterbatasan: **nggak bisa tembus UAC /
  secure desktop** (login screen) — itu butuh driver/virtualisasi kayak
  cara kerja VirtualBox; jangan digarap dulu.
  Linux: `uinput` atau `XTest`. macOS: `CGEventPost`.
- **Prediksi kursor:** client bisa render kursor lokal dengan prediksi posisi
  (host kirim posisi + timestamp), biar kursor terasa "instan" meski ada
  latency jaringan. Parsec/Moonlight nggak sepenuhnya begini, tapi buat
  pengalaman 120 fps, ini bedanya.

---

## 10. Budget latency & cara ngukur

Budget 1080p120 (LAN, semua hardware):

| Tahap | Anggaran | Realistis |
|---|---|---|
| Capture (DXGI) | < 2 ms | 0.5–1.5 ms |
| Encode (NVENC P1) | < 2 ms | 1–2 ms |
| Transport (UDP LAN) | < 1 ms | 0.5–2 ms |
| Decode (D3D11VA) | < 2 ms | 1–2 ms |
| Present (flip no-vsync) | < 1 ms | 0.5–1 ms |
| **Total glass-to-glass** | **< 8 ms** | **8–15 ms** |

Cara ngukur (jangan nebak):
1. **Watermark jam**: gambar jam milidetik di sudut frame (udah ada di
   `SyntheticCapture`), foto monitor host & client bersamaan pakai kamera HP
   (mode pro, shutter cepat), selisih jam = glass-to-glass. ±2–4 ms akurat.
2. **Frame counter**: host nempel nomor frame di gambar; client tunjukkan
   nomor yang diterima → selisih = frame di pipeline × interval.
3. **Loopback timestamp**: client kirim ping ber-timestamp lewat jalur input,
   host balas; itu RTT aplikasi (bukan murni video, tapi indikator bagus).

---

## 11. Roadmap: dari POC ini ke 120+ fps

> Setiap langkah punya cara ukur sendiri; JANGAN lanjut sebelum target
> langkah sebelumnya tercapai.

| Langkah | Kerjaan | Target terukur |
|---|---|---|
| **v0 (selesai)** | POC Python: WebSocket + JPEG + input | 60 fps @720p, pipeline terbukti |
| **v1: encode hw** | Server pakai FFmpeg `h264_nvenc` (PyAV), Ganti encoder di `encoder.py` | encode 1080p120 < 3 ms, bitrate < 40 Mbps |
| **v2: capture GPU** | Windows: DXGI Desktop Duplication ganti `MssCapture` | capture 120 fps, dirty rect |
| **v3: transport UDP** | Ganti WebSocket → UDP/RTP custom atau WebRTC | frame drop < 1% LAN, jitter < 2 ms |
| **v4: decode+present GPU** | Client decode D3D11VA + flip-model no-vsync | glass-to-glass < 15 ms LAN |
| **v5: polish** | region/delta encoding, AV1, audio Opus, FEC, STUN/TURN, prediksi kursor, multi-monitor | internet: < 40 ms @ 1080p60 |

**Urutan prioritas kalau waktu kamu mepet:** v1 (encode hw) dulu — itu yang
buka pintu 120fps. v2 kalau mau capture nggak nyedot CPU. v3 baru kalau mau
lewat internet.

---

## 12. Bahasa pemrograman

- **POC (ini): Python.** Gampang dibaca, gampang dimodif, bukti konsep.
- **Produksi 120fps: C++ atau Rust.**
  - C++: jalur paling mulus buat DXGI + NVENC SDK + D3D11VA (semua contoh
    resmi di C++).
  - Rust: `windows` crate (DXGI/D3D11 binding), `wgpu` buat render, `tokio`
    buat UDP, NVENC via FFI `nv-codec-headers`. Lebih aman, tapi contoh
    resminya lebih sedikit.
- Python bisa dipakai di lapisan kontrol (protocol, signaling WebRTC), tapi
  hot path (capture→encode→transport→decode→present) harus native.

---

## 13. Referensi (semua gratis/terbuka)

- **DXGI Desktop Duplication**: `docs.microsoft.com/windows/win32/direct3ddxgi/desktop-dup-api`
- **NVENC SDK**: `developer.nvidia.com/nvidia-video-codec-sdk` (free, cuma
  butuh akun NVIDIA)
- **FFmpeg (encode hw)**: `trac.ffmpeg.org/wiki/HWAccelIntro` — NVENC preset
  low-latency: `-preset p1 -tune ll -rc cqp -qp 22`
- **WebRTC**: `webrtc.org`; Python `aiortc`; Rust `webrtc-rs`
- **ScreenCaptureKit (macOS)**: `developer.apple.com/documentation/screencapturekit`
- **PipeWire portal (Linux)**: `flatpak.github.io/xdg-desktop-portal`
- **Raw Input / SendInput**: `learn.microsoft.com/windows/win32/inputdev/`
- **AIORTC contoh remote desktop**: `github.com/aiortc/aiortc` (contoh WebRTC)
- **Open-source yang boleh diintip (buat belajar, bukan dipakai mentah)**: Moonlight (`moonlight-stream/moonlight-common-c`), Sunshine, Parsec (closed tapi blog teknisnya bagus).

---

*Terakhir diperbarui: 2026-08-18. Angka performa = estimasi wajar di hardware
konsumen (RTX 30xx, i5 modern), LAN gigabit, tanpa load GPU lain.*
