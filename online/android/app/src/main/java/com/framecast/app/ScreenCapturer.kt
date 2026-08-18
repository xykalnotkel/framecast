package com.framecast.app

import android.content.Context
import android.graphics.PixelFormat
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.ImageReader
import android.media.projection.MediaProjection
import android.os.Handler
import android.os.HandlerThread
import android.view.Surface
import org.webrtc.CapturerObserver
import org.webrtc.SurfaceTextureHelper
import org.webrtc.VideoCapturer
import java.nio.ByteBuffer

/**
 * ScreenCapturer — tangkap layar HP (MediaProjection) terus feed ke WebRTC
 * VideoSource (format NV21, kayak kamera). Dipakai buat mode HOST di HP.
 *
 * Ini setara "capture" di host PC (DXGI/mss), tapi buat Android:
 * MediaProjection + VirtualDisplay + ImageReader.
 */
class ScreenCapturer(
    private val mediaProjection: MediaProjection,
    private val width: Int,
    private val height: Int,
    private val dpi: Int,
) : VideoCapturer {

    override fun isScreencast(): Boolean = true

    private var observer: CapturerObserver? = null
    private var imageReader: ImageReader? = null
    private var virtualDisplay: VirtualDisplay? = null
    private var handlerThread: HandlerThread? = null
    private var handler: Handler? = null
    private var nv21 = ByteArray(0)

    override fun initialize(
        surfaceTextureHelper: SurfaceTextureHelper?,
        context: Context?,
        capturerObserver: CapturerObserver
    ) {
        observer = capturerObserver
    }

    override fun startCapture(width: Int, height: Int, framerate: Int) {
        handlerThread = HandlerThread("ScreenCapturer").also { it.start() }
        handler = Handler(handlerThread!!.looper)
        nv21 = ByteArray(this.width * this.height * 3 / 2)

        imageReader = ImageReader.newInstance(this.width, this.height, PixelFormat.RGBA_8888, 2)
        imageReader?.setOnImageAvailableListener({ reader -> onImage(reader) }, handler)

        virtualDisplay = mediaProjection.createVirtualDisplay(
            "FrameCastCapture",
            this.width, this.height, this.dpi,
            DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
            imageReader!!.surface,
            null, handler
        )
        observer?.onCapturerStarted(true)
    }

    private fun onImage(reader: ImageReader) {
        val image = reader.acquireLatestImage() ?: return
        try {
            val plane = image.planes[0]
            val buf = plane.buffer
            val rowStride = plane.rowStride
            val w = image.width
            val h = image.height
            val ySize = w * h
            buf.rewind()
            // RGBA -> NV21 (Y plane + interleaved VU)
            for (y in 0 until h) {
                val rowStart = y * rowStride
                val outRow = y * w
                for (x in 0 until w) {
                    val i = rowStart + x * 4
                    val r = buf.get(i).toInt() and 0xFF
                    val g = buf.get(i + 1).toInt() and 0xFF
                    val b = buf.get(i + 2).toInt() and 0xFF
                    nv21[outRow + x] = (((66 * r + 129 * g + 25 * b + 128) shr 8) + 16).toByte()
                    if (y % 2 == 0 && x % 2 == 0) {
                        val uv = ySize + (y / 2) * w + (x / 2) * 2
                        nv21[uv] = (((112 * r - 94 * g - 18 * b + 128) shr 8) + 128).toByte()
                        nv21[uv + 1] = (((-38 * r - 74 * g + 112 * b + 128) shr 8) + 128).toByte()
                    }
                }
            }
            // bungkus NV21 -> VideoFrame -> observer (SDK M125 cuma onFrameCaptured)
            val buffer = org.webrtc.NV21Buffer(nv21, w, h, null)
            val frame = org.webrtc.VideoFrame(buffer, 0, System.nanoTime())
            observer?.onFrameCaptured(frame)
            frame.release()
        } catch (e: Exception) {
            android.util.Log.e("FrameCast", "capture frame gagal", e)
        } finally {
            image.close()
        }
    }

    @Throws(InterruptedException::class)
    override fun stopCapture() {
        virtualDisplay?.release()
        imageReader?.close()
        handlerThread?.quitSafely()
        virtualDisplay = null
        imageReader = null
        handlerThread = null
    }

    override fun changeCaptureFormat(width: Int, height: Int, framerate: Int) = Unit
    override fun dispose() = stopCapture()
}
