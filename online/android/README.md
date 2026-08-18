# FrameCast Android (Kotlin) — Client

Aplikasi Android untuk **melihat & mengontrol PC** (client). Pakai library
WebRTC resmi (`io.github.webrtc-sdk:android:125.6422.07`, gratis) + OkHttp WebSocket.

> Bisa juga dipakai sebagai HOST nanti: tinggal tambah capture layar
> (`MediaProjection` + `VirtualDisplay`) + `VideoSource` sebagai track
> WebRTC — arsitekturnya sama persis (lihat `WebRtcSession.kt`).

## Build (Android Studio, gratis)

1. Buka folder ini di **Android Studio** (atau CLI: `./gradlew assembleDebug`).
2. Edit `MainActivity.kt` → ganti `SIGNALING_URL` ke worker kamu:
 `wss://<nama-worker>.workers.dev/ws`
3. Build APK → install di HP.
4. Buka app → masukkan **ID host (9 digit)** + **PIN (6 digit)** → Connect.
5. Layar PC tampil; geser = mouse, pinch/gulir = scroll, keyboard = ketik.

SDK: compileSdk 35, minSdk 26 (Android 8.0+), Kotlin 2.x, AGP 8.x.

## Cara kerja (ringkas)

```
MainActivity -> UI + signaling (OkHttp WebSocket) + join ID/PIN
WebRtcSession -> RTCPeerConnection: offer, video recvonly, DataChannel input
InputSender -> MotionEvent/KeyEvent -> JSON -> DataChannel -> host
Host (PC) -> terima input -> SendInput ke OS (lihat ../host_rtc.py)
```

Video di-render ke `SurfaceViewRenderer` (decoding hardware otomatis oleh
MediaCodec). PIN & ID dibawa server cuma dalam bentuk hash — aman.

## Catatan produksi
- WebRTC di Android butuh permission: `INTERNET` (ada di manifest).
- Buat fullscreen kontrol: mode immersive (sudah disiapkan di layout).
- Wake lock biar layar HP nggak mati selama remote (tambahkan saat connect).
- Kalau P2P gagal (jaringan seluler CGNAT), wajib pasang **TURN** di
 `WebRtcSession.kt` (`iceServers`) — lihat docs/ONLINE.md.
