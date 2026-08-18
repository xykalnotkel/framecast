#!/usr/bin/env python3
"""Signaling server FrameCast Online — versi lokal buat development & tes.

Logikanya MIRIP dengan Cloudflare Worker (online/backend/src/index.ts) yang
dipakai untuk produksi gratis. Server ini cuma relay SDP/ICE + verifikasi PIN;
media & input tetap P2P host<->client.

Jalankan:  python signaling_local.py --port 9010
"""

import argparse
import asyncio
import json
import time
import uuid

import websockets

from msgproto import verify_pin


class Room:
    def __init__(self, host_id, name, platform, pin_hash, salt, ws):
        self.host_id = host_id
        self.name = name
        self.platform = platform
        self.pin_hash = pin_hash
        self.salt = salt
        self.host_ws = ws
        self.clients = {}  # client_id -> ws
        self.created_at = time.time()

    def is_online(self):
        return self.host_ws is not None


ROOMS = {}  # host_id -> Room


async def send(ws, obj):
    await ws.send(json.dumps(obj))


async def handle(ws):
    role = None      # "host" | "client"
    room = None
    client_id = None
    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            t = msg.get("type")

            # ---------- HOST ----------
            if t == "host_register":
                host_id = str(msg.get("host_id", ""))
                old = ROOMS.get(host_id)
                if old is not None and old.host_ws is not None and old.host_ws is not ws:
                    await send(old.host_ws, {"type": "bye", "reason": "host_replaced"})
                room = Room(
                    host_id,
                    msg.get("name", "PC"),
                    msg.get("platform", "unknown"),
                    msg.get("pin_hash", ""),
                    msg.get("salt", ""),
                    ws,
                )
                ROOMS[host_id] = room
                role = "host"
                await send(ws, {"type": "registered", "host_id": host_id})
                print(f"[room] host online: {host_id} ({room.name})")

            elif role == "host" and t == "client_joined_check":
                pass  # reserved

            elif role == "host" and t == "signal":
                client_id = msg.get("to")
                peer = room.clients.get(client_id)
                if peer:
                    await send(peer, {"type": "signal", "from": "host", "payload": msg["payload"]})

            # ---------- CLIENT ----------
            elif t == "client_join":
                host_id = str(msg.get("host_id", ""))
                room = ROOMS.get(host_id)
                if room is None or not room.is_online():
                    await send(ws, {"type": "join_fail", "reason": "offline"})
                elif not verify_pin(str(msg.get("pin", "")), room.salt, room.pin_hash):
                    await send(ws, {"type": "join_fail", "reason": "pin_salah"})
                else:
                    client_id = uuid.uuid4().hex[:8]
                    room.clients[client_id] = ws
                    role = "client"
                    await send(ws, {
                        "type": "join_ok",
                        "client_id": client_id,
                        "host": {"name": room.name, "platform": room.platform},
                    })
                    await send(room.host_ws, {"type": "client_joined", "client_id": client_id})
                    print(f"[room] client {client_id} join host {host_id}")

            elif role == "client" and t == "signal":
                await send(room.host_ws, {"type": "signal", "from": client_id, "payload": msg["payload"]})

            elif t == "ping":
                await send(ws, {"type": "pong"})

    except websockets.ConnectionClosed:
        pass
    finally:
        # bersihkan saat putus
        if role == "host" and room is not None:
            if ROOMS.get(room.host_id) is room:
                ROOMS.pop(room.host_id, None)
            print(f"[room] host offline: {room.host_id}")
        elif role == "client" and room is not None and client_id:
            room.clients.pop(client_id, None)


async def main():
    ap = argparse.ArgumentParser(description="Signaling server lokal FrameCast")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=9010)
    args = ap.parse_args()
    print(f"[signaling] listen ws://{args.host}:{args.port} (versi lokal, "
          f"produksi pakai Cloudflare Worker)")
    async with websockets.serve(handle, args.host, args.port, max_size=1 << 20):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
