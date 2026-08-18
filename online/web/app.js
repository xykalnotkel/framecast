/**
 * FrameCast Online — client Web (viewer + input).
 *
 * Tanpa library: pakai RTCPeerConnection native + WebSocket.
 * Alur: join pakai ID+PIN -> (kalau host PREMIUM, ambil TURN credential) ->
 *       offer WebRTC -> P2P video dari host -> kirim input via DataChannel.
 */

// ====== konfigurasi ======
const SIGNALING_URL = "wss://framecast-signal.akuntiktok76y.workers.dev/ws"; // ganti kalau worker berubah
const STUN_SERVERS = [
  { urls: "stun:stun.cloudflare.com:3478" },
  { urls: "stun:stun.l.google.com:19302" },
];

// ====== elemen UI ======
const $ = (id) => document.getElementById(id);
const btn = $("btn"), idInput = $("id"), pinInput = $("pin");
const statusEl = $("status"), errEl = $("err"), stage = $("stage"), video = $("video");
const badge = $("badge");

let ws = null;        // koneksi signaling
let pc = null;        // RTCPeerConnection
let dc = null;        // DataChannel input
let seq = 0;
let hostPlan = "free";
let turnIce = [];     // TURN credential (kalau premium)

function status(text, ok) {
  statusEl.textContent = text;
  $("dot").className = "dot" + (ok ? " on" : "");
  $("hdrState").textContent = text;
}
function error(text) { errEl.textContent = text; }

// ====== input -> host ======
function sendInput(action, extra = {}) {
  if (!dc || dc.readyState !== "open") return;
  dc.send(JSON.stringify({ type: "input", action, seq: ++seq, ...extra }));
}

function normalize(e) {
  const r = video.getBoundingClientRect();
  return {
    x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)),
    y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)),
  };
}

video.addEventListener("pointermove", (e) => {
  if (e.buttons === 1) sendInput("mousemove", normalize(e));
});
video.addEventListener("pointerdown", (e) => {
  sendInput("mousemove", normalize(e));
  const b = e.button === 2 ? "right" : e.button === 1 ? "middle" : "left";
  sendInput("mousedown", { button: b });
  video.setPointerCapture(e.pointerId);
});
video.addEventListener("pointerup", (e) => {
  const b = e.button === 2 ? "right" : e.button === 1 ? "middle" : "left";
  sendInput("mouseup", { button: b });
});
video.addEventListener("contextmenu", (e) => e.preventDefault());
video.addEventListener("wheel", (e) => {
  e.preventDefault();
  sendInput("wheel", { dy: Math.sign(e.deltaY) });
}, { passive: false });
window.addEventListener("keydown", (e) => {
  if (["INPUT"].includes(document.activeElement?.tagName)) return;
  if (e.repeat) return;
  sendInput("keydown", { code: e.code });
});
window.addEventListener("keyup", (e) => {
  if (["INPUT"].includes(document.activeElement?.tagName)) return;
  sendInput("keyup", { code: e.code });
});

// ====== WebRTC ======
function iceServers() {
  const list = STUN_SERVERS.slice();
  if (turnIce.length) list.push(...turnIce); // TURN hanya kalau premium
  return list;
}

async function fetchTurn() {
  try {
    const hostId = idInput.value.replace(/\s/g, "");
    const r = await fetch(`https://framecast-signal.akuntiktok76y.workers.dev/api/turn?host=${hostId}`);
    const j = await r.json();
    if (j && Array.isArray(j.iceServers)) turnIce = j.iceServers;
  } catch {}
}

function setupPeer() {
  pc = new RTCPeerConnection({ iceServers: iceServers() });
  pc.addTransceiver("video", { direction: "recvonly" });

  pc.ontrack = (ev) => {
    video.srcObject = new MediaStream([ev.track]);
    stage.style.display = "block";
    status("terhubung — video P2P", true);
  };

  pc.ondatachannel = (ev) => {
    dc = ev.channel;
    dc.onmessage = () => {};
  };

  pc.onicecandidate = (ev) => {
    if (ev.candidate && ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({
        type: "signal", to: "host",
        payload: {
          type: "candidate",
          candidate: ev.candidate.candidate,
          sdpMid: ev.candidate.sdpMid,
          sdpMLineIndex: ev.candidate.sdpMLineIndex,
        },
      }));
    }
  };

  pc.onconnectionstatechange = () => {
    status(`koneksi P2P: ${pc.connectionState}`, pc.connectionState === "connected");
    if (pc.connectionState === "failed") {
      error(hostPlan === "premium"
        ? "P2P gagal — coba lagi (TURN relay akan dipakai otomatis)"
        : "P2P gagal di jaringan ini — butuh PREMIUM (TURN relay)");
    }
  };
}

async function makeOffer() {
  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);
  ws.send(JSON.stringify({
    type: "signal", to: "host",
    payload: { type: "offer", sdp: pc.localDescription.sdp },
  }));
}

// ====== signaling ======
async function connect() {
  errEl.textContent = "";
  const hostId = idInput.value.replace(/\s/g, "");
  const pin = pinInput.value.trim();
  if (!/^\d{9}$/.test(hostId)) { error("ID harus 9 digit angka"); return; }
  if (!pin) { error("PIN wajib diisi (bebas)"); return; }
  btn.disabled = true;
  status("menghubungi signaling...");

  ws = new WebSocket(`${SIGNALING_URL}?host=${hostId}`);
  ws.onopen = () => {
    ws.send(JSON.stringify({ type: "client_join", host_id: hostId, pin }));
  };
  ws.onmessage = async (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "join_ok") {
      hostPlan = msg.host?.plan || "free";
      status(`join OK → ${msg.host.name} (${msg.host.platform})`, true);
      if (hostPlan === "premium") {
        badge.style.display = "inline-block";
        status(`join OK → ${msg.host.name} [PREMIUM]`, true);
        await fetchTurn(); // ambil TURN credential sebelum bikin offer
      }
      setupPeer();
      await makeOffer();
    } else if (msg.type === "join_fail") {
      error(msg.reason === "offline" ? "Host offline (PC mati / app host belum jalan)" : "PIN salah");
      btn.disabled = false;
    } else if (msg.type === "signal") {
      const p = msg.payload;
      if (p.type === "answer") {
        await pc.setRemoteDescription({ type: "answer", sdp: p.sdp });
      } else if (p.type === "candidate") {
        try {
          await pc.addIceCandidate({
            candidate: p.candidate,
            sdpMid: p.sdpMid,
            sdpMLineIndex: p.sdpMLineIndex,
          });
        } catch {}
      }
    } else if (msg.type === "host_offline") {
      error("Host offline — koneksi ditutup");
    }
  };
  ws.onclose = () => {
    status("koneksi signaling tertutup");
    btn.disabled = false;
    if (pc) { pc.close(); pc = null; dc = null; }
  };
  ws.onerror = () => error("Gagal hubungi signaling — cek network");
}

btn.addEventListener("click", connect);
pinInput.addEventListener("keydown", (e) => { if (e.key === "Enter") connect(); });
