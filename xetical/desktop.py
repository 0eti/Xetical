"""바탕화면에 창을 '박는' Win32 처리.

DesktopCal과 같은 방식: 우리 창의 소유자(owner)를 바탕화면 아이콘 목록 창(FolderView)으로 지정한다.
소유 창은 항상 소유자 바로 위에 붙어 다니므로 바탕화면과 한 몸처럼 동작하고,
'바탕화면 보기(Win+D)'를 해도 최소화되지 않는다. 여기에 WS_EX_NOACTIVATE를 걸어
클릭해도 앞으로 튀어나오지 않게 하고, WM_WINDOWPOSCHANGING에서 Z순서 변경을 막는다.
"""
import ctypes
import ctypes.wintypes as wt

user32 = ctypes.WinDLL("user32", use_last_error=True)

GWL_EXSTYLE = -20
GWLP_HWNDPARENT = -8
GW_OWNER = 4
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
WS_EX_NOACTIVATE = 0x08000000
HWND_BOTTOM = wt.HWND(1)
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE = 0x1, 0x2, 0x4, 0x10

WM_WINDOWPOSCHANGING = 0x0046
WM_MOUSEACTIVATE = 0x0021
MA_NOACTIVATE = 3

user32.FindWindowW.restype = wt.HWND
user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
user32.FindWindowExW.restype = wt.HWND
user32.FindWindowExW.argtypes = [wt.HWND, wt.HWND, wt.LPCWSTR, wt.LPCWSTR]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.GetWindow.restype = wt.HWND
user32.GetWindow.argtypes = [wt.HWND, wt.UINT]
user32.IsWindow.argtypes = [wt.HWND]
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]


class WINDOWPOS(ctypes.Structure):
    _fields_ = [("hwnd", wt.HWND), ("hwndInsertAfter", wt.HWND), ("x", ctypes.c_int), ("y", ctypes.c_int),
                ("cx", ctypes.c_int), ("cy", ctypes.c_int), ("flags", wt.UINT)]


def _defview_listview(parent):
    defview = user32.FindWindowExW(parent, None, "SHELLDLL_DefView", None)
    if not defview:
        return None
    return user32.FindWindowExW(defview, None, "SysListView32", None) or defview


def find_desktop_window():
    """아이콘 목록 창(FolderView)을 찾는다. 윈도우 버전에 따라 Progman 또는 WorkerW 아래에 있다."""
    progman = user32.FindWindowW("Progman", None)
    if progman:
        lv = _defview_listview(progman)
        if lv:
            return lv
    h = None
    while True:
        h = user32.FindWindowExW(None, h, "WorkerW", None)
        if not h:
            break
        lv = _defview_listview(h)
        if lv:
            return lv
    return progman


def attach(hwnd):
    """창을 바탕화면 소유 창으로 만든다. 성공하면 소유자 핸들을 돌려준다."""
    owner = find_desktop_window()
    if not owner:
        return None
    ex = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    ex = (ex | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE) & ~WS_EX_APPWINDOW
    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex)
    user32.SetWindowLongPtrW(hwnd, GWLP_HWNDPARENT, owner)
    return owner


def send_to_bottom(hwnd):
    user32.SetWindowPos(hwnd, HWND_BOTTOM, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)


def is_attached(hwnd, owner):
    return bool(owner) and user32.IsWindow(owner) and user32.GetWindow(hwnd, GW_OWNER) == owner


class _AccentPolicy(ctypes.Structure):
    _fields_ = [("AccentState", ctypes.c_int), ("AccentFlags", ctypes.c_int),
                ("GradientColor", ctypes.c_uint), ("AnimationId", ctypes.c_int)]


class _CompositionData(ctypes.Structure):
    _fields_ = [("Attribute", ctypes.c_int), ("Data", ctypes.c_void_p), ("SizeOfData", ctypes.c_size_t)]


def set_blur(hwnd, on):
    """유리 효과: 창 뒤 바탕화면을 흐리게 비친다(Windows 10 이상, 비공개 API라 실패해도 무시)."""
    try:
        fn = user32.SetWindowCompositionAttribute
    except AttributeError:
        return False
    accent = _AccentPolicy(3 if on else 0, 0, 0, 0)  # 3 = ACCENT_ENABLE_BLURBEHIND
    data = _CompositionData(19, ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p), ctypes.sizeof(accent))
    return bool(fn(wt.HWND(hwnd), ctypes.byref(data)))


WS_EX_TRANSPARENT = 0x00000020


def set_click_through(hwnd, on):
    """마우스가 창을 통과해 아래 바탕화면(아이콘)으로 가게 한다. 달력 쪽 클릭은 마우스 훅이 대신 전해 준다."""
    ex = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    ex = ex | WS_EX_TRANSPARENT if on else ex & ~WS_EX_TRANSPARENT
    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex)


def force_foreground(hwnd):
    """클릭을 다른 프로세스(탐색기)가 받았을 때 키보드 입력 권한을 빌려 와 우리 창을 활성화한다."""
    fg = user32.GetForegroundWindow()
    kernel32 = ctypes.windll.kernel32
    me = kernel32.GetCurrentThreadId()
    other = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    if other and other != me:
        user32.AttachThreadInput(me, other, True)
    try:
        user32.SetForegroundWindow(wt.HWND(hwnd))
    finally:
        if other and other != me:
            user32.AttachThreadInput(me, other, False)


def block_zorder(lparam):
    """WM_WINDOWPOSCHANGING의 WINDOWPOS에 SWP_NOZORDER를 붙여 Z순서 변경을 무시한다."""
    WINDOWPOS.from_address(lparam).flags |= SWP_NOZORDER
