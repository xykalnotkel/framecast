#!/usr/bin/env python3
"""CLIENT FrameCast Online (CLI) — buat tes end-to-end & benchmark.

  python client_rtc.py --host-id 123456789 --pin 482913

Apa yang dilakuin:
  1. join via server signaling pakai ID+PIN
  2. bikin offer WebRTC -> host jawab -> P2P
  3. terima N frame video (diverifikasi ukuran & fps)
  4. kirim input simulasi via DataChannel, cek echo dari host
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # root framecast/

import websockets
from aiortc import (
    RTCIceCandidate,
    RTCPeerConnection,
    RTCSessionDescription,
)
from aiortc.rtcconfiguration import RTCConfiguration, RTCIceServer


def api_base(signaling):
    return signaling.replace("ws://", "http://").replace("wss://", "https://").split("/ws")[0]


def http_json(url, data=None, token=None, timeout=15):
    import urllib.request

    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        url, data=json.dumps(data).encode() if data is not None else None, headers=headers
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def login_account(args):
    """Login akun -> token. Bisa langsung --account-token, atau email+password."""
    if args.account_token:
        return args.account_token, None
    if args.account_email and args.account_password:
        data = http_json(
            f"{api_base(args.signaling)}/api/login",
            {"email": args.account_email, "password": args.account_password},
        )
        return data["token"], data
    return None, None


def pick_host(args, token):
    """Kalau pakai akun: list device punya akun, biarkan user pilih (atau --host-id)."""
    if not token:
        return args.host_id
    devs = []
    try:
        devs = http_json(f"{api_base(args.signaling)}/api/devices", token=token)
    except Exception as e:
        print(f"[client] gak bisa ambil daftar device ({e}) — pakai --host-id langsung")
        return args.host_id
    if args.host_id:
        # validasi punya akun?
        for d in devs:
            if d.get("host_id") == args.host_id:
                return args.host_id
        print(f"[client] host-id {args.host_id} tidak ada di akun kamu. Pilih dari daftar:")
    print("[client] Device milik akun kamu:")
    for i, d in enumerate(devs):
        online = "ONLINE" if d.get("online") else "offline"
        print(f"  [{i}] {d.get('type','?').upper():6} {d.get('model') or d.get('name') or '?'} "
              f"({d.get('host_id')}) — {online}")
    if not devs:
        print("  (kosong — jalankan host dengan login akun yang sama dulu)")
        return None
    idx = input("  pilih nomor: ").strip()
    try:
        return devs[int(idx)]["host_id"]
    except Exception:
        return None


async def main():
    ap = argparse.ArgumentParser(description="Client CLI FrameCast Online")
    ap.add_argument("--host-id", default=None, help="ID host. Wajib buat mode PIN (PC gratis)")
    ap.add_argument("--pin", default=None, help="PIN host (mode PC gratis)")
    ap.add_argument("--account-email", default=None, help="login akun (remote HP premium / akun sama)")
    ap.add_argument("--account-password", default=None, help="password akun")
    ap.add_argument("--account-token", default=None, help="token akun langsung")
    ap.add_argument("--signaling", default="ws://127.0.0.1:9010")
    ap.add_argument("--frames", type=int, default=60, help="jumlah frame video untuk diukur")
    args = ap.parse_args()

    # MODE AKUN vs MODE PIN
    token, acct = login_account(args)
    if token:
        print(f"[client] login akun OK: {acct.get('email') if acct else '?'} "
              f"(plan {acct.get('plan','?') if acct else '?'})")
        host_id = pick_host(args, token)
        if not host_id:
            return 1
    else:
        if not args.host_id or not args.pin:
            print("Mode PIN butuh --host-id + --pin. Atau pakai akun: "
                  "--account-email + --account-password")
            return 1
        host_id = args.host_id

    join_msg = {"type": "client_join", "host_id": host_id}
    if token:
        join_msg["token"] = token
    else:
        join_msg["pin"] = args.pin

    async with websockets.connect(
        f"{args.signaling}?host={host_id}",
        max_size=1 << 20,
        compression=None,
    ) as ws:
        await ws.send(json.dumps(join_msg))
        reply = json.loads(await ws.recv())
        if reply.get("type") != "join_ok":
            reason = reply.get("reason")
            msg = reply.get("message", "")
            if reason == "premium_required":
                print(f"[client] ❌ {msg or 'Remote HP butuh akun PREMIUM'} "
                      f"(upgrade akun untuk remote HP)")
            elif reason == "not_yours":
                print(f"[client] ❌ device ini bukan milik akun kamu")
            else:
                print(f"[client] GAGAL join: {reason} {msg}")
            return 1
        host_info = reply["host"]
        plan = host_info.get("plan", "free")
        htype = host_info.get("type", "pc")
        print(f"[client] join OK -> host: {host_info.get('name')} "
              f"({host_info.get('platform')}) type={htype.upper()} "
              f"model={host_info.get('model','')} plan={plan.upper()}")

        # kalau host premium, ambil TURN credential dari backend
        ice_servers = [{"urls": ["stun:stun.cloudflare.com:3478", "stun:stun.l.google.com:19302"]}]
        if plan == "premium":
            try:
                import urllib.request

                api = args.signaling.replace("ws://", "http://").replace("wss://", "https://")
                api = api.split("/ws")[0]
                with urllib.request.urlopen(f"{api}/api/turn?host={args.host_id}", timeout=10) as r:
                    data = json.loads(r.read())
                if data.get("iceServers"):
                    ice_servers += data["iceServers"]
                    print(f"[client] TURN relay aktif ({len(data['iceServers'])} server)")
            except Exception as e:
                print(f"[client] ambil TURN gagal ({e}) — P2P saja")

        servers = []
        for item in ice_servers:
            servers.append(RTCIceServer(
                urls=item["urls"],
                username=item.get("username"),
                credential=item.get("credential"),
            ))
        pc = RTCPeerConnection(RTCConfiguration(iceServers=servers))
        # penting: deklarasi di offer bahwa kita mau TERIMA video dari host
        # (kalau tidak, offer tidak punya m-line video -> host gagal menjawab)
        pc.addTransceiver("video", direction="recvonly")
        video_track = [None]
        echoes = []
        answer_ev = asyncio.Event()
        answer_payload = {}
        stop = asyncio.Event()

        dc = pc.createDataChannel("input", negotiated=True, id=0)

        @dc.on("open")
        def on_open():
            print("[p2p] DataChannel 'input' terbuka")

        @dc.on("message")
        def on_msg(raw):
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                return
            if msg.get("type") == "echo":
                echoes.append(msg["seq"])

        @pc.on("track")
        def on_track(t):
            if t.kind == "video":
                video_track[0] = t

        @pc.on("icecandidate")
        async def on_ice(candidate):
            if candidate is None:
                return
            await ws.send(json.dumps({
                "type": "signal", "to": "host",
                "payload": {
                    "type": "candidate",
                    "candidate": candidate.candidate,
                    "sdpMid": candidate.sdpMid,
                    "sdpMLineIndex": candidate.sdpMLineIndex,
                },
            }))

        async def recv_loop():
            while not stop.is_set():
                raw = await ws.recv()
                msg = json.loads(raw)
                if msg.get("type") != "signal":
                    continue
                p = msg["payload"]
                if p.get("type") == "answer":
                    answer_payload.update(p)
                    answer_ev.set()
                elif p.get("type") == "candidate":
                    await pc.addIceCandidate(RTCIceCandidate(
                        candidate=p["candidate"],
                        sdpMid=p.get("sdpMid"),
                        sdpMLineIndex=p.get("sdpMLineIndex"),
                    ))

        rx = asyncio.create_task(recv_loop())

        # bikin offer -> host
        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)
        # kandidat ICE baru ada setelah setLocalDescription ->
        # wajib kirim pc.localDescription.sdp (bukan offer.sdp)
        await ws.send(json.dumps({
            "type": "signal", "to": "host",
            "payload": {"type": "offer", "sdp": pc.localDescription.sdp},
        }))
        print("[client] offer dikirim, tunggu answer host...")

        await asyncio.wait_for(answer_ev.wait(), timeout=20)
        await pc.setRemoteDescription(RTCSessionDescription(
            sdp=answer_payload["sdp"], type="answer"
        ))
        print("[client] answer diterima -> koneksi P2P dibangun...")

        # tunggu track video & terima N frame
        for _ in range(200):
            if video_track[0] is not None:
                break
            await asyncio.sleep(0.05)
        if video_track[0] is None:
            print("[client] TIDAK dapat track video!")
            stop.set()
            await rx
            return 2

        frames = 0
        t0 = time.perf_counter()
        first_size = None
        try:
            async with asyncio.timeout(30):
                while frames < args.frames:
                    frame = await video_track[0].recv()
                    arr = frame.to_ndarray(format="rgb24")
                    if first_size is None:
                        first_size = (arr.shape[1], arr.shape[0])
                        print(f"[client] video masuk: {first_size[0]}x{first_size[1]}")
                    frames += 1
        except asyncio.TimeoutError:
            print("[client] timeout nunggu frame video")
        elapsed = time.perf_counter() - t0
        fps = (frames - 1) / elapsed if frames > 1 else 0
        print(f"[client] video: {frames} frame dalam {elapsed:.2f}s -> "
              f"{fps:.1f} fps diterima")

        # kirim input simulasi + cek echo host
        sent = 0
        for i, ev in enumerate([
            {"action": "mousemove", "x": 0.5, "y": 0.5},
            {"action": "mousedown", "button": "left"},
            {"action": "mouseup", "button": "left"},
            {"action": "wheel", "dy": 1},
            {"action": "keydown", "code": "KeyA"},
            {"action": "keyup", "code": "KeyA"},
        ]):
            seq = i + 1
            dc.send(json.dumps({"type": "input", **ev, "seq": seq}))  # sync di aiortc
            sent += 1
            await asyncio.sleep(0.1)
        await asyncio.sleep(1.0)

        verified = sum(1 for s in echoes if 1 <= s <= sent)
        print(f"[client] input: {sent} event terkirim, {verified} echo diterima host")
        if verified == sent:
            print("\n[RESULT] END-TO-END OK: join -> P2P -> video -> input -> echo")
        else:
            print("\n[RESULT] ada yang kurang — cek log host")

        stop.set()
        rx.cancel()  # berhenti nunggu pesan signaling berikutnya
        try:
            await rx
        except asyncio.CancelledError:
            pass
        await pc.close()
        return 0 if verified == sent else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
