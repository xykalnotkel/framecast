"""Injeksi input ke OS host (dipakai server/streamer).

Aktif di Windows via SendInput (ctypes — tanpa library tambahan).
Linux & macOS masih stub; implementasi penuh (uinput/XTEST, CGEvent)
dijelaskan di docs/ARCHITECTURE.md.

Format event dari client (text frame JSON lewat WebSocket):
  {"type":"input", "action":"mouse_move",   "x":0.42, "y":0.33}          # koordinat normalisasi 0..1
  {"type":"input", "action":"mouse_button", "button":"left", "down":true}
  {"type":"input", "action":"mouse_wheel",  "delta":1}
  {"type":"input", "action":"key",          "scancode":97, "down":true, "unicode":"a"}
"""

import sys

WHEEL_DELTA = 120

# --- map scancode pygame -> Virtual-Key Windows ----------------------------
# pygame.K_a = 97 = ASCII 'a' = VK_A, dst. Angka & huruf tinggal pakai ASCII.
_PYGAME_TO_VK = {
    8: 0x08,          # backspace
    9: 0x09,          # tab
    13: 0x0D,         # enter
    27: 0x1B,         # escape
    32: 0x20,         # spasi
    127: 0x2E,        # delete
    1073741903: 0x27, # panah kanan
    1073741904: 0x25, # panah kiri
    1073741905: 0x28, # panah bawah
    1073741906: 0x26, # panah atas
}
_PYGAME_TO_VK.update({c: c for c in range(ord("a"), ord("z") + 1)})
_PYGAME_TO_VK.update({c: c for c in range(ord("0"), ord("9") + 1)})

# --- map KeyboardEvent.code (browser/Android) -> Virtual-Key Windows ---------
WEB_CODE_TO_VK = {f"Key{chr(c)}": c for c in range(ord("A"), ord("Z") + 1)}
WEB_CODE_TO_VK.update({f"Digit{i}": ord("0") + i for i in range(10)})
WEB_CODE_TO_VK.update({f"F{i}": 0x6F + i for i in range(1, 13)})
WEB_CODE_TO_VK.update({
    "ArrowUp": 0x26, "ArrowDown": 0x28, "ArrowLeft": 0x25, "ArrowRight": 0x27,
    "Enter": 0x0D, "Tab": 0x09, "Space": 0x20, "Backspace": 0x08,
    "Delete": 0x2E, "Insert": 0x2D, "Escape": 0x1B, "Home": 0x24, "End": 0x23,
    "PageUp": 0x21, "PageDown": 0x22, "CapsLock": 0x14, "NumLock": 0x90,
    "ShiftLeft": 0xA0, "ShiftRight": 0xA1,
    "ControlLeft": 0xA2, "ControlRight": 0xA3,
    "AltLeft": 0xA4, "AltRight": 0xA5,
    "MetaLeft": 0x5B, "MetaRight": 0x5C,
    "Minus": 0xBD, "Equal": 0xBB, "BracketLeft": 0xDB, "BracketRight": 0xDD,
    "Backslash": 0xDC, "Semicolon": 0xBA, "Quote": 0xDE, "Comma": 0xBC,
    "Period": 0xBE, "Slash": 0xBF, "Backquote": 0xC0,
})
for i in range(10):
    WEB_CODE_TO_VK[f"Numpad{i}"] = 0x60 + i


_WEB_ACTIONS = {
    "mousemove": "mouse_move",
    "wheel": "mouse_wheel",
    "mousedown": "mouse_button",
    "mouseup": "mouse_button",
    "keydown": "key",
    "keyup": "key",
}


def inject(event):
    """Suntikkan 1 event input ke sistem host. Return True kalau berhasil.

    Menerima dua gaya action:
      - gaya lokal (pygame): mouse_move / mouse_button / mouse_wheel / key
      - gaya web/Android:   mousemove / mousedown / mouseup / wheel / keydown / keyup
    """
    if event.get("type") != "input":
        return False
    action = event.get("action")
    if action in _WEB_ACTIONS:
        event = dict(event)
        event["action"] = _WEB_ACTIONS[action]
        if action == "wheel":
            event["delta"] = int(event.get("dy", 0)) * WHEEL_DELTA
        elif action in ("mousedown", "mouseup"):
            event["down"] = action == "mousedown"
        elif action in ("keydown", "keyup"):
            event["down"] = action == "keydown"
    if sys.platform == "win32":
        return _win32_inject(event)
    print(f"[input] (stub {sys.platform}) action={event.get('action')} "
          f"— implementasi penuh lihat docs/ARCHITECTURE.md")
    return False


# --- SendInput (Windows) ---------------------------------------------------
if sys.platform == "win32":
    import ctypes

    INPUT_MOUSE = 0
    INPUT_KEYBOARD = 1
    MOUSEEVENTF_ABSOLUTE = 0x8000
    MOUSEEVENTF_MOVE = 0x0001
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    MOUSEEVENTF_RIGHTDOWN = 0x0008
    MOUSEEVENTF_RIGHTUP = 0x0010
    MOUSEEVENTF_MIDDLEDOWN = 0x0020
    MOUSEEVENTF_MIDDLEUP = 0x0040
    MOUSEEVENTF_WHEEL = 0x0800
    KEYEVENTF_KEYUP = 0x0002

    class _MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", ctypes.c_long),
            ("dy", ctypes.c_long),
            ("mouseData", ctypes.c_ulong),
            ("dwFlags", ctypes.c_ulong),
            ("time", ctypes.c_ulong),
            ("dwExtraInfo", ctypes.c_ulong),
        ]

    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", ctypes.c_ushort),
            ("wScan", ctypes.c_ushort),
            ("dwFlags", ctypes.c_ulong),
            ("time", ctypes.c_ulong),
            ("dwExtraInfo", ctypes.c_ulong),
        ]

    class _INPUT_UNION(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]

    class _INPUT(ctypes.Structure):
        _fields_ = [("type", ctypes.c_ulong), ("u", _INPUT_UNION)]

    def _send(*inputs):
        arr = (_INPUT * len(inputs))(*inputs)
        sent = ctypes.windll.user32.SendInput(len(inputs), arr, ctypes.sizeof(_INPUT))
        return sent == len(inputs)

    def _mouse_input(flags, dx=0, dy=0, data=0):
        inp = _INPUT()
        inp.type = INPUT_MOUSE
        inp.u.mi.dx = dx
        inp.u.mi.dy = dy
        inp.u.mi.mouseData = data
        inp.u.mi.dwFlags = flags
        return inp

    def _key_input(vk, keyup):
        inp = _INPUT()
        inp.type = INPUT_KEYBOARD
        inp.u.ki.wVk = vk
        inp.u.ki.dwFlags = KEYEVENTF_KEYUP if keyup else 0
        return inp

    def _win32_inject(event):
        action = event.get("action")
        u = ctypes.windll.user32

        if action == "mouse_move":
            # koordinat absolut SendInput: 0..65535 untuk seluruh layar utama
            w, h = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
            px = int(event.get("x", 0.5) * w)
            py = int(event.get("y", 0.5) * h)
            dx = int(px * 65535 // max(1, w - 1))
            dy = int(py * 65535 // max(1, h - 1))
            return _send(_mouse_input(MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_MOVE, dx, dy))

        if action == "mouse_button":
            flags = {
                ("left", True): MOUSEEVENTF_LEFTDOWN,
                ("left", False): MOUSEEVENTF_LEFTUP,
                ("right", True): MOUSEEVENTF_RIGHTDOWN,
                ("right", False): MOUSEEVENTF_RIGHTUP,
                ("middle", True): MOUSEEVENTF_MIDDLEDOWN,
                ("middle", False): MOUSEEVENTF_MIDDLEUP,
            }.get((event.get("button"), event.get("down")))
            if flags is None:
                return False
            return _send(_mouse_input(flags))

        if action == "mouse_wheel":
            delta = int(event.get("delta", 1)) * WHEEL_DELTA
            return _send(_mouse_input(MOUSEEVENTF_WHEEL, data=delta))

        if action == "key":
            vk = _PYGAME_TO_VK.get(event.get("scancode")) or WEB_CODE_TO_VK.get(event.get("code"))
            if vk is None:
                return False
            return _send(_key_input(vk, not event.get("down", True)))

        return False
