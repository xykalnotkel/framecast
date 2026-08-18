package com.framecast.app

import android.content.Context
import org.json.JSONObject
import org.webrtc.DataChannel
import org.webrtc.EglBase
import org.webrtc.IceCandidate
import org.webrtc.MediaStreamTrack
import org.webrtc.PeerConnection
import org.webrtc.PeerConnectionFactory
import org.webrtc.RtpTransceiver
import org.webrtc.SessionDescription
import org.webrtc.SurfaceViewRenderer
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
    private val onSignal: (JSONObject) -> Unit,
) {
    private var pc: PeerConnection? = null
    private var dc: DataChannel? = null
    private val executor = Executors.newSingleThreadExecutor()

    companion object {
        @Volatile
        private var factory: PeerConnectionFactory? = null

        private fun factory(context: Context): PeerConnectionFactory {
            var f = factory
            if (f == null) {
                synchronized(this) {
                    f = factory
                    if (f == null) {
                        PeerConnectionFactory.initialize(
                            PeerConnectionFactory.InitializationOptions.builder(context)
                                .createInitializationOptions()
                        )
                        val eglBase = EglBase.create()
                        f = PeerConnectionFactory.builder()
                            .setVideoEncoderFactory(org.webrtc.DefaultVideoEncoderFactory(
                                eglBase.eglBaseContext, true, true))
                            .setVideoDecoderFactory(org.webrtc.DefaultVideoDecoderFactory(
                                eglBase.eglBaseContext))
                            .createPeerConnectionFactory()
                        factory = f
                    }
                }
            }
            return f
        }
    }

    fun start() {
        val rtcConfig = PeerConnection.RTCConfiguration(listOf(
            org.webrtc.PeerConnection.IceServer.builder("stun:stun.cloudflare.com:3478").createIceServer(),
            org.webrtc.PeerConnection.IceServer.builder("stun:stun.l.google.com:19302").createIceServer(),
            // TURN wajib kalau P2P gagal (CGNAT seluler). Self-host coturn:
            // org.webrtc.PeerConnection.IceServer.builder("turn:host:3478")
            //   .setUsername("u").setPassword("p").createIceServer()
        ))
        val connection = factory(appContext).createPeerConnection(rtcConfig, object : PeerConnection.Observer {
            override fun onIceCandidate(candidate: IceCandidate) {
                onSignal(JSONObject()
                    .put("type", "candidate")
                    .put("candidate", candidate.sdp)
                    .put("sdpMid", candidate.sdpMid)
                    .put("sdpMLineIndex", candidate.sdpMLineIndex))
            }

            override fun onTrack(receiver: org.webrtc.RtpReceiver) {
                val track = receiver.track()
                if (track?.kind() == MediaStreamTrack.VIDEO_TRACK_KIND) {
                    track.addSink(renderer) // render ke SurfaceViewRenderer (GPU)
                }
            }

            override fun onConnectionChange(state: PeerConnection.PeerConnectionState) {
                android.util.Log.i("FrameCast", "P2P state: $state")
            }
        }) ?: return
        pc = connection

        // kita hanya menerima video dari host
        connection.addTransceiver(MediaStreamTrack.VIDEO_TRACK_KIND,
            RtpTransceiver.RtpTransceiverDirection.RECV_ONLY)

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
