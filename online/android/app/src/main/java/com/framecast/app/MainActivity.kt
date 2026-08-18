package com.framecast.app

import android.app.Activity
import android.content.Intent
import android.graphics.Color
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Bundle
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
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
 * FrameCast Android:
 *   - LOGIN akun (email+password) — akun SAMA dengan device tujuan
 *   - DAFTAR device milik akun (PC = gratis, HP = premium) -> connect
 *   - JADIKAN HP HOST — stream layar HP ke client (akun sama, premium)
 */
class MainActivity : Activity() {

    private lateinit var renderer: SurfaceViewRenderer
    private lateinit var etEmail: EditText
    private lateinit var etPass: EditText
    private lateinit var tvStatus: TextView
    private lateinit var devList: LinearLayout
    private val scope = CoroutineScope(Dispatchers.Main + SupervisorJob())

    private var ws: WebSocket? = null
    private var rtc: WebRtcSession? = null
    private var input: InputSender? = null
    private var token: String? = null
    private var turnIce = JSONArray()
    private var hostSession: HostSession? = null
    private var mediaProjection: MediaProjection? = null
    private var auth: AuthApi? = null

    private val signalingUrl = "wss://framecast-signal.akuntiktok76y.workers.dev/ws"
    private val baseUrl = "https://framecast-signal.akuntiktok76y.workers.dev"
    private val projReqCode = 9001

    private val deviceModel: String
        get() = "${Build.MANUFACTURER} ${Build.MODEL}".trim()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        auth = AuthApi(baseUrl)

        renderer = findViewById(R.id.renderer)
        etEmail = findViewById(R.id.etEmail)
        etPass = findViewById(R.id.etPass)
        tvStatus = findViewById(R.id.tvStatus)
        devList = findViewById(R.id.devList)

        val egl = org.webrtc.EglBase.create()
        renderer.init(egl.eglBaseContext, null)
        renderer.setMirror(false)
        renderer.setScalingType(org.webrtc.RendererCommon.ScalingType.SCALE_ASPECT_FIT)
        renderer.setEnableHardwareScaler(true)

        findViewById<Button>(R.id.btnLogin).setOnClickListener { doAuth(register = false) }
        findViewById<Button>(R.id.btnRegister).setOnClickListener { doAuth(register = true) }
        findViewById<Button>(R.id.btnHost).setOnClickListener { startHost() }

        renderer.setOnTouchListener { _, ev -> input?.onTouch(renderer, ev); true }
    }

    private fun doAuth(register: Boolean) {
        val email = etEmail.text.toString().trim()
        val password = etPass.text.toString()
        if (!android.util.Patterns.EMAIL_ADDRESS.matcher(email).matches() || password.length < 6) {
            tvStatus.text = "Email valid & password min 6 karakter"
            return
        }
        val device = JSONObject()
            .put("type", "phone")
            .put("model", deviceModel)
            .put("platform", "android")
            .put("name", "HP $deviceModel")
        scope.launch(Dispatchers.IO) {
            val res = if (register) auth!!.register(email, password, device)
                      else auth!!.login(email, password, device)
            launch(Dispatchers.Main) {
                if (res.optString("error").isNotEmpty()) {
                    tvStatus.text = res.optString("message", res.optString("error"))
                } else {
                    token = res.getString("token")
                    val plan = res.optString("plan", "free")
                    tvStatus.text = "masuk: $email (${if (plan == "premium") "PREMIUM" else "free"})"
                    loadDevices()
                }
            }
        }
    }

    private fun loadDevices() {
        val tk = token ?: return
        scope.launch(Dispatchers.IO) {
            val devs = auth!!.devices(tk)
            launch(Dispatchers.Main) { renderDevices(devs) }
        }
    }

    private fun renderDevices(devs: JSONArray) {
        devList.removeAllViews()
        if (devs.length() == 0) {
            tvStatus.text = "Belum ada device. Login akun sama di PC (host) atau jadikan HP ini host."
            return
        }
        for (i in 0 until devs.length()) {
            val d = devs.getJSONObject(i)
            val isPhone = d.optString("type") == "phone"
            val row = TextView(this).apply {
                text = "${if (isPhone) "HP" else "PC"} · ${d.optString("name", d.optString("host_id"))}\n" +
                    "   ${d.optString("model")} · ${if (d.optBoolean("online")) "ONLINE" else "offline"}" +
                    (if (isPhone) " · [PREMIUM]" else " · [gratis]")
                textSize = 14f
                setPadding(16, 14, 16, 14)
                gravity = Gravity.CENTER_VERTICAL
                setTextColor(Color.WHITE)
                background = android.graphics.drawable.GradientDrawable().apply {
                    setColor(0xFF171B26.toInt())
                    cornerRadius = 16f
                    setStroke(2, if (d.optBoolean("online")) 0xFF2FD06A.toInt() else 0xFF262C3B.toInt())
                }
                setOnClickListener { connectTo(d) }
            }
            val lp = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
            ).apply { bottomMargin = 16 }
            devList.addView(row, lp)
        }
    }

    // ---------- client: connect ke device (token, tanpa PIN) ----------
    private fun connectTo(dev: JSONObject) {
        val hostId = dev.optString("host_id")
        val tk = token ?: return
        tvStatus.text = "connect ke ${dev.optString("name")}..."
        val isPhone = dev.optString("type") == "phone"
        if (isPhone) {
            scope.launch(Dispatchers.IO) {
                turnIce = fetchTurn(hostId)
                launch(Dispatchers.Main) { openClient(hostId, tk) }
            }
        } else {
            openClient(hostId, tk)
        }
    }

    private fun fetchTurn(hostId: String): JSONArray {
        return try {
            val client = OkHttpClient()
            val req = Request.Builder().url("$baseUrl/api/turn?host=$hostId").build()
            client.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) return JSONArray()
                val j = JSONObject(resp.body?.string() ?: "{}")
                j.optJSONArray("iceServers") ?: JSONArray()
            }
        } catch (e: Exception) { JSONArray() }
    }

    private fun openClient(hostId: String, tk: String) {
        val client = OkHttpClient()
        val req = Request.Builder().url("$signalingUrl?host=$hostId").build()
        ws = client.newWebSocket(req, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                webSocket.send(JSONObject()
                    .put("type", "client_join").put("host_id", hostId).put("token", tk)
                    .toString())
            }
            override fun onMessage(webSocket: WebSocket, text: String) {
                runOnUiThread { handleClientSignal(text) }
            }
            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                runOnUiThread { tvStatus.text = "gagal hubungi signaling" }
            }
        })
    }

    private fun handleClientSignal(raw: String) {
        val msg = JSONObject(raw)
        when (msg.optString("type")) {
            "join_ok" -> {
                val h = msg.optJSONObject("host")
                tvStatus.text = "terhubung ke ${h?.optString("name")}"
                val session = WebRtcSession(applicationContext, renderer, onSignal = { json ->
                    ws?.send(JSONObject().put("type", "signal").put("to", "host").put("payload", json).toString())
                })
                session.extraIce = turnIce
                rtc = session
                input = InputSender(session)
                session.start()
            }
            "join_fail" -> {
                val reason = msg.optString("reason")
                tvStatus.text = when (reason) {
                    "premium_required" -> "DITOLAK: remote HP butuh akun PREMIUM"
                    "not_yours" -> "DITOLAK: bukan device akun kamu"
                    "offline" -> "Host offline"
                    else -> "Gagal: $reason"
                }
            }
            "signal" -> {
                val p = msg.optJSONObject("payload")
                when (p?.optString("type")) {
                    "answer" -> rtc?.handleAnswer(p.optString("sdp"))
                    "candidate" -> rtc?.handleCandidate(
                        p.optString("candidate"), p.optString("sdpMid"), p.optInt("sdpMLineIndex"))
                }
            }
            "host_offline" -> tvStatus.text = "Host offline — koneksi ditutup"
        }
    }

    // ---------- host: jadikan HP ini host (MediaProjection) ----------
    private fun startHost() {
        if (token == null) { tvStatus.text = "Login dulu dengan akun"; return }
        val mpm = getSystemService(MediaProjectionManager::class.java)
        startActivityForResult(mpm.createScreenCaptureIntent(), projReqCode)
    }

    @Deprecated("pakai startActivityForResult klasik biar tanpa dependency androidx")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == projReqCode) {
            if (resultCode == Activity.RESULT_OK && data != null) {
                val mpm = getSystemService(MediaProjectionManager::class.java)
                mediaProjection = mpm.getMediaProjection(resultCode, data)
                val hostId = token!!.hashCode().toString().replace("-", "1").padEnd(9, '7').take(9)
                hostSession = HostSession(
                    applicationContext, signalingUrl, hostId, token!!, deviceModel, mediaProjection!!
                ) { s -> runOnUiThread { tvStatus.text = s } }
                hostSession?.start()
            } else {
                tvStatus.text = "Izin tangkap layar ditolak"
            }
        }
    }

    override fun dispatchKeyEvent(event: android.view.KeyEvent): Boolean {
        input?.onKey(event)
        return super.dispatchKeyEvent(event)
    }

    override fun onDestroy() {
        hostSession?.close()
        rtc?.close()
        ws?.close(1000, null)
        renderer.release()
        scope.cancel()
        super.onDestroy()
    }

    override fun onTouchEvent(event: MotionEvent): Boolean = false
}
