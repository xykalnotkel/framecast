/**
 * FrameCast Online — signaling + AKUN (Cloudflare Workers + Durable Objects)
 *
 * GRATIS: free tier Workers 100rb request/hari, tanpa kartu kredit.
 * Deploy:  npx wrangler deploy   (dari folder online/backend)
 *
 * MODEL BISNIS:
 *   - Remote PC/desktop  = GRATIS  (ID + PIN, tanpa akun)
 *   - Remote HP          = PREMIUM (login akun SAMA di HP & device lain,
 *                                    tanpa ID+PIN; sistem deteksi model HP;
 *                                    gak bisa connect kalau akun gak premium)
 *
 * Endpoint:
 *   WS   /ws?host=<hostId>          host & client connect (signaling)
 *   POST /api/register              {email,password,device?} -> token+akun
 *   POST /api/login                 {email,password,device?} -> token+akun
 *   POST /api/logout                {token}
 *   GET  /api/devices?token=...     daftar device punya akun (online? model?)
 *   POST /api/upgrade               {token,dev_key} -> jadikan PREMIUM (test; nanti payment)
 *   GET  /api/host?id=<hostId>      status host (online? nama? plan?)
 *   GET  /api/turn?host=<hostId>    TURN credential (akun premium, opsional)
 *
 * Penyimpanan: Durable Object "GlobalRoom" = auth (akun, sesi) + registri
 * device + status host. Durable Object "HostRoom" per host = relay signaling.
 */

export interface Env {
  HOST_ROOMS: DurableObjectNamespace;
  GLOBAL: DurableObjectNamespace;
  TURN_KEY_ID?: string;
  TURN_KEY_API_TOKEN?: string;
  PREMIUM_DEV_KEY?: string; // kunci dev buat naikin akun ke premium (test; ganti payment nanti)
}

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization",
};

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (request.method === "OPTIONS") {
      return new Response(null, { headers: CORS });
    }
    if (url.pathname === "/ws") {
      if (request.headers.get("Upgrade")?.toLowerCase() !== "websocket") {
        return new Response("butuh WebSocket upgrade", { status: 426, headers: CORS });
      }
      const hostId = url.searchParams.get("host") || "default";
      return env.HOST_ROOMS.get(env.HOST_ROOMS.idFromName(hostId)).fetch(request);
    }
    if (url.pathname.startsWith("/api/")) {
      return env.GLOBAL.get(env.GLOBAL.idFromName("global")).fetch(request);
    }
    return new Response(
      "FrameCast — /ws?host=<ID> | /api/register | /api/login | /api/devices | /api/host | /api/turn",
      { status: 200, headers: { "content-type": "text/plain", ...CORS } }
    );
  },
};

// ============================ helpers ============================
function sha256Hex(text: string): Promise<string> {
  return crypto.subtle
    .digest("SHA-256", new TextEncoder().encode(text))
    .then((buf) => [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join(""));
}

function randomHex(bytes: number): string {
  const arr = new Uint8Array(bytes);
  crypto.getRandomValues(arr);
  return [...arr].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function hashPassword(password: string, salt: Uint8Array): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(password), "PBKDF2", false, ["deriveBits"]
  );
  const bits = await crypto.subtle.deriveBits(
    { name: "PBKDF2", salt, iterations: 100000, hash: "SHA-256" }, key, 256
  );
  return [...new Uint8Array(bits)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

interface DeviceInfo {
  type: string;      // "pc" | "phone"
  model: string;
  platform: string;
  name: string;
}

function json(res: unknown, status = 200): Response {
  return Response.json(res, { status, headers: CORS });
}

// ============================ GlobalRoom (auth + registri) ============================
export class GlobalRoom implements DurableObject {
  private env: Env;
  private storage: DurableObjectStorage;

  constructor(state: DurableObjectState, env: Env) {
    this.env = env;
    this.storage = state.storage;
  }

  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    let body: any = {};
    try {
      body = await request.json();
    } catch { /* GET / query */ }

    // panggilan internal antar Durable Object (HostRoom -> GlobalRoom)
    if (body.__op) {
      switch (body.__op) {
        case "registerHost": return await this.registerHost(body);
        case "unregisterHost": return await this.unregisterHost(body);
        case "hostStatus": return await this.hostStatus(body);
        case "accountPlanOf": return await this.accountPlanOf(body);
        case "checkJoin": return await this.checkJoin(body);
        default: return json({ error: "not_found" }, 404);
      }
    }

    try {
      switch (url.pathname) {
        case "/api/register":
          return await this.register(body);
        case "/api/login":
          return await this.login(body);
        case "/api/logout":
          return await this.logout(body.token);
        case "/api/devices":
          return await this.devices(url.searchParams.get("token") || body.token);
        case "/api/upgrade":
          return await this.upgrade(body.token, body.dev_key);
        case "/api/host":
          return await this.hostStatus({
            host_id: url.searchParams.get("id") || url.searchParams.get("host") || "",
          });
        case "/api/turn":
          return await this.turnCred(url.searchParams.get("host") || "");
        default:
          return json({ error: "not_found" }, 404);
      }
    } catch (e: any) {
      return json({ error: "server_error", message: String(e?.message ?? e) }, 500);
    }
  }

  // ---------- internal: akun ----------
  private async getAccount(email: string): Promise<any | null> {
    return (await this.storage.get(`acct:${email}`)) ?? null;
  }
  private async saveAccount(email: string, acct: any): Promise<void> {
    await this.storage.put(`acct:${email}`, acct);
  }
  private async createSession(email: string): Promise<string> {
    const token = randomHex(24);
    await this.storage.put(`tok:${await sha256Hex(token)}`, {
      email, exp: Date.now() + 30 * 24 * 3600 * 1000,
    });
    return token;
  }
  private async emailFromToken(token: string): Promise<string | null> {
    if (!token) return null;
    const s = await this.storage.get(`tok:${await sha256Hex(token)}`);
    if (!s) return null;
    if (s.exp < Date.now()) { await this.storage.delete(`tok:${await sha256Hex(token)}`); return null; }
    return s.email;
  }
  private async accountPlan(email: string): Promise<string> {
    const a = await this.getAccount(email);
    return a?.plan ?? "free";
  }

  private async devices(token: string): Promise<Response> {
    const email = await this.emailFromToken(token);
    if (!email) return json({ error: "unauthorized" }, 401);
    return json({ devices: await this.deviceList(email) });
  }

  // ---------- internal: device/host ----------
  private async hostRecord(hostId: string): Promise<any | null> {
    return (await this.storage.get(`host:${hostId}`)) ?? null;
  }

  // ---------- endpoints ----------
  private async register(body: any): Promise<Response> {
    const email = String(body.email || "").toLowerCase().trim();
    const password = String(body.password || "");
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) || password.length < 6) {
      return json({ error: "invalid", message: "email valid & password min 6 karakter" }, 400);
    }
    if (await this.getAccount(email)) {
      return json({ error: "email_exists", message: "email sudah terdaftar" }, 409);
    }
    const salt = crypto.getRandomValues(new Uint8Array(16));
    const acct = {
      email,
      plan: "free",
      salt: [...salt].map((b) => b.toString(16).padStart(2, "0")).join(""),
      hash: await hashPassword(password, salt),
      devices: [] as any[],
    };
    await this.saveAccount(email, acct);
    const token = await this.createSession(email);
    if (body.device) await this.upsertDevice(email, body.device, token);
    return json({ token, email, plan: "free", devices: await this.deviceList(email) });
  }

  private async login(body: any): Promise<Response> {
    const email = String(body.email || "").toLowerCase().trim();
    const password = String(body.password || "");
    const acct = await this.getAccount(email);
    if (!acct) return json({ error: "bad_credentials", message: "email/password salah" }, 401);
    const salt = Uint8Array.from(acct.salt.match(/.{2}/g)!.map((h: string) => parseInt(h, 16)));
    const h = await hashPassword(password, salt);
    if (h !== acct.hash) return json({ error: "bad_credentials", message: "email/password salah" }, 401);
    const token = await this.createSession(email);
    if (body.device) await this.upsertDevice(email, body.device, token);
    return json({ token, email, plan: acct.plan, devices: await this.deviceList(email) });
  }

  private async logout(token: string): Promise<Response> {
    if (token) await this.storage.delete(`tok:${await sha256Hex(token)}`);
    return json({ ok: true });
  }

  private async upgrade(token: string, devKey: string): Promise<Response> {
    if (!this.env.PREMIUM_DEV_KEY || devKey !== this.env.PREMIUM_DEV_KEY) {
      return json({ error: "bad_dev_key", message: "kunci dev salah" }, 403);
    }
    const email = await this.emailFromToken(token);
    if (!email) return json({ error: "unauthorized" }, 401);
    const acct = await this.getAccount(email);
    if (!acct) return json({ error: "not_found" }, 404);
    acct.plan = "premium";
    await this.saveAccount(email, acct);
    return json({ ok: true, email, plan: "premium" });
  }

  private async upsertDevice(email: string, dev: DeviceInfo, token: string): Promise<void> {
    const acct = await this.getAccount(email);
    if (!acct) return;
    const device = {
      host_id: `9${randomHex(4).slice(0, 8).replace(/[^0-9]/g, "1").slice(0, 8)}`,
      type: dev.type === "phone" ? "phone" : "pc",
      model: dev.model || "",
      platform: dev.platform || "",
      name: dev.name || "Device",
      added: Date.now(),
    };
    // reuse host_id kalau device yang sama (type+model sama) sudah ada
    const existing = acct.devices.find((d: any) => d.type === device.type && d.model === device.model);
    device.host_id = existing ? existing.host_id : device.host_id;
    if (!existing) acct.devices.push(device);
    await this.saveAccount(email, acct);
    // catat host record (online=false dulu; nanti host_register bikin online)
    const rec = await this.hostRecord(device.host_id);
    if (!rec) {
      await this.storage.put(`host:${device.host_id}`, {
        email, type: device.type, model: device.model,
        platform: device.platform, name: device.name, online: false,
      });
    }
  }

  private async deviceList(email: string): Promise<any[]> {
    const acct = await this.getAccount(email);
    if (!acct) return [];
    const out = [];
    for (const d of acct.devices) {
      const rec = await this.hostRecord(d.host_id);
      out.push({ ...d, online: rec?.online ?? false });
    }
    return out;
  }

  // ---------- dipanggil HostRoom (signaling) ----------
  async registerHost(body: any): Promise<Response> {
    const email = await this.emailFromToken(body.token);
    if (!email) return json({ error: "unauthorized" }, 401);
    const hostId = String(body.host_id || "");
    const dev = body.device || { type: "pc", model: "", platform: "", name: "PC" };
    const type = dev.type === "phone" ? "phone" : "pc";
    await this.storage.put(`host:${hostId}`, {
      email, type, model: dev.model || "", platform: dev.platform || "",
      name: dev.name || "Device", online: true,
    });
    // pastikan device tercatat di akun
    const acct = await this.getAccount(email);
    if (acct) {
      const exists = acct.devices.find((d: any) => d.host_id === hostId);
      if (!exists) {
        acct.devices.push({ host_id: hostId, type, model: dev.model || "", platform: dev.platform || "", name: dev.name || "Device", added: Date.now() });
        await this.saveAccount(email, acct);
      }
    }
    return json({ ok: true, email, plan: await this.accountPlan(email) });
  }

  async unregisterHost(body: any): Promise<Response> {
    const rec = await this.hostRecord(body.host_id);
    if (rec) {
      rec.online = false;
      await this.storage.put(`host:${body.host_id}`, rec);
    }
    return json({ ok: true });
  }

  async hostStatus(body: any): Promise<Response> {
    const rec = await this.hostRecord(body.host_id);
    if (!rec) return json({ online: false });
    return json({
      online: rec.online,
      name: rec.name,
      platform: rec.platform,
      type: rec.type,
      model: rec.model,
      plan: await this.accountPlan(rec.email),
    });
  }

  /** TURN credential — akun premium (opsional; TURN key bisa belum dipasang). */
  async turnCred(hostId: string): Promise<Response> {
    const rec = await this.hostRecord(hostId);
    const plan = rec ? await this.accountPlan(rec.email) : "free";
    if (plan !== "premium") {
      return json({ error: "premium_only", message: "TURN relay khusus akun premium" }, 403);
    }
    if (!this.env.TURN_KEY_ID || !this.env.TURN_KEY_API_TOKEN) {
      return json({ error: "turn_not_configured", message: "TURN_KEY_ID / TURN_KEY_API_TOKEN belum diset (opsional)" }, 500);
    }
    try {
      const res = await fetch(
        `https://rtc.live.cloudflare.com/v1/turn/keys/${this.env.TURN_KEY_ID}/credentials/generate-ice-servers`,
        {
          method: "POST",
          headers: { Authorization: `Bearer ${this.env.TURN_KEY_API_TOKEN}`, "Content-Type": "application/json" },
          body: JSON.stringify({ ttl: 86400 }),
        }
      );
      return json(await res.json(), res.status);
    } catch (e: any) {
      return json({ error: "turn_failed", message: String(e?.message ?? e) }, 500);
    }
  }

  async accountPlanOf(body: any): Promise<Response> {
    return json({ plan: await this.accountPlan(body.email) });
  }

  /** Cek apakah client (token) boleh join host (host_id). */
  async checkJoin(body: any): Promise<Response> {
    const email = await this.emailFromToken(body.token);
    if (!email) return json({ ok: false, reason: "unauthorized" });
    const rec = await this.hostRecord(body.host_id);
    if (!rec || !rec.online) return json({ ok: false, reason: "offline" });
    if (rec.email !== email) return json({ ok: false, reason: "not_yours", message: "device ini bukan milik akun kamu" });
    if (rec.type === "phone" && (await this.accountPlan(email)) !== "premium") {
      return json({ ok: false, reason: "premium_required", message: "Remote HP butuh akun PREMIUM" });
    }
    return json({
      ok: true,
      host: { name: rec.name, platform: rec.platform, type: rec.type, model: rec.model, plan: await this.accountPlan(email) },
    });
  }
}

// ============================ HostRoom (signaling per host) ============================
interface HostInfo {
  name: string;
  platform: string;
  pinHash: string;
  salt: string;
  deviceType: string;   // "pc" | "phone"
  model: string;
  accountEmail: string | null; // kalau host daftar pakai akun
}

export class HostRoom implements DurableObject {
  private host: WebSocket | null = null;
  private hostInfo: HostInfo | null = null;
  private clients = new Map<string, WebSocket>();
  private hostId = "";
  private env: Env;

  constructor(state: DurableObjectState, env: Env) {
    this.env = env;
  }

  private callGlobal(op: string, payload: any): Promise<any> {
    return this.env.GLOBAL.get(this.env.GLOBAL.idFromName("global"))
      .fetch(new Request("https://do/global", {
        method: "POST",
        body: JSON.stringify({ ...payload, __op: op }),
      }))
      .then((r) => r.json());
  }

  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    this.hostId = url.searchParams.get("host") || "default";

    if (url.pathname === "/api/host") {
      const g = await this.callGlobal("hostStatus", { host_id: this.hostId });
      return Response.json(g, { headers: CORS });
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

  /** TURN credential — akun premium (opsional; kalau TURN key belum diset, skip). */
  private async handleTurn(): Promise<Response> {
    const cors = { "Access-Control-Allow-Origin": "*" };
    const email = this.hostInfo?.accountEmail;
    let plan = "free";
    if (email) {
      const g: any = await this.callGlobal("accountPlanOf", { email });
      plan = g.plan ?? "free";
    }
    if (plan !== "premium") {
      return Response.json({ error: "premium_only", message: "TURN relay khusus akun premium" }, { status: 403, headers: cors });
    }
    if (!this.env.TURN_KEY_ID || !this.env.TURN_KEY_API_TOKEN) {
      return Response.json({ error: "turn_not_configured", message: "TURN_KEY_ID / TURN_KEY_API_TOKEN belum diset (opsional)" }, { status: 500, headers: cors });
    }
    try {
      const res = await fetch(
        `https://rtc.live.cloudflare.com/v1/turn/keys/${this.env.TURN_KEY_ID}/credentials/generate-ice-servers`,
        {
          method: "POST",
          headers: { Authorization: `Bearer ${this.env.TURN_KEY_API_TOKEN}`, "Content-Type": "application/json" },
          body: JSON.stringify({ ttl: 86400 }),
        }
      );
      return Response.json(await res.json(), { status: res.status, headers: cors });
    } catch (e: any) {
      return Response.json({ error: "turn_failed", message: String(e?.message ?? e) }, { status: 500, headers: cors });
    }
  }

  private attach(ws: WebSocket): void {
    ws.addEventListener("message", (ev) => void this.onMessage(ws, ev.data as string));
    ws.addEventListener("close", () => this.onClose(ws));
    ws.addEventListener("error", () => this.onClose(ws));
  }

  private async onMessage(ws: WebSocket, raw: string): Promise<void> {
    let msg: any;
    try { msg = JSON.parse(raw); } catch { return; }
    const t = msg.type;

    if (t === "host_register") {
      this.host = ws;
      (ws as any).__role = "host";
      const deviceType = msg.device_type === "phone" ? "phone" : "pc";
      this.hostInfo = {
        name: msg.name ?? "PC",
        platform: msg.platform ?? "unknown",
        pinHash: msg.pin_hash ?? "",
        salt: msg.salt ?? "",
        deviceType,
        model: msg.model ?? "",
        accountEmail: null,
      };
      // kalau host daftar pakai akun (token) -> catat di registri global
      if (msg.account_token) {
        try {
          const g: any = await this.callGlobal("registerHost", {
            token: msg.account_token,
            host_id: this.hostId,
            device: { type: deviceType, model: msg.model ?? "", platform: msg.platform ?? "", name: msg.name ?? "Device" },
          });
          if (g.ok) this.hostInfo.accountEmail = g.email;
          else console.log("registerHost ditolak:", g);
        } catch (e: any) {
          console.log("registerHost error:", e?.message ?? e);
        }
      }
      ws.send(JSON.stringify({ type: "registered", host_id: msg.host_id }));
      return;
    }

    if (t === "client_join") {
      const hostId = String(msg.host_id ?? this.hostId);
      // MODE AKUN: token (remote HP = premium; remote PC = gratis)
      if (msg.token) {
        const g: any = await this.callGlobal("checkJoin", { token: msg.token, host_id: hostId });
        if (!g.ok) {
          ws.send(JSON.stringify({ type: "join_fail", reason: g.reason, message: g.message }));
          return;
        }
        const clientId = crypto.randomUUID().slice(0, 8);
        this.clients.set(clientId, ws);
        (ws as any).__role = "client";
        (ws as any).__clientId = clientId;
        ws.send(JSON.stringify({
          type: "join_ok",
          client_id: clientId,
          host: {
            name: g.host.name, platform: g.host.platform,
            type: g.host.type, model: g.host.model, plan: g.host.plan,
          },
        }));
        this.host?.send(JSON.stringify({ type: "client_joined", client_id: clientId }));
        return;
      }
      // MODE PIN (gratis): cek host online + PIN + bukan HP
      if (!this.host || !this.hostInfo) {
        ws.send(JSON.stringify({ type: "join_fail", reason: "offline" }));
        return;
      }
      if (this.hostInfo.deviceType === "phone") {
        ws.send(JSON.stringify({ type: "join_fail", reason: "premium_required", message: "Remote HP butuh login akun PREMIUM" }));
        return;
      }
      const hash = await sha256Hex(this.hostInfo.salt + String(msg.pin ?? ""));
      if (hash !== this.hostInfo.pinHash) {
        ws.send(JSON.stringify({ type: "join_fail", reason: "pin_salah" }));
        return;
      }
      const clientId = crypto.randomUUID().slice(0, 8);
      this.clients.set(clientId, ws);
      (ws as any).__role = "client";
      (ws as any).__clientId = clientId;
      ws.send(JSON.stringify({
        type: "join_ok",
        client_id: clientId,
        host: {
          name: this.hostInfo.name, platform: this.hostInfo.platform,
          type: "pc", model: "", plan: "free",
        },
      }));
      this.host.send(JSON.stringify({ type: "client_joined", client_id: clientId }));
      return;
    }

    if (t === "signal" && (ws as any).__role === "host") {
      const peer = this.clients.get(msg.to);
      if (peer) peer.send(JSON.stringify({ type: "signal", from: "host", payload: msg.payload }));
      return;
    }
    if (t === "signal" && (ws as any).__role === "client") {
      if (this.host) {
        this.host.send(JSON.stringify({ type: "signal", from: (ws as any).__clientId, payload: msg.payload }));
      }
      return;
    }
    if (t === "ping") ws.send(JSON.stringify({ type: "pong" }));
  }

  private async onClose(ws: WebSocket): Promise<void> {
    if ((ws as any).__role === "host" && this.host === ws) {
      this.host = null;
      if (this.hostInfo?.accountEmail) {
        try { await this.callGlobal("unregisterHost", { host_id: this.hostId }); } catch {}
      }
      this.hostInfo = null;
      for (const [, cws] of this.clients) {
        try { cws.send(JSON.stringify({ type: "host_offline" })); cws.close(1000, "host_offline"); } catch {}
      }
      this.clients.clear();
    } else if ((ws as any).__role === "client") {
      this.clients.delete((ws as any).__clientId);
    }
  }
}
