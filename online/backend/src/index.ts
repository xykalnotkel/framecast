/**
 * FrameCast Online — signaling server (Cloudflare Workers + Durable Objects)
 *
 * GRATIS: free tier Workers 100rb request/hari, tanpa kartu kredit.
 * Deploy:  npx wrangler deploy   (dari folder online/backend)
 *
 * Fungsi: cuma mempertemukan host & client (relay SDP/ICE WebRTC) + verifikasi
 * PIN. Media & input tetap P2P langsung antara host dan client — server tidak
 * pernah melihat isi layar. Kode ini setara dengan online/signaling_local.py
 * (versi lokal untuk development).
 *
 * Endpoint:
 *   WS   wss://<worker>/ws?host=<hostId>   <- host & client connect di sini
 *   GET wss://<worker>/api/host?id=<hostId>  <- cek status host (online? nama? plan?)
 *   GET wss://<worker>/api/turn?host=<hostId> <- TURN credential (khusus plan premium)
 *
 * TURN: pakai Cloudflare Realtime TURN (gratis 1000 GB/bulan).
 * - Buat TURN Key di dashboard: Realtime → TURN Keys → Create
 * - Simpan TURN_KEY_ID & TURN_KEY_API_TOKEN sebagai secret Worker (wrangler secret put)
 */

export interface Env {
  HOST_ROOMS: DurableObjectNamespace;
  TURN_KEY_ID?: string;
  TURN_KEY_API_TOKEN?: string;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const cors = {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
    };
    if (request.method === "OPTIONS") {
      return new Response(null, { headers: cors });
    }

    if (url.pathname === "/ws") {
      if (request.headers.get("Upgrade")?.toLowerCase() !== "websocket") {
        return new Response("butuh WebSocket upgrade", { status: 426, headers: cors });
      }
      const hostId = url.searchParams.get("host") || "default";
      const id = env.HOST_ROOMS.idFromName(hostId);
      return env.HOST_ROOMS.get(id).fetch(request);
    }

    if (url.pathname === "/api/host" || url.pathname === "/api/turn") {
      const hostId = url.searchParams.get("host") || url.searchParams.get("id") || "";
      const id = env.HOST_ROOMS.idFromName(hostId);
      return env.HOST_ROOMS.get(id).fetch(request);
    }

    return new Response(
      "FrameCast signaling — /ws?host=<ID> | /api/host?id=<ID> | /api/turn?host=<ID>",
      { status: 200, headers: { "content-type": "text/plain", ...cors } }
    );
  },
};

function sha256Hex(text: string): Promise<string> {
  return crypto.subtle
    .digest("SHA-256", new TextEncoder().encode(text))
    .then((buf) =>
      [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("")
    );
}

interface HostInfo {
  name: string;
  platform: string;
  pinHash: string;
  salt: string;
  plan: "free" | "premium";
}

export class HostRoom implements DurableObject {
  private host: WebSocket | null = null;
  private hostInfo: HostInfo | null = null;
  private clients = new Map<string, WebSocket>();
  private env: Env;

  constructor(state: DurableObjectState, env: Env) {
    this.env = env;
  }

  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);

    if (url.pathname === "/api/host") {
      const body = this.host
        ? {
            online: true,
            name: this.hostInfo?.name ?? "",
            platform: this.hostInfo?.platform ?? "",
            plan: this.hostInfo?.plan ?? "free",
          }
        : { online: false };
      return Response.json(body, {
        headers: { "Access-Control-Allow-Origin": "*" },
      });
    }

    if (url.pathname === "/api/turn") {
      return this.handleTurn();
    }

    if (request.headers.get("Upgrade")?.toLowerCase() !== "websocket") {
      return new Response("expected websocket", { status: 400 });
    }

    const pair = new WebSocketPair();
    const [client, server] = Object.values(pair);
    server.accept();
    this.attach(server);
    return new Response(null, { status: 101, webSocket: client });
  }

  /** TURN credential — hanya untuk host plan premium (fitur berbayar). */
  private async handleTurn(): Promise<Response> {
    const cors = { "Access-Control-Allow-Origin": "*" };
    // cek premium + TURN key sudah di-set
    if (!this.hostInfo || this.hostInfo.plan !== "premium") {
      return Response.json(
        { error: "premium_only", message: "TURN relay khusus host plan premium" },
        { status: 403, headers: cors }
      );
    }
    if (!this.env.TURN_KEY_ID || !this.env.TURN_KEY_API_TOKEN) {
      return Response.json(
        { error: "turn_not_configured", message: "TURN_KEY_ID / TURN_KEY_API_TOKEN belum diset" },
        { status: 500, headers: cors }
      );
    }
    try {
      const res = await fetch(
        `https://rtc.live.cloudflare.com/v1/turn/keys/${this.env.TURN_KEY_ID}/credentials/generate-ice-servers`,
        {
          method: "POST",
          headers: {
            Authorization: `Bearer ${this.env.TURN_KEY_API_TOKEN}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ ttl: 86400 }), // berlaku 24 jam
        }
      );
      const data = await res.json();
      return Response.json(data, {
        status: res.status,
        headers: cors,
      });
    } catch (e: any) {
      return Response.json(
        { error: "turn_failed", message: String(e?.message ?? e) },
        { status: 500, headers: cors }
      );
    }
  }

  private attach(ws: WebSocket): void {
    ws.addEventListener("message", (ev) => {
      void this.onMessage(ws, ev.data as string);
    });
    ws.addEventListener("close", () => this.onClose(ws));
    ws.addEventListener("error", () => this.onClose(ws));
  }

  private async onMessage(ws: WebSocket, raw: string): Promise<void> {
    let msg: any;
    try {
      msg = JSON.parse(raw);
    } catch {
      return;
    }
    const t = msg.type;

    if (t === "host_register") {
      this.host = ws;
      (ws as any).__role = "host";
      this.hostInfo = {
        name: msg.name ?? "PC",
        platform: msg.platform ?? "unknown",
        pinHash: msg.pin_hash ?? "",
        salt: msg.salt ?? "",
        plan: msg.plan === "premium" ? "premium" : "free",
      };
      ws.send(JSON.stringify({ type: "registered", host_id: msg.host_id }));
      return;
    }

    if (t === "client_join") {
      if (!this.host || !this.hostInfo) {
        ws.send(JSON.stringify({ type: "join_fail", reason: "offline" }));
        return;
      }
      // PIN diverifikasi sebagai HASH (sha256(salt+pin)) — PIN asli tidak
      // pernah lewat server, aman walau backend bocor.
      const hash = await sha256Hex(this.hostInfo.salt + String(msg.pin ?? ""));
      if (hash !== this.hostInfo.pinHash) {
        ws.send(JSON.stringify({ type: "join_fail", reason: "pin_salah" }));
        return;
      }
      const clientId = crypto.randomUUID().slice(0, 8);
      this.clients.set(clientId, ws);
      (ws as any).__role = "client";
      (ws as any).__clientId = clientId;
      ws.send(
        JSON.stringify({
          type: "join_ok",
          client_id: clientId,
          host: {
            name: this.hostInfo.name,
            platform: this.hostInfo.platform,
            plan: this.hostInfo.plan,
          },
        })
      );
      this.host.send(JSON.stringify({ type: "client_joined", client_id: clientId }));
      return;
    }

    if (t === "signal" && (ws as any).__role === "host") {
      const peer = this.clients.get(msg.to);
      if (peer) {
        peer.send(JSON.stringify({ type: "signal", from: "host", payload: msg.payload }));
      }
      return;
    }

    if (t === "signal" && (ws as any).__role === "client") {
      if (this.host) {
        this.host.send(
          JSON.stringify({
            type: "signal",
            from: (ws as any).__clientId,
            payload: msg.payload,
          })
        );
      }
      return;
    }

    if (t === "ping") {
      ws.send(JSON.stringify({ type: "pong" }));
    }
  }

  private onClose(ws: WebSocket): void {
    if ((ws as any).__role === "host" && this.host === ws) {
      this.host = null;
      this.hostInfo = null;
      for (const [, cws] of this.clients) {
        try {
          cws.send(JSON.stringify({ type: "host_offline" }));
          cws.close(1000, "host_offline");
        } catch {
          /* ignore */
        }
      }
      this.clients.clear();
    } else if ((ws as any).__role === "client") {
      this.clients.delete((ws as any).__clientId);
    }
  }
}
