package com.framecast.app

import android.os.Bundle
import android.view.MotionEvent
import android.view.View
import androidx.appcompat.app.AppCompatActivity
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import org.webrtc.SurfaceViewRenderer

/**
 * Client FrameCast Android — lihat & kontrol PC pakai ID + PIN.
 *
 * Alur: connect WS signaling -> client_join (ID+PIN) -> join_ok ->
 *       WebRtcSession buat offer -> host jawab -> video P2P di renderer ->
 *       input (touch/keyboard) dikirim via DataChannel.
 */
class MainActivity : AppCompatActivity() {

    private lateinit var renderer: SurfaceViewRenderer
    private var ws: WebSocket? = null
    private var rtc: WebRtcSession? = null
    private var input: InputSender? = null

    // GANTI dengan worker kamu: wss://<nama-worker>.workers.dev/ws
    private val signalingUrl = "wss://framecast-signal.akuntiktok76y.workers.dev/ws"

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        renderer = findViewById(R.id.renderer)
        val etId = findViewById<android.widget.EditText>(R.id.etId)
        val etPin = findViewById<android.widget.EditText>(R.id.etPin)
        val btn = findViewById<android.widget.Button>(R.id.btnConnect)
        val tvStatus = findViewById<android.widget.TextView>(R.id.tvStatus)

        // renderer WebRTC (decoding hardware via MediaCodec)
        val egl = org.webrtc.EglBase.create()
        renderer.init(egl.eglBaseContext, null)
        renderer.setMirror(false)
        renderer.setScalingType(org.webrtc.RendererCommon.ScalingType.SCALE_ASPECT_FIT)
        renderer.setEnableHardwareScaler(true)

        btn.setOnClickListener {
            val id = etId.text.toString().trim()
            val pin = etPin.text.toString().trim()
            if (id.length == 9 && pin.length == 6) {
                connect(id, pin, tvStatus)
            } else {
                tvStatus.text = "ID harus 9 digit & PIN 6 digit"
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
                tvStatus.text = "join OK → ${host?.optString("name")} — membangun P2P..."
                rtc = WebRtcSession(applicationContext, renderer, onSignal = { json ->
                    ws?.send(JSONObject().put("type", "signal").put("to", "host").put("payload", json).toString())
                }).also { session ->
                    input = InputSender(session)
                    renderer.setOnTouchListener { _, ev -> input?.onTouch(renderer, ev); true }
                    CoroutineScope(Dispatchers.Main).launch {
                        session.start()
                        tvStatus.text = "offer dikirim, tunggu host..."
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

    // biarkan touch/gesture di renderer tidak memakan keyboard fokus
    override fun onTouchEvent(event: MotionEvent): Boolean = false
}
