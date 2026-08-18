package com.framecast.app

import android.content.Context
import org.json.JSONArray
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
import org.webrtc.SurfaceViewRenderer
import org.webrtc.VideoTrack
import java.nio.ByteBuffer
import java.nio.charset.StandardCharsets
import java.util.concurrent.Executors

/**
 * Bungkus RTCPeerConnection (WebRTC resmi, decoding hardware otomatis).
 *
 * Mirip peran host_rtc.py di sisi server: offer dari client (recvonly video),
 * DataChannel "input" ter-negosiasi id=0, dan kirim SDP/ICE via callback.
 */
class WebRtcSession(
    private val appContext: Context,
    private val renderer: SurfaceViewRenderer,
    extraIce: JSONArray = JSONArray(),
    private val onSignal: (JSONObject) -> Unit,
) {
    private var pc: PeerConnection? = null
    private var dc: DataChannel? = null
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
        // STUN gratis + TURN Cloudflare (kalau host premium, dari /api/turn)
        val servers = mutableListOf(
            org.webrtc.PeerConnection.IceServer.builder("stun:stun.cloudflare.com:3478").createIceServer(),
            org.webrtc.PeerConnection.IceServer.builder("stun:stun.l.google.com:19302").createIceServer(),
        )
        for (i in 0 until extraIce.length()) {
            val item = extraIce.optJSONObject(i) ?: continue
            val urls = item.optJSONArray("urls") ?: continue
            for (u in 0 until urls.length()) {
                try {
                    val b = org.webrtc.PeerConnection.IceServer.builder(urls.getString(u))
                    if (item.has("username")) b.setUsername(item.getString("username"))
                    if (item.has("credential")) b.setPassword(item.getString("credential"))
                    servers.add(b.createIceServer())
                } catch (e: Exception) { /* skip url yang gagal */ }
            }
        }
        val rtcConfig = PeerConnection.RTCConfiguration(servers)
        val connection = factory(appContext).createPeerConnection(rtcConfig, object : PeerConnection.Observer {
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
                onSignal(JSONObject()
                    .put("type", "candidate")
                    .put("candidate", candidate.sdp)
                    .put("sdpMid", candidate.sdpMid)
                    .put("sdpMLineIndex", candidate.sdpMLineIndex))
            }

            override fun onTrack(transceiver: RtpTransceiver) {
                val track = transceiver.receiver.track()
                if (track?.kind() == MediaStreamTrack.VIDEO_TRACK_KIND && track is VideoTrack) {
                    track.addSink(renderer) // render ke SurfaceViewRenderer (GPU)
                }
            }

            override fun onConnectionChange(state: PeerConnection.PeerConnectionState) {
                android.util.Log.i("FrameCast", "P2P state: $state")
            }
        }) ?: return
        pc = connection

        // kita hanya menerima video dari host
        connection.addTransceiver(
            MediaStreamTrack.MediaType.MEDIA_TYPE_VIDEO,
            RtpTransceiver.RtpTransceiverInit(RtpTransceiver.RtpTransceiverDirection.RECV_ONLY))

        // DataChannel input (ter-negosiasi, id 0 — sama dengan host)
        val init = DataChannel.Init().apply {
            negotiated = true
            id = 0
        }
        dc = connection.createDataChannel("input", init)
        dc?.registerObserver(object : DataChannel.Observer {
            override fun onBufferedAmountChange(previousAmount: Long) {}
            override fun onStateChange() {}
            override fun onMessage(buffer: DataChannel.Buffer) {
                val bytes = ByteArray(buffer.data.remaining())
                buffer.data.get(bytes)
                val msg = String(bytes, StandardCharsets.UTF_8)
                if (msg.contains("\"type\":\"hello\"")) {
                    android.util.Log.i("FrameCast", "host menyapa: $msg")
                }
            }
        })

        // offer
        executor.execute {
            connection.createOffer(object : org.webrtc.SdpObserver {
                override fun onCreateSuccess(desc: SessionDescription) {
                    connection.setLocalDescription(this, desc)
                    onSignal(JSONObject().put("type", "offer").put("sdp", desc.description))
                }
                override fun onCreateFailure(error: String) = Unit
                override fun onSetSuccess() = Unit
                override fun onSetFailure(error: String) = Unit
            }, org.webrtc.MediaConstraints())
        }
    }

    fun handleAnswer(sdp: String) {
        pc?.setRemoteDescription(object : org.webrtc.SdpObserver {
            override fun onCreateSuccess(desc: SessionDescription) = Unit
            override fun onCreateFailure(error: String) = Unit
            override fun onSetSuccess() = Unit
            override fun onSetFailure(error: String) = Unit
        }, SessionDescription(SessionDescription.Type.ANSWER, sdp))
    }

    fun handleCandidate(sdp: String, sdpMid: String, sdpMLineIndex: Int) {
        pc?.addIceCandidate(IceCandidate(sdpMid, sdpMLineIndex, sdp))
    }

    fun send(json: JSONObject) {
        val buf = ByteBuffer.wrap(json.toString().toByteArray(StandardCharsets.UTF_8))
        dc?.send(DataChannel.Buffer(buf, false))
    }

    fun close() {
        dc?.close()
        pc?.close()
        executor.shutdown()
    }
}
