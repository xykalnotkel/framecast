/**
 * FrameCast Online — client Web (viewer + input).
 *
 * Dua mode:
 *   AKUN : login email+password -> daftar device milik akun -> connect
 *          (PC = gratis, HP = premium)
 *   PIN  : connect cepat pakai ID+PIN (PC gratis)
 *
 * Tanpa library: RTCPeerConnection native + WebSocket.
 */

const SIGNALING_URL = "wss://framecast-signal.akuntiktok76y.workers.dev/ws";
const API_URL = "https://framecast-signal.akuntiktok76y.workers.dev";
const STUN_SERVERS = [
  { urls: "stun:stun.cloudflare.com:3478" },
  { urls: "stun:stun.l.google.com:19302" },
];

const $ = (id) => document.getElementById(id);
const statusEl = $("status"), errEl = $("err");
const badge = $("badge"), usrEl = $("usr");

let ws = null, pc = null, dc = null, seq = 0;
let token = null, hostPlan = "free", turnIce = [];

function status(text, ok) {
  statusEl.textContent = text;
  $("dot").className = "dot" + (ok ? " on" : "");
  $("hdrState").textContent = text;
}
function error(text) { errEl.textContent = text; }
function showTab(which) {
  $("tabAcc").className = "tab" + (which === "acc" ? " on" : "");
  $("tabPin").className = "tab" + (which === "pin" ? " on" : "");
  $("panel-acc").className = "panel" + (which === "acc" ? " on" : "");
  $("panel-pin").className = "panel" + (which === "pin" ? " on" : "");
}

// ============ API ============
async function api(path, body) {
  const headers = { "Content-Type": "application/json" };
  if (token) headers["Authorization"] = "Bearer " + token;
  const r = await fetch(API_URL + path, {
    method: body ? "POST" : "GET",
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  return r.json();
}

async function register() {
  errEl.textContent = "";
  const email = $("email").value.trim(), password = $("pass").value;
  const r = await api("/api/register", { email, password });
  if (r.error) return error(r.message || r.error);
  token = r.token;
  afterLogin(r);
}

async function login() {
  errEl.textContent = "";
  const email = $("email").value.trim(), password = $("pass").value;
  const r = await api("/api/login", { email, password });
  if (r.error) return error(r.message || r.error);
  token = r.token;
  afterLogin(r);
}

function afterLogin(r) {
  usrEl.textContent = r.email + (r.plan === "premium" ? " ★PREMIUM" : "");
  badge.style.display = r.plan === "premium" ? "inline-block" : "none";
  status("masuk sebagai " + r.email, true);
  renderDevices(r.devices || []);
}

function renderDevices(devices) {
  const box = $("devList");
  box.innerHTML = "";
  if (!devices.length) {
    box.innerHTML = '<div style="font-size:13px;color:var(--dim);text-align:center;padding:8px">' +
      'Belum ada device. Jalankan host dengan login akun yang sama.</div>';
    return;
  }
  devices.forEach((d) => {
    const el = document.createElement("div");
    el.className = "dev";
    const isPhone = d.type === "phone";
    el.innerHTML =
      `<span class="dot2 ${d.online ? "on" : ""}"></span>` +
      `<span class="info"><b>${escapeHtml(d.name || d.model || d.host_id)}</b>` +
      `<span>${isPhone ? "📱 HP · " : "🖥️ PC · "}${escapeHtml(d.model || "")} · ${d.online ? "online" : "offline"}</span></span>` +
      `<span class="tag ${isPhone ? "phone" : ""}">${isPhone ? "PREMIUM" : "gratis"}</span>`;
    el.onclick = () => connectHost(d);
    box.appendChild(el);
  });
}

function escapeHtml(s) {
  return String(s || "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// ============ INPUT ============
function sendInput(action, extra = {}) {
  if (!dc || dc.readyState !== "open") return;
  dc.send(JSON.stringify({ type: "input", action, seq: ++seq, ...extra }));
}
function normalize(e) {
  const r = $("video").getBoundingClientRect();
  return { x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)),
           y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)) };
}
const video = $("video");
video.addEventListener("pointermove", (e) => { if (e.buttons === 1) sendInput("mousemove", normalize(e)); });
video.addEventListener("pointerdown", (e) => {
  sendInput("mousemove", normalize(e));
  const b = e.button === 2 ? "right" : e.button === 1 ? "middle" : "left";
  sendInput("mousedown", { button: b }); video.setPointerCapture(e.pointerId);
});
video.addEventListener("pointerup", (e) => {
  const b = e.button === 2 ? "right" : e.button === 1 ? "middle" : "left";
  sendInput("mouseup", { button: b });
});
video.addEventListener("contextmenu", (e) => e.preventDefault());
video.addEventListener("wheel", (e) => { e.preventDefault(); sendInput("wheel", { dy: Math.sign(e.deltaY) }); }, { passive: false });
window.addEventListener("keydown", (e) => {
  if (["INPUT"].includes(document.activeElement?.tagName) || e.repeat) return;
  sendInput("keydown", { code: e.code });
});
window.addEventListener("keyup", (e) => {
  if (["INPUT"].includes(document.activeElement?.tagName)) return;
  sendInput("keyup", { code: e.code });
});

// ============ WEBRTC ============
function setupPeer() {
  const ice = STUN_SERVERS.slice();
  if (turnIce.length) ice.push(...turnIce);
  pc = new RTCPeerConnection({ iceServers: ice });
  pc.addTransceiver("video", { direction: "recvonly" });
  pc.ontrack = (ev) => {
    video.srcObject = new MediaStream([ev.track]);
    $("stage").style.display = "block";
    status("terhubung — video P2P", true);
  };
  pc.ondatachannel = (ev) => { dc = ev.channel; };
  pc.onicecandidate = (ev) => {
    if (ev.candidate && ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "signal", to: "host",
        payload: { type: "candidate", candidate: ev.candidate.candidate,
                   sdpMid: ev.candidate.sdpMid, sdpMLineIndex: ev.candidate.sdpMLineIndex } }));
    }
  };
  pc.onconnectionstatechange = () => {
    status(`koneksi P2P: ${pc.connectionState}`, pc.connectionState === "connected");
    if (pc.connectionState === "failed")
      error(hostPlan === "premium" ? "P2P gagal — coba lagi (TURN akan dipakai)" : "P2P gagal di jaringan ini");
  };
}

async function makeOffer() {
  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);
  ws.send(JSON.stringify({ type: "signal", to: "host",
    payload: { type: "offer", sdp: pc.localDescription.sdp } }));
}

function openSignaling(hostId, joinMsg, onJoin) {
  ws = new WebSocket(`${SIGNALING_URL}?host=${hostId}`);
  ws.onopen = () => ws.send(JSON.stringify(joinMsg));
  ws.onmessage = async (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "join_ok") {
      onJoin(msg);
    } else if (msg.type === "join_fail") {
      const r = msg.reason;
      if (r === "premium_required")
        error("❌ Remote HP butuh akun PREMIUM — upgrade akun untuk connect");
      else if (r === "not_yours") error("❌ Device ini bukan milik akun kamu");
      else if (r === "offline") error("Host offline");
      else error(r === "pin_salah" ? "PIN salah" : "Gagal: " + r);
      cleanup();
    } else if (msg.type === "signal") {
      const p = msg.payload;
      if (p.type === "answer") await pc.setRemoteDescription({ type: "answer", sdp: p.sdp });
      else if (p.type === "candidate") {
        try { await pc.addIceCandidate({ candidate: p.candidate, sdpMid: p.sdpMid, sdpMLineIndex: p.sdpMLineIndex }); } catch {}
      }
    } else if (msg.type === "host_offline") { error("Host offline"); cleanup(); }
  };
  ws.onclose = () => { status("koneksi signaling tertutup"); cleanup(); };
  ws.onerror = () => error("Gagal hubungi signaling");
}

function cleanup() {
  if (pc) { pc.close(); pc = null; dc = null; }
}

// ============ CONNECT ============
async function fetchTurn(hostId) {
  try {
    const r = await api(`/api/turn?host=${hostId}`);
    if (Array.isArray(r.iceServers)) turnIce = r.iceServers;
  } catch {}
}

function connectHost(dev) {
  errEl.textContent = "";
  hostPlan = dev.type === "phone" ? "premium" : "free";
  badge.style.display = hostPlan === "premium" ? "inline-block" : "none";
  if (hostPlan === "premium") fetchTurn(dev.host_id);
  status(`connect ke ${dev.name || dev.model}...`);
  setupPeer();
  openSignaling(dev.host_id, { type: "client_join", host_id: dev.host_id, token }, (msg) => {
    status(`terhubung → ${msg.host.name} [${msg.host.type}]`, true);
    makeOffer();
  });
}

function quickConnect() {
  errEl.textContent = "";
  const hostId = $("id").value.replace(/\s/g, "");
  const pin = $("pin").value.trim();
  if (!/^\d{9}$/.test(hostId)) return error("ID harus 9 digit angka");
  if (!pin) return error("PIN wajib diisi (bebas)");
  hostPlan = "free";
  status("menghubungi signaling...");
  setupPeer();
  openSignaling(hostId, { type: "client_join", host_id: hostId, pin }, (msg) => {
    status(`terhubung → ${msg.host.name}`, true);
    makeOffer();
  });
}
