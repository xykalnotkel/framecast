package com.framecast.app

import android.app.Activity
import android.os.Bundle
import android.view.MotionEvent
import android.view.View
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONArray
import org.json.JSONObject
import org.webrtc.SurfaceViewRenderer

/**
 * Client FrameCast Android — lihat & kontrol PC pakai ID + PIN.
 *
 * Alur: connect WS signaling -> client_join (ID+PIN) -> join_ok ->
 *       (kalau host PREMIUM, ambil TURN credential dari /api/turn) ->
 *       WebRtcSession buat offer -> host jawab -> video P2P di renderer ->
 *       input (touch/keyboard) dikirim via DataChannel.
 */
class MainActivity : Activity() {

    private lateinit var renderer: SurfaceViewRenderer
    private var ws: WebSocket? = null
    private var rtc: WebRtcSession? = null
    private var input: InputSender? = null
    private var turnIce = JSONArray()

    // GANTI kalau worker berubah
    private val signalingUrl = "wss://framecast-signal.akuntiktok76y.workers.dev/ws"
    private val baseUrl = "https://framecast-signal.akuntiktok76y.workers.dev"

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        renderer = findViewById(R.id.renderer)
        val etId = findViewById<android.widget.EditText>(R.id.etId)
        val etPin = findViewById<android.widget.EditText>(R.id.etPin)
        val btn = findViewById<android.widget.Button>(R.id.btnConnect)
        val tvStatus = findViewById<android.widget.TextView>(R.id.tvStatus)

        val egl = org.webrtc.EglBase.create()
        renderer.init(egl.eglBaseContext, null)
        renderer.setMirror(false)
        renderer.setScalingType(org.webrtc.RendererCommon.ScalingType.SCALE_ASPECT_FIT)
        renderer.setEnableHardwareScaler(true)

        btn.setOnClickListener {
            val id = etId.text.toString().trim()
            val pin = etPin.text.toString().trim()
            if (id.length == 9) {
                if (pin.isNotEmpty()) connect(id, pin, tvStatus)
                else tvStatus.text = "PIN wajib diisi (bebas)"
            } else {
                tvStatus.text = "ID harus 9 digit angka"
            }
        }
    }

    private fun connect(hostId: String, pin: String, tvStatus: android.widget.TextView) {
        tvStatus.text = "menghubungi signaling..."
        val client = OkHttpClient()
        val request = Request.Builder().url("$signalingUrl?host=$hostId").build()
        ws = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                val join = JSONObject()
                    .put("type", "client_join")
                    .put("host_id", hostId)
                    .put("pin", pin)
                webSocket.send(join.toString())
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                runOnUiThread { handleSignal(text, tvStatus) }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                runOnUiThread { tvStatus.text = "gagal hubungi signaling: ${t.message}" }
            }
        })
    }

    private fun handleSignal(raw: String, tvStatus: android.widget.TextView) {
        val msg = JSONObject(raw)
        when (msg.optString("type")) {
            "join_ok" -> {
                val host = msg.optJSONObject("host")
                val plan = host?.optString("plan") ?: "free"
                tvStatus.text = "join OK → ${host?.optString("name")}" +
                    (if (plan == "premium") " [PREMIUM]" else "") + " — membangun P2P..."
                CoroutineScope(Dispatchers.IO).launch {
                    if (plan == "premium") {
                        turnIce = fetchTurn(hostId())
                    }
                    CoroutineScope(Dispatchers.Main).launch {
                        startSession(tvStatus)
                    }
                }
            }
            "join_fail" -> tvStatus.text = if (msg.optString("reason") == "offline")
                "Host offline" else "PIN salah"
            "signal" -> {
                val p = msg.optJSONObject("payload")
                when (p?.optString("type")) {
                    "answer" -> rtc?.handleAnswer(p.optString("sdp"))
                    "candidate" -> rtc?.handleCandidate(
                        p.optString("candidate"), p.optString("sdpMid"), p.optInt("sdpMLineIndex")
                    )
                }
            }
            "host_offline" -> tvStatus.text = "Host offline — koneksi ditutup"
        }
    }

    private fun hostId(): String =
        findViewById<android.widget.EditText>(R.id.etId).text.toString().trim()

    private fun fetchTurn(hostId: String): JSONArray {
        return try {
            val client = OkHttpClient()
            val req = Request.Builder().url("$baseUrl/api/turn?host=$hostId").build()
            client.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) return JSONArray()
                val j = JSONObject(resp.body?.string() ?: "{}")
                j.optJSONArray("iceServers") ?: JSONArray()
            }
        } catch (e: Exception) {
            JSONArray()
        }
    }

    private fun startSession(tvStatus: android.widget.TextView) {
        val session = WebRtcSession(applicationContext, renderer, onSignal = { json ->
            ws?.send(JSONObject().put("type", "signal").put("to", "host").put("payload", json).toString())
        })
        session.extraIce = turnIce // TURN (kalau host premium)
        rtc = session
        input = InputSender(session)
        renderer.setOnTouchListener { _, ev -> input?.onTouch(renderer, ev); true }
        CoroutineScope(Dispatchers.Main).launch {
            session.start()
            tvStatus.text = "offer dikirim, tunggu host..."
        }
    }

    override fun dispatchKeyEvent(event: android.view.KeyEvent): Boolean {
        input?.onKey(event)
        return super.dispatchKeyEvent(event)
    }

    override fun onDestroy() {
        rtc?.close()
        ws?.close(1000, null)
        renderer.release()
        super.onDestroy()
    }

    override fun onTouchEvent(event: MotionEvent): Boolean = false
}
