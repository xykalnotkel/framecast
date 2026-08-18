package com.framecast.app

import android.view.KeyEvent
import android.view.MotionEvent
import android.view.View
import org.json.JSONObject

/**
 * Ubah sentuhan & tombol Android -> pesan input JSON -> DataChannel -> host.
 * Skema sama dengan online/msgproto.py (di-inject host lewat SendInput).
 */
class InputSender(private val session: WebRtcSession) {

    private var seq = 0

    fun onTouch(view: View, ev: MotionEvent) {
        val x = ev.x / view.width
        val y = ev.y / view.height
        when (ev.actionMasked) {
            MotionEvent.ACTION_DOWN ->
                send("mousedown", mapOf("button" to "left", "x" to x, "y" to y))
            MotionEvent.ACTION_MOVE -> send("mousemove", mapOf("x" to x, "y" to y))
            MotionEvent.ACTION_UP ->
                send("mouseup", mapOf("button" to "left", "x" to x, "y" to y))
            MotionEvent.ACTION_SCROLL ->
                send("wheel", mapOf("dy" to (ev.getAxisValue(MotionEvent.AXIS_VSCROLL) * -1)))
        }
    }

    fun onKey(event: KeyEvent): Boolean {
        val code = keyToWebCode(event.keyCode) ?: return false
        val action = if (event.action == KeyEvent.ACTION_DOWN) "keydown" else "keyup"
        send(action, mapOf("code" to code))
        return true
    }

    /** Map keyCode Android -> KeyboardEvent.code ala browser (host sudah paham). */
    private fun keyToWebCode(keyCode: Int): String? = when (keyCode) {
        KeyEvent.KEYCODE_A -> "KeyA"; KeyEvent.KEYCODE_B -> "KeyB"
        KeyEvent.KEYCODE_C -> "KeyC"; KeyEvent.KEYCODE_D -> "KeyD"
        KeyEvent.KEYCODE_E -> "KeyE"; KeyEvent.KEYCODE_F -> "KeyF"
        KeyEvent.KEYCODE_G -> "KeyG"; KeyEvent.KEYCODE_H -> "KeyH"
        KeyEvent.KEYCODE_I -> "KeyI"; KeyEvent.KEYCODE_J -> "KeyJ"
        KeyEvent.KEYCODE_K -> "KeyK"; KeyEvent.KEYCODE_L -> "KeyL"
        KeyEvent.KEYCODE_M -> "KeyM"; KeyEvent.KEYCODE_N -> "KeyN"
        KeyEvent.KEYCODE_O -> "KeyO"; KeyEvent.KEYCODE_P -> "KeyP"
        KeyEvent.KEYCODE_Q -> "KeyQ"; KeyEvent.KEYCODE_R -> "KeyR"
        KeyEvent.KEYCODE_S -> "KeyS"; KeyEvent.KEYCODE_T -> "KeyT"
        KeyEvent.KEYCODE_U -> "KeyU"; KeyEvent.KEYCODE_V -> "KeyV"
        KeyEvent.KEYCODE_W -> "KeyW"; KeyEvent.KEYCODE_X -> "KeyX"
        KeyEvent.KEYCODE_Y -> "KeyY"; KeyEvent.KEYCODE_Z -> "KeyZ"
        KeyEvent.KEYCODE_0 -> "Digit0"; KeyEvent.KEYCODE_1 -> "Digit1"
        KeyEvent.KEYCODE_2 -> "Digit2"; KeyEvent.KEYCODE_3 -> "Digit3"
        KeyEvent.KEYCODE_4 -> "Digit4"; KeyEvent.KEYCODE_5 -> "Digit5"
        KeyEvent.KEYCODE_6 -> "Digit6"; KeyEvent.KEYCODE_7 -> "Digit7"
        KeyEvent.KEYCODE_8 -> "Digit8"; KeyEvent.KEYCODE_9 -> "Digit9"
        KeyEvent.KEYCODE_ENTER -> "Enter"; KeyEvent.KEYCODE_TAB -> "Tab"
        KeyEvent.KEYCODE_SPACE -> "Space"; KeyEvent.KEYCODE_DEL -> "Backspace"
        KeyEvent.KEYCODE_FORWARD_DEL -> "Delete"; KeyEvent.KEYCODE_ESCAPE -> "Escape"
        KeyEvent.KEYCODE_DPAD_UP -> "ArrowUp"; KeyEvent.KEYCODE_DPAD_DOWN -> "ArrowDown"
        KeyEvent.KEYCODE_DPAD_LEFT -> "ArrowLeft"; KeyEvent.KEYCODE_DPAD_RIGHT -> "ArrowRight"
        KeyEvent.KEYCODE_HOME -> "Home"; KeyEvent.KEYCODE_MOVE_END -> "End"
        KeyEvent.KEYCODE_PAGE_UP -> "PageUp"; KeyEvent.KEYCODE_PAGE_DOWN -> "PageDown"
        KeyEvent.KEYCODE_SHIFT_LEFT -> "ShiftLeft"; KeyEvent.KEYCODE_SHIFT_RIGHT -> "ShiftRight"
        KeyEvent.KEYCODE_CTRL_LEFT -> "ControlLeft"; KeyEvent.KEYCODE_CTRL_RIGHT -> "ControlRight"
        KeyEvent.KEYCODE_ALT_LEFT -> "AltLeft"; KeyEvent.KEYCODE_ALT_RIGHT -> "AltRight"
        KeyEvent.KEYCODE_CAPS_LOCK -> "CapsLock"
        KeyEvent.KEYCODE_MINUS -> "Minus"; KeyEvent.KEYCODE_EQUALS -> "Equal"
        KeyEvent.KEYCODE_COMMA -> "Comma"; KeyEvent.KEYCODE_PERIOD -> "Period"
        KeyEvent.KEYCODE_SLASH -> "Slash"; KeyEvent.KEYCODE_BACKSLASH -> "Backslash"
        KeyEvent.KEYCODE_GRAVE -> "Backquote"; KeyEvent.KEYCODE_SEMICOLON -> "Semicolon"
        KeyEvent.KEYCODE_APOSTROPHE -> "Quote"
        KeyEvent.KEYCODE_LEFT_BRACKET -> "BracketLeft"; KeyEvent.KEYCODE_RIGHT_BRACKET -> "BracketRight"
        in KeyEvent.KEYCODE_F1..KeyEvent.KEYCODE_F12 -> "F${keyCode - KeyEvent.KEYCODE_F1 + 1}"
        else -> null
    }

    private fun send(action: String, extra: Map<String, Any>) {
        val json = JSONObject().put("type", "input").put("action", action).put("seq", ++seq)
        extra.forEach { (k, v) -> json.put(k, v) }
        session.send(json)
    }
}
