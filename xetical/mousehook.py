"""'바탕화면 아이콘 클릭 통과' 모드(1-5).

달력 창을 마우스가 통과하는 창(WS_EX_TRANSPARENT)으로 만들고, 전역 마우스 훅(WH_MOUSE_LL)으로
달력 위에서 일어난 클릭을 지켜본다.
- 그 자리에 바탕화면 아이콘이 있으면: 아무것도 하지 않는다 → 탐색기가 아이콘 클릭으로 처리
- 빈 곳이면: 그 클릭을 탐색기에 보내지 않고(삼키고) 달력에 전해 준다
아이콘이 있는지는 탐색기의 아이콘 목록 창에 LVM_HITTEST로 묻는다(탐색기에 코드를 넣지 않는다).
"""
import ctypes
import ctypes.wintypes as wt
import logging

from PySide6.QtCore import QObject, QPointF, Qt, QTimer

from . import desktop

log = logging.getLogger("xetical")
u, k = ctypes.windll.user32, ctypes.windll.kernel32

WH_MOUSE_LL = 14
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_RBUTTONDOWN, WM_RBUTTONUP, WM_MOUSEWHEEL = (
    0x200, 0x201, 0x202, 0x204, 0x205, 0x20A)
LVM_HITTEST = 0x1012
PROCESS_VM = 0x0008 | 0x0010 | 0x0020  # VM_OPERATION | VM_READ | VM_WRITE

LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)
u.SetWindowsHookExW.restype = wt.HHOOK
u.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
u.CallNextHookEx.restype = LRESULT
u.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
u.UnhookWindowsHookEx.argtypes = [wt.HHOOK]
u.WindowFromPoint.restype = wt.HWND
u.WindowFromPoint.argtypes = [wt.POINT]
u.GetAncestor.restype = wt.HWND
u.GetAncestor.argtypes = [wt.HWND, wt.UINT]
u.ScreenToClient.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
u.SendMessageTimeoutW.restype = ctypes.c_ssize_t
u.SendMessageTimeoutW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM, wt.UINT, wt.UINT,
                                  ctypes.POINTER(ctypes.c_size_t)]
k.VirtualAllocEx.restype = ctypes.c_void_p
k.VirtualAllocEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wt.DWORD, wt.DWORD]
k.VirtualFreeEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wt.DWORD]
k.WriteProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p]
k.OpenProcess.restype = wt.HANDLE
k.GetModuleHandleW.restype = wt.HMODULE

DESKTOP_CLASSES = {"SysListView32", "SHELLDLL_DefView", "Progman", "WorkerW"}


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wt.POINT), ("mouseData", wt.DWORD), ("flags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class LVHITTESTINFO(ctypes.Structure):
    _fields_ = [("pt", wt.POINT), ("flags", wt.UINT), ("iItem", ctypes.c_int), ("iSubItem", ctypes.c_int),
                ("iGroup", ctypes.c_int)]


def _class(h):
    b = ctypes.create_unicode_buffer(64)
    u.GetClassNameW(h, b, 64)
    return b.value


class IconProbe:
    """탐색기 아이콘 목록 창에 '이 자리에 아이콘이 있나?'를 묻는다."""

    def __init__(self):
        self.lv = self.proc = self.mem = None

    def _open(self):
        lv = desktop.find_desktop_window()
        if lv == self.lv and self.mem:
            return True
        self.close()
        if not lv or _class(lv) != "SysListView32":
            return False
        pid = wt.DWORD()
        u.GetWindowThreadProcessId(wt.HWND(lv), ctypes.byref(pid))
        proc = k.OpenProcess(PROCESS_VM, False, pid.value)
        if not proc:
            return False
        mem = k.VirtualAllocEx(proc, None, ctypes.sizeof(LVHITTESTINFO), 0x3000, 0x04)
        if not mem:
            k.CloseHandle(proc)
            return False
        self.lv, self.proc, self.mem = lv, proc, mem
        return True

    def icon_at(self, x, y):
        """화면 좌표(물리 픽셀)에 아이콘이 있으면 True. 확인할 수 없으면 True(안전하게 탐색기에 맡김)."""
        try:
            if not self._open():
                return True
            pt = wt.POINT(x, y)
            u.ScreenToClient(wt.HWND(self.lv), ctypes.byref(pt))
            info = LVHITTESTINFO(pt, 0, -1, 0, 0)
            k.WriteProcessMemory(self.proc, self.mem, ctypes.byref(info), ctypes.sizeof(info), None)
            res = ctypes.c_size_t()
            ok = u.SendMessageTimeoutW(wt.HWND(self.lv), LVM_HITTEST, 0, self.mem, 0x0002, 80, ctypes.byref(res))
            if not ok:
                return True
            return ctypes.c_int(res.value & 0xFFFFFFFF).value >= 0
        except OSError:
            return True

    def close(self):
        if self.mem and self.proc:
            k.VirtualFreeEx(self.proc, self.mem, 0, 0x8000)
        if self.proc:
            k.CloseHandle(self.proc)
        self.lv = self.proc = self.mem = None


class ClickThrough(QObject):
    """달력 창(win)을 위한 마우스 훅. start()/stop()으로 켜고 끈다."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.hook = None
        self.probe = IconProbe()
        self._proc = HOOKPROC(self._callback)  # GC되지 않게 보관
        self._swallowing = set()                # 삼킨 버튼(눌림을 삼켰으면 뗌도 삼킨다)
        self._last_down = (0, 0, 0)             # 더블클릭 판정: (시각, x, y)

    def start(self):
        if not self.hook:
            self.hook = u.SetWindowsHookExW(WH_MOUSE_LL, self._proc, k.GetModuleHandleW(None), 0)
            if not self.hook:
                log.warning("마우스 훅 설치 실패: %s", ctypes.get_last_error())

    def stop(self):
        if self.hook:
            u.UnhookWindowsHookEx(self.hook)
            self.hook = None
        self.probe.close()

    # 화면 물리 좌표 → 달력 창 안 논리 좌표
    def _local(self, x, y):
        r = wt.RECT()
        u.GetWindowRect(wt.HWND(int(self.win.winId())), ctypes.byref(r))
        if not (r.left <= x < r.right and r.top <= y < r.bottom):
            return None
        ratio = self.win.devicePixelRatioF() or 1.0
        return QPointF((x - r.left) / ratio, (y - r.top) / ratio)

    def _over_desktop(self, x, y):
        h = u.WindowFromPoint(wt.POINT(x, y))  # 통과 창인 달력은 건너뛰고 그 아래 창을 준다
        return bool(h) and _class(h) in DESKTOP_CLASSES

    def _callback(self, code, wparam, lparam):
        try:
            if code >= 0 and self.win.isVisible() and not self.win.adjusting:
                info = MSLLHOOKSTRUCT.from_address(lparam)
                if self._handle(wparam, info):
                    return 1  # 탐색기에 보내지 않음
        except Exception:  # noqa: BLE001 - 훅에서 예외가 나면 마우스가 멈출 수 있어 절대 밖으로 내보내지 않는다
            log.exception("마우스 훅 처리 실패")
        return u.CallNextHookEx(self.hook, code, wparam, lparam)

    def _handle(self, msg, info):
        x, y = info.pt.x, info.pt.y
        if msg in (WM_LBUTTONUP, WM_RBUTTONUP):
            btn = Qt.LeftButton if msg == WM_LBUTTONUP else Qt.RightButton
            if btn in self._swallowing:
                self._swallowing.discard(btn)
                pos = self._local(x, y)
                QTimer.singleShot(0, lambda: self.win.on_release(pos or QPointF(-1, -1), btn))
                return True
            return False
        pos = self._local(x, y)
        if pos is None or not self._over_desktop(x, y):
            return False
        gpos = self.win.mapToGlobal(pos.toPoint())
        if msg == WM_MOUSEMOVE:
            left = Qt.LeftButton in self._swallowing
            QTimer.singleShot(0, lambda: self.win.on_move(pos, gpos, left))
            return False  # 움직임은 탐색기에도 그대로
        if msg == WM_MOUSEWHEEL:
            delta = ctypes.c_short(info.mouseData >> 16).value
            QTimer.singleShot(0, lambda: self.win.on_wheel(pos, delta))
            return False
        if msg in (WM_LBUTTONDOWN, WM_RBUTTONDOWN):
            if self.probe.icon_at(x, y):
                return False  # 아이콘 클릭 → 탐색기
            btn = Qt.LeftButton if msg == WM_LBUTTONDOWN else Qt.RightButton
            self._swallowing.add(btn)
            double = False
            if btn == Qt.LeftButton:
                t0, x0, y0 = self._last_down
                cx, cy = u.GetSystemMetrics(36), u.GetSystemMetrics(37)  # SM_CXDOUBLECLK, SM_CYDOUBLECLK
                double = info.time - t0 <= u.GetDoubleClickTime() and abs(x - x0) <= cx and abs(y - y0) <= cy
                self._last_down = (0, 0, 0) if double else (info.time, x, y)
            if double:
                QTimer.singleShot(0, lambda: self.win.on_double(pos, btn))
            else:
                QTimer.singleShot(0, lambda: self.win.on_press(pos, gpos, btn))
            return True
        return False
