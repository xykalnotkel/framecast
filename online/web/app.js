/**
 * FrameCast Online — client Web (viewer + input).
 *
 * Tanpa library: pakai RTCPeerConnection native + WebSocket.
 * Ganti SIGNALING_URL ke worker kamu (wss://<nama-worker>.workers.dev/ws).
 *
 * Alur: join pakai ID+PIN -> offer WebRTC -> P2P video dari host ->
 *       kirim input (mouse/keyboard) via DataChannel.
 */

// ====== konfigurasi ======
const SIGNALING_URL = "wss://framecast-signal.akuntiktok76y.workers.dev/ws"; // GANTI
const STUN_SERVERS = [
  { urls: "stun:stun.cloudflare.com:3478" },
  { urls: "stun:stun.l.google.com:19302" },
];
// TURN diperlukan kalau NAT/CGNAT nggak lolos P2P (sering di jaringan seluler ID).
// Testing gratis: https://www.metered.ca/tools/openrelay/  |  Produksi: self-host coturn.
const TURN_URL = null; // contoh: { urls:"turn:openrelay.metered.ca:80", username:"...", credential:"..." }

// ====== elemen UI ======
const $ = (id) => document.getElementById(id);
const btn = $("btn"), idInput = $("id"), pinInput = $("pin");
const statusEl = $("status"), errEl = $("err"), stage = $("stage"), video = $("video");

let ws = null;        // koneksi signaling
let pc = null;        // RTCPeerConnection
let dc = null;        // DataChannel input
let seq = 0;

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
  // video object-fit:contain → koordinat relatif ke area video yang terlihat
  return {
    x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)),
    y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)),
  };
}

video.addEventListener("pointermove", (e) => {
  if (e.buttons === 1) { // drag = gerak + tombol kiri
    sendInput("mousemove", normalize(e));
  }
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
  if (TURN_URL) list.push(TURN_URL);
  return list;
}

function setupPeer() {
  pc = new RTCPeerConnection({ iceServers: iceServers() });
  pc.addTransceiver("video", { direction: "recvonly" }); // kita terima video dari host

  pc.ontrack = (ev) => {
    video.srcObject = new MediaStream([ev.track]);
    stage.style.display = "block";
    status("terhubung — video P2P", true);
  };

  pc.ondatachannel = (ev) => {
    dc = ev.channel;
    dc.onmessage = (m) => {
      try {
        const j = JSON.parse(m.data);
        if (j.type === "hello") status(`terhubung ke ${j.name || "host"}`, true);
      } catch {}
    };
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
    if (pc.connectionState === "failed") error("P2P gagal — butuh TURN di jaringan ini");
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
  if (!/^\d{9}$/.test(hostId) || !/^\d{6}$/.test(pin)) {
    error("ID 9 digit & PIN 6 digit");
    return;
  }
  btn.disabled = true;
  status("menghubungi signaling...");

  ws = new WebSocket(`${SIGNALING_URL}?host=${hostId}`);
  ws.onopen = () => {
    ws.send(JSON.stringify({ type: "client_join", host_id: hostId, pin }));
  };
  ws.onmessage = async (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "join_ok") {
      status(`join OK → ${msg.host.name} (${msg.host.platform})`, true);
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
  ws.onerror = () => error("Gagal hubungi signaling — cek SIGNALING_URL & network");
}

btn.addEventListener("click", connect);
pinInput.addEventListener("keydown", (e) => { if (e.key === "Enter") connect(); });
