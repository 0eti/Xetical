"""전역 단축키(RegisterHotKey). '캘린더 숨기기', '예약된 작업 표시/숨기기'에 쓴다."""
import ctypes
import ctypes.wintypes as wt

from PySide6.QtCore import QAbstractNativeEventFilter, Qt
from PySide6.QtGui import QKeySequence

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
user32.UnregisterHotKey.argtypes = [wt.HWND, ctypes.c_int]
WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000


def _vk(key):
    if Qt.Key_A <= key <= Qt.Key_Z or Qt.Key_0 <= key <= Qt.Key_9:
        return int(key)
    if Qt.Key_F1 <= key <= Qt.Key_F24:
        return 0x70 + int(key) - int(Qt.Key_F1)
    return {Qt.Key_Space: 0x20, Qt.Key_Home: 0x24, Qt.Key_End: 0x23, Qt.Key_Insert: 0x2D, Qt.Key_Delete: 0x2E,
            Qt.Key_PageUp: 0x21, Qt.Key_PageDown: 0x22, Qt.Key_Left: 0x25, Qt.Key_Up: 0x26, Qt.Key_Right: 0x27,
            Qt.Key_Down: 0x28}.get(key)


def parse(seq_text):
    seq = QKeySequence(seq_text or "")
    if seq.isEmpty():
        return None
    combo = seq[0]
    key, mods = combo.key(), combo.keyboardModifiers()
    vk = _vk(key)
    if vk is None:
        return None
    m = MOD_NOREPEAT
    if mods & Qt.ControlModifier:
        m |= MOD_CONTROL
    if mods & Qt.AltModifier:
        m |= MOD_ALT
    if mods & Qt.ShiftModifier:
        m |= MOD_SHIFT
    if mods & Qt.MetaModifier:
        m |= MOD_WIN
    return m, vk


class Hotkeys(QAbstractNativeEventFilter):
    def __init__(self):
        super().__init__()
        self.callbacks = {}

    def set(self, hid, seq_text, enabled, callback):
        """등록에 성공하면 True. 다른 프로그램이 이미 쓰는 조합이면 False."""
        user32.UnregisterHotKey(None, hid)
        self.callbacks.pop(hid, None)
        parsed = parse(seq_text) if enabled else None
        if not parsed:
            return not enabled
        if user32.RegisterHotKey(None, hid, *parsed):
            self.callbacks[hid] = callback
            return True
        return False

    def clear(self):
        for hid in list(self.callbacks):
            user32.UnregisterHotKey(None, hid)
        self.callbacks.clear()

    def nativeEventFilter(self, event_type, message):
        et = event_type.data() if hasattr(event_type, "data") else event_type
        if et == b"windows_generic_MSG":
            msg = wt.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.wParam in self.callbacks:
                self.callbacks[msg.wParam]()
                return True, 0
        return False, 0
