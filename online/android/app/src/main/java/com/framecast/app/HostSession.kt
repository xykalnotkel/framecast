package com.framecast.app

import android.content.Context
import android.media.projection.MediaProjection
import org.json.JSONObject
import org.webrtc.DataChannel
import org.webrtc.EglBase
import org.webrtc.IceCandidate
import org.webrtc.MediaStream
import org.webrtc.MediaStreamTrack
import org.webrtc.PeerConnection
import org.webrtc.PeerConnectionFactory
import org.webrtc.RtpReceiver
import org.webrtc.RtpTransceiver
import org.webrtc.SessionDescription
import org.webrtc.VideoSource
import org.webrtc.VideoTrack
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import java.util.concurrent.Executors

/**
 * HOST di HP: daftar ke signaling pakai akun (device_type=phone), tunggu
 * client (akun sama, premium), lalu stream layar via MediaProjection.
 *
 * Mirip host_rtc.py di Python — tapi di sisi Android.
 */
class HostSession(
    private val appContext: Context,
    private val signalingUrl: String,
    private val hostId: String,
    private val token: String,
    private val deviceModel: String,
    private val mediaProjection: MediaProjection,
    private val onStatus: (String) -> Unit,
) {
    private var ws: WebSocket? = null
    private var pc: PeerConnection? = null
    private var dc: DataChannel? = null
    private var videoSource: VideoSource? = null
    private val executor = Executors.newSingleThreadExecutor()

    companion object {
        @Volatile
        private var factory: PeerConnectionFactory? = null
        private fun factory(context: Context): PeerConnectionFactory {
            factory?.let { return it }
            synchronized(this) {
                factory?.let { return it }
                PeerConnectionFactory.initialize(
                    PeerConnectionFactory.InitializationOptions.builder(context)
                        .createInitializationOptions()
                )
                val eglBase = EglBase.create()
                val created = PeerConnectionFactory.builder()
                    .setVideoEncoderFactory(org.webrtc.DefaultVideoEncoderFactory(
                        eglBase.eglBaseContext, true, true))
                    .setVideoDecoderFactory(org.webrtc.DefaultVideoDecoderFactory(
                        eglBase.eglBaseContext))
                    .createPeerConnectionFactory()
                factory = created
                return created
            }
        }
    }

    fun start() {
        onStatus("menunggu client (akun sama)...")
        val client = OkHttpClient()
        val req = Request.Builder().url("$signalingUrl?host=$hostId").build()
        ws = client.newWebSocket(req, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                val reg = JSONObject()
                    .put("type", "host_register")
                    .put("host_id", hostId)
                    .put("name", "HP " + deviceModel)
                    .put("platform", "android")
                    .put("device_type", "phone")
                    .put("model", deviceModel)
                    .put("account_token", token)
                    .put("pin_hash", "")
                    .put("salt", "")
                webSocket.send(reg.toString())
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                handleSignal(text)
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                onStatus("signaling gagal: ${t.message}")
            }
        })
    }

    private fun handleSignal(raw: String) {
        val msg = JSONObject(raw)
        when (msg.optString("type")) {
            "registered" -> onStatus("HP terdaftar — tunggu client connect...")
            "client_joined" -> setupPeer()
            "signal" -> {
                val p = msg.optJSONObject("payload")
                when (p?.optString("type")) {
                    "offer" -> handleOffer(p.optString("sdp"))
                    "candidate" -> pc?.addIceCandidate(IceCandidate(
                        p.optString("sdpMid"), p.optInt("sdpMLineIndex"), p.optString("candidate")))
                }
            }
        }
    }

    private fun setupPeer() {
        if (pc != null) return
        onStatus("client masuk — mulai stream layar...")
        val f = factory(appContext)
        videoSource = f.createVideoSource(true /* isScreencast */)
        val videoTrack: VideoTrack = videoSource!!.createVideoTrack("screen0")

        val rtcConfig = PeerConnection.RTCConfiguration(listOf(
            org.webrtc.PeerConnection.IceServer.builder("stun:stun.cloudflare.com:3478").createIceServer(),
            org.webrtc.PeerConnection.IceServer.builder("stun:stun.l.google.com:19302").createIceServer(),
        ))
        val connection = f.createPeerConnection(rtcConfig, object : PeerConnection.Observer {
            override fun onSignalingChange(state: PeerConnection.SignalingState) = Unit
            override fun onIceConnectionChange(state: PeerConnection.IceConnectionState) = Unit
            override fun onIceConnectionReceivingChange(receiving: Boolean) = Unit
            override fun onIceGatheringChange(state: PeerConnection.IceGatheringState) = Unit
            override fun onIceCandidatesRemoved(candidates: Array<IceCandidate>) = Unit
            override fun onAddStream(stream: MediaStream) = Unit
            override fun onRemoveStream(stream: MediaStream) = Unit
            override fun onDataChannel(channel: DataChannel) = Unit
            override fun onRenegotiationNeeded() = Unit
            override fun onIceCandidate(candidate: IceCandidate) {
                ws?.send(JSONObject()
                    .put("type", "signal").put("to", "host")
                    .put("payload", JSONObject()
                        .put("type", "candidate")
                        .put("candidate", candidate.sdp)
                        .put("sdpMid", candidate.sdpMid)
                        .put("sdpMLineIndex", candidate.sdpMLineIndex))
                    .toString())
            }
            override fun onTrack(transceiver: RtpTransceiver) = Unit
            override fun onConnectionChange(state: PeerConnection.PeerConnectionState) {
                android.util.Log.i("FrameCast", "host P2P: $state")
            }
        }) ?: return
        pc = connection
        connection.addTrack(videoTrack, listOf())

        // DataChannel input (id 0) — terima input dari client (bisa diabaikan dulu)
        val init = DataChannel.Init().apply { negotiated = true; id = 0 }
        dc = connection.createDataChannel("input", init)
        dc?.registerObserver(object : DataChannel.Observer {
            override fun onBufferedAmountChange(previousAmount: Long) {}
            override fun onStateChange() {}
            override fun onMessage(buffer: DataChannel.Buffer) {
                // TODO: inject input ke HP (butuh AccessibilityService) — fase berikutnya
            }
        })

        // mulai tangkap layar
        val w = appContext.resources.displayMetrics.widthPixels
        val h = appContext.resources.displayMetrics.heightPixels
        val dpi = appContext.resources.displayMetrics.densityDpi
        val capturer = ScreenCapturer(mediaProjection, w, h, dpi)
        videoSource!!.adaptOutputFormat(w, h, 30)
        videoSource!!.setVideoCapturer(capturer)
    }

    private fun handleOffer(sdp: String) {
        val connection = pc ?: return
        executor.execute {
            connection.setRemoteDescription(object : org.webrtc.SdpObserver {
                override fun onCreateSuccess(desc: SessionDescription) = Unit
                override fun onCreateFailure(error: String) = Unit
                override fun onSetSuccess() {
                    connection.createAnswer(object : org.webrtc.SdpObserver {
                        override fun onCreateSuccess(desc: SessionDescription) {
                            connection.setLocalDescription(this, desc)
                            ws?.send(JSONObject()
                                .put("type", "signal").put("to", "host")
                                .put("payload", JSONObject()
                                    .put("type", "answer").put("sdp", connection.localDescription.description))
                                .toString())
                        }
                        override fun onCreateFailure(error: String) = Unit
                        override fun onSetSuccess() = Unit
                        override fun onSetFailure(error: String) = Unit
                    }, org.webrtc.MediaConstraints())
                }
                override fun onSetFailure(error: String) = Unit
            }, SessionDescription(SessionDescription.Type.OFFER, sdp))
        }
    }

    fun close() {
        ws?.close(1000, null)
        pc?.close()
        videoSource?.dispose()
        executor.shutdown()
    }
}
