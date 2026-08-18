# FrameCast Online — Arsitektur, Backend Gratis & Cara Naik ke Produksi

Dokumen ini menjelaskan sistem online yang ada di folder `online/`:
**host PC (exe) dapat ID + PIN → client (Web / Android / Windows) connect dari
mana saja → WebRTC P2P → lihat & kontrol PC.** Semua pakai layanan GRATIS
tanpa kartu kredit (dengan catatan jujur di §5).

---

## 1. Gambaran sistem

```
 BACKEND (gratis, cuma relay, TIDAK lihat isi layar)
 ┌────────────────────────────────────────────────┐
 │ Cloudflare Workers + Durable Objects │
 │ - verifikasi ID + PIN (hanya HASH pin) │
 │ - relay SDP/ICE WebRTC (host <-> client) │
 │ - cek status host (GET /api/host?id=...) │
 └───────▲───────────────────────────▲────────────┘
 │ WS signaling │ WS signaling
 │ (wss://worker/ws?host=ID) │
 ┌───────┴────────┐ ┌───────┴─────────┐
 │ HOST (PC) │ │ CLIENT │
 │ online/host_rtc.py (exe) │ Web / Android / │
 │ ID 9 digit + PIN 6 digit │ Windows │
 │ capture + encode + input │ decode + input │
 └────────────────┘ └─────────────────┘
 ▲ jalur MEDIA & INPUT = P2P langsung ▲
 └──────────── WebRTC (UDP) ────────────┘
```

**Prinsip penting:** backend cuma "ruang tunggu" untuk mempertemukan host &
client. Setelah SDP/ICE bertukar, semua video & input jalan **langsung
host↔client** (peer-to-peer), jadi backend gratis tidak pernah menanggung
bandwidth video. Kalau P2P tidak bisa (NAT ketat/CGNAT), barulah dibutuhkan
**TURN relay** (§6).

### Kenapa Cloudflare Workers + Durable Objects?
- **Gratis tanpa kartu kredit** (free tier: 100rb request/hari).
- **Durable Object = ruang per host ID** — WebSocket host & client diarahkan
 ke objek yang sama (`/ws?host=<ID>`), jadi relay-nya stateful & instan.
- Edge network global (latensi rendah untuk signaling di mana pun).
- Satu Worker sudah cukup untuk seluruh signaling. (Alternatif di §7.)

---

## 2. Alur ID + PIN (ala AnyDesk)

1. **Host start** → generate ID 9 digit stabil per mesin (dari MAC+hostname)
 + PIN 6 digit acak per sesi.
 Host kirim ke backend: `{host_id, pin_hash=sha256(salt+pin), salt}`.
 → **Server tidak pernah tahu PIN aslinya** (cuma hash) — aman walau
 database backend bocor. Bisa dipakai berkali-kali, dan PIN mati saat host
 restart (perlu PIN baru) — persis perilaku AnyDesk.
2. **Client buka app/website** → masukkan ID + PIN.
 Backend cek hash → `join_ok` + kabari host (`client_joined`).
3. **WebRTC handshake** (SDP offer/answer + ICE candidate) di-relay backend
 sekali doang. Setelah itu P2P langsung.
4. **Video layar** mengalir dari host; **input** (mouse/keyboard) dari client
 lewat DataChannel terpisah (prioritas tinggi, terpisah dari video).

Skema pesan lengkap: `online/msgproto.py` (Python) ↔ `online/backend/src/index.ts`
(Worker) — dua implementasi protokol yang sama, satu untuk dev lokal, satu
untuk produksi gratis.

---

## 3. Isi folder `online/`

| File | Fungsi |
|---|---|
| `msgproto.py` | protokol signaling + ID/PIN helper |
| `signaling_local.py` | signaling server LOKAL (dev, jalan di laptop) |
| `host_rtc.py` | **HOST**: ID+PIN, capture, WebRTC video + input (ini yang jadi .exe) |
| `client_rtc.py` | client CLI (tes end-to-end & benchmark) |
| `backend/wrangler.toml`, `backend/src/index.ts` | **Cloudflare Worker** (produksi, gratis) |
| `web/index.html`, `web/app.js` | client browser (tanpa build, tanpa library) |
| `android/` | client **Android Kotlin** (Android Studio) |

**Status terverifikasi (di sandbox, WebRTC P2P beneran):**
```
join OK → P2P connected → video 640x360 @ ±52 fps → input 6/6 echo dari host
(dua sesi client berurutan juga jalan)
```

---

## 4. Cara deploy (semua gratis)

### 4.1 Backend Cloudflare (sekali, ~5 menit)
```bash
cd online/backend
npm i -g wrangler # CLI Cloudflare (gratis)
wrangler login # buka browser, izinkan (tanpa kartu kredit)
wrangler deploy # -> dapat URL https://framecast-signal.akuntiktok76y.workers.dev
```
Lalu ganti `SIGNALING_URL` di:
- `web/app.js` → `wss://framecast-signal.<sub>.workers.dev/ws`
- `android/.../MainActivity.kt` → url yang sama
- `host_rtc.py` → `--signaling wss://.../ws`

### 4.2 Host jadi .exe (Windows)
```bash
pip install pyinstaller
cd framecast
pyinstaller --onefile --name FrameCastHost online/host_rtc.py
# hasil: dist/FrameCastHost.exe — jalanin di PC yang mau di-remote,
# muncul ID + PIN di jendela console
```
Catatan: file jadi ~100+ MB karena bawa encoder (av/aiortc). Bisa dikecilkan
dengan UPX atau pindah ke Rust/C++ (lihat docs/ARCHITECTURE.md §12).

### 4.3 Client Web
Hosting gratis: **Cloudflare Pages / GitHub Pages / Netlify** — upload folder
`online/web` apa adanya (statis, tanpa build). Buka di HP/PC browser.

### 4.4 Client Android
Buka `online/android` di Android Studio → Run/`assembleDebug` → install APK.

---

## 5. Matriks backend gratis (tanpa kartu kredit) — pilih sesuai kebutuhan

| Layanan | Dipakai buat | Free tier | Kartu kredit? | Verdict |
|---|---|---|---|---|
| **Cloudflare Workers + DO** | signaling (WS relay) | 100rb req/hari | (belum) tidak | **Pilihan utama** (ini yang dipakai kode kita) |
| **Supabase** | DB host registry + Realtime signaling + Auth | 500MB DB, 2 project | (belum) tidak | Alternatif bagus kalau mau app full-stack & perlu akun user |
| **Firebase** | Realtime DB signaling + FCM push | Spark plan | (belum) tidak | Bisa, tapi latensi signaling lebih tinggi; FCM push gratis |
| **OneSignal** | push notification ("host online" / wake-up) | 10rb subscriber | (belum) tidak | Opsional, tambahan saja |
| **STUN** | tembus NAT ringan | gratis publik (Google/Cloudflare) | (belum) tidak | Sudah default di semua client |
| **TURN** | relay saat P2P gagal (CGNAT) | **lihat §6** | bervariasi | Satu-satunya yang bisa berbayar |

> Cek ulang angka free tier di halaman pricing resmi masing-masing — vendor
> sering ubah kebijakan. Prinsipnya: **signaling & STUN gratis selamanya;
> TURN & push punya batas wajar untuk skala personal.**

---

## 6. NAT, CGNAT & TURN — bagian yang HARUS jujur

WebRTC otomatis tembus NAT biasa (host candidates + STUN). TAPI:

- **Jaringan seluler Indonesia (Telkomsel/XL/Indosat) hampir selalu CGNAT** —
 kamu dapat IP privat yang dibagi banyak orang. P2P langsung sering gagal.
- Saat P2P gagal, dibutuhkan **TURN relay** (server yang mem-forward video).
 Ini SATU-SATUNYA komponen yang bisa berbayar — karena menanggung bandwidth.
 AnyDesk/TeamViewer punya jaringan relay raksasa sendiri (itu mahal).
- **Cloudflare Realtime TURN (dipakai proyek ini) — gratis 1.000 GB/bulan:**
 1. Buka https://dash.cloudflare.com → **Realtime → TURN Keys → Create** (1 menit).
 2. Salin **TURN Key ID** & **API Token** (yang muncul sekali, simpan aman).
 3. Set sebagai secret Worker + secret GitHub Actions:
 - Worker: `wrangler secret put TURN_KEY_ID` & `TURN_KEY_API_TOKEN`
 - GitHub: `Settings → Secrets → CLOUDFLARE_TURN_KEY_ID` &
 `CLOUDFLARE_TURN_KEY_API_TOKEN` (CI deploy otomatis set ke worker)
 4. Server TURN: `turn.cloudflare.com:3478` (UDP/TCP), `turns:...:5349` (TLS).
 Credential TTL 24 jam di-generate otomatis oleh Worker (`/api/turn`) saat
 client connect ke host **premium** — client tidak pernah pegang key asli.
 5. Harga: **gratis sampai 1.000 GB/bulan** (STUN selalu gratis), setelah itu
 $0.05/GB — detail: developers.cloudflare.com/realtime/turn
- **Cadangan kalau mau self-host**: coturn di Oracle Cloud free tier (ARM 4
 vCPU/24GB, gratis permanen) — tetap valid sebagai alternatif.
- **Open Relay (metered.ca)** — cuma buat tes/development.
- **Strategi cerdas:** coba P2P dulu (gratis), **fallback ke TURN hanya kalau
 gagal**. Kode client sudah melakukan ini otomatis — TURN tinggal ditambah
 di daftar `iceServers`.

---


## 6b. Model bisnis: PC gratis, HP PREMIUM

**Aturan main (v0.3):**

| Remote | Biaya | Cara masuk |
|---|---|---|
| **PC / desktop** | GRATIS | ID + PIN (tanpa akun) ATAU login akun sama |
| **HP (Android)** | **PREMIUM** | login akun SAMA di HP & device lain — tanpa ID+PIN, sistem deteksi model HP, **gak bisa connect kalau akun gak premium** |

Cara kerja:
1. **Akun** (register/login email+password) disimpan di backend (Cloudflare
 DO, password di-hash PBKDF2, sesi token 30 hari).
2. **Device registry** — tiap login, device daftar dengan:
 - PC: `type=pc`, model/platform OS
 - HP: `type=phone`, **model otomatis** (Android: `Build.MANUFACTURER + MODEL`,
 mis. "Samsung SM-A525F")
 Client lihat daftar device milik akun (`/api/devices`) lengkap dengan
 status online + model.
3. **Gating di server** (`checkJoin` di worker):
 - connect ke **PC** → selalu boleh (gratis) — via PIN atau akun sama
 - connect ke **HP** → cek akun client: kalau `plan != premium` →
 `join_fail reason=premium_required`
 Jadi bukan cuma pajangan UI — server yang nolak.
4. **Jadikan HP host**: app Android pakai MediaProjection + WebRTC
 (`ScreenCapturer` + `HostSession`), daftar sebagai `type=phone` dengan
 token akun. Client (akun sama, premium) lihat HP itu di daftar device.

### Naikin akun ke PREMIUM (test)
```bash
# dengan token akun + dev key (ganti payment integration nanti)
curl -X POST https://framecast-signal.akuntiktok76y.workers.dev/api/upgrade \
 -H "Content-Type: application/json" \
 -d '{"token":"<TOKEN_AKUN>","dev_key":"<PREMIUM_DEV_KEY>"}'
```
`PREMIUM_DEV_KEY` = secret worker (buat development; nanti diganti
payment webhook Stripe/Google Play/QRIS).

### Host PC dengan akun (opsional)
```bash
python host_rtc.py --account-email kamu@email.com --account-password rahasia \
 --device-type pc --name "PC-Kantor"
# PIN tetap muncul (akses gratis buat siapa saja yang tahu PIN),
# plus client login akun sama bisa connect tanpa PIN.
```

### Host HP (Python, simulasi buat tes gating)
```bash
python host_rtc.py --account-email kamu@email.com --account-password rahasia \
 --device-type phone --model "Samsung SM-A525F" --capture synthetic
```
(di HP asli, pakai app Android — lihat `online/android`.)

## 6c. TURN — jujur soal kartu kredit & alternatif

**Fakta:** Cloudflare Realtime TURN memang butuh aktifkan billing
(kartu kredit) — free tier 1.000 GB/bulan, tapi setup-nya minta CC.

**Keputusan v0.3:** premium **TIDAK bergantung TURN**. Premium = remote HP.
TURN jadi **add-on opsional**:
- Kode TURN tetap ada (`/api/turn`, client web/Android/Python sudah
 otomatis ambil kalau akun premium & TURN key terpasang).
- Kalau nanti kamu mau TURN tanpa kartu kredit:
 1. **Self-host coturn** di VPS yang kamu punya (Oracle free tier butuh CC
 saat signup, tapi tetap opsi paling populer) — satu command:
 `coturn -n --lt-cred-mech --user=user:pass --realm=framecast`
 2. **Metered.ca open relay** — gratis buat development/tes (jangan produksi).
- P2P (STUN) tetap jalan gratis buat mayoritas koneksi (LAN, Wi-Fi sama,
 NAT ringan). CGNAT seluler: perlu TURN — paling enak nanti kalau sudah
 ada payment/premium beneran.


## 6d. HIGH-PERF: DXGI + NVENC (120 fps)

Modul `online/highperf.py` — jalur 120+ fps di Windows + GPU:

```bash
# benchmark dulu (ukur capture+encode beneran di mesinmu)
python highperf.py --bench --size 1920x1080 --fps 120

# terus pakai di host online
python host_rtc.py --capture dxgi --codec h264 --fps 120 --plan premium
```

- `--capture dxgi` = **DXGI Desktop Duplication** via `dxcam` (capture GPU,
 zero-copy, dapat frame termutakhir, jauh lebih ringan dari mss/GDI).
- `--codec h264` = preferensi **H.264** (bitrate jauh lebih hemat dari VP8);
 encode tetap internal WebRTC, untuk NVENC hardware langsung di dalam
 WebRTC gunakan jalur GStreamer `webrtcbin` (lihat ARCHITECTURE.md §12)
 atau sambungkan `NvencEncoder` ke transport sendiri.
- `requirements-host.txt` = dependency opsional host (dxcam). Sudah otomatis
 terpasang di CI build exe.

## 7. Alternatif backend yang kamu sebut (Firebase / Supabase / OneSignal)

- **Supabase** bisa menggantikan Cloudflare Worker: pakai **Realtime
 (WebSocket)** sebagai relay signaling + **Postgres** untuk daftar host &
 akun + **Edge Functions** untuk verifikasi PIN. Lebih cocok kalau produkmu
 butuh akun pengguna & riwayat perangkat. Protokol pesan di `msgproto.py`
 tinggal diterjemahkan ke channel Realtime.
- **Firebase**: Realtime Database cocok sebagai "papan pesan" relay, tapi
 latensinya lebih tinggi & biaya scaling naik cepat; FCM gratis untuk push.
- **OneSignal**: pakai untuk notifikasi, mis. "PC kamu online" atau kirim
 PIN baru ke HP pemilik. Tidak menggantikan signaling.

**Rekomendasi:** mulai dengan Cloudflare (paling murah & latensi paling
rendah), tambah Supabase belakangan kalau butuh akun pengguna, dan OneSignal
paling akhir sebagai fitur push.

---

## 8. Keamanan (penting, jangan dilewati)

1. **PIN**: server cuma simpan `sha256(salt+pin)`; verifikasi constant-time.
 PIN 6 digit hanya melindungi dari tebakan lambat — untuk produksi serius
 tambahkan **rate limit join** (mis. 5 percobaan/menit per IP).
2. **Transport**: WebRTC sudah **DTLS-SRTP encrypted** (video & input
 terenkripsi end-to-end, tidak bisa disadap). WebSocket signaling pakai
 **WSS** (TLS). Jangan pernah pakai `ws://` biasa di luar LAN.
3. **Input = kendali penuh PC**: pastikan hanya orang yang kamu percaya yang
 dapat PIN. Buat fitur "one-time PIN" default (sudah) + log siapa connect.
4. **Jangan pakai port forwarding manual** — WebRTC menembus NAT lebih aman
 daripada membuka port 3389 (RDP) ke internet.

---

## 9. Roadmap v2 (online) lanjutan

| Langkah | Kerjaan | Status |
|---|---|---|
| v2.0 | signaling lokal + host + client CLI (P2P WebRTC) | (selesai) **selesai & teruji** |
| v2.1 | Cloudflare Worker produksi + web client + Android | (selesai) kode siap, tinggal deploy |
| v2.2 | TURN Cloudflare (opsional, butuh billing) — premium gak tergantung TURN | (selesai) kode siap |
| v2.3 | host jadi .exe (PyInstaller) + autostart/tray icon | (rencana) |
| v2.4 | OneSignal push + daftar host favorit di client | (rencana) opsional |
| v2.5 | host Android (MediaProjection capture) | (rencana) biar HP juga bisa jadi host |
| v2.6 | audio Opus + clipboard sync | (rencana) |
| v2.7 | 120fps: DXGI + NVENC di host (`--capture dxgi --codec h264`) | (selesai) kode siap, butuh GPU Windows |

---

## 10. Fakta & angka jujur

- **Latency**: LAN 10–30 ms glass-to-glass (P2P); internet tergantung RTT +
 TURN. Jauh lebih baik dari RDP klasik di internet.
- **Bandwidth**: VP8 640×360 ≈ 1–3 Mbps; 1080p60 H.264 ≈ 5–15 Mbps (dengan
 region/delta encoding). TURN gratis Oracle cukup untuk pemakaian wajar.
- **Free tier** menanggung: signaling (Cloudflare), STUN, app Android & web,
 host exe. Yang bisa tembus biaya hanya **TURN saat skala besar** dan
 **push saat >10rb subscriber**.

---


---

## 11. Repo & CI (GitHub Actions)

Repo: **https://github.com/xykalnotkel/framecast** — semua kode & workflow.

| Workflow | Nganggurin | Hasil (artifact) |
|---|---|---|
| `build-host.yml` | Windows runner + PyInstaller | `FrameCastHost-windows` (.exe, ~20 MB) |
| `build-android.yml` | Ubuntu + JDK 17 + Android SDK | `FrameCastClient-debug.apk` |
| `deploy-cloudflare.yml` | wrangler + `CLOUDFLARE_API_TOKEN` secret | worker live `framecast-signal.akuntiktok76y.workers.dev` |
| `pages.yml` | GitHub Pages | web client live `xykalnotkel.github.io/framecast` |

- Artifact bisa di-download dari tab **Actions** → pilih run → **Artifacts**.
- Secret repo (`Settings → Secrets and variables → Actions`): `CLOUDFLARE_API_TOKEN`.
- Semua workflow juga bisa dijalankan manual dari tab Actions (tombol
 **Run workflow**) — tanpa harus push.

*Terakhir diperbarui: 2026-08-18. Angka free tier = kondisi saat dokumen
ditulis; cek halaman pricing resmi sebelum produksi.*
