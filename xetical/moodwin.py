"""따로 뗀 트래커 창(설정 → 트래커 → 위치 '따로 떼어 바탕화면에').

달력처럼 바탕화면에 붙는다. 칸은 달력에 붙었을 때처럼 더블클릭(또는 오른쪽 클릭)해야 색을 고르고, 이름을 누르면
트래커가 바뀐다. 배경은 달력의 트래커처럼 투명하다.
옮기기·크기 바꾸기는 달력과 같이 '위치 및 크기'(조정 모드)에서만 된다: 아무 데나 끌면 옮기고, 오른쪽 아래 모서리를
끌면 크기가 바뀐다(높이는 너비에 맞춰 칸이 정사각형이 되게). 자리는 달력의 '✓ 완료'를 누를 때 저장한다.
"""
import ctypes.wintypes as wt
from datetime import date

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from . import desktop
from .mood import MoodPanel
from .view import C_WARN, DRAG_START, mood_font, ui_scale

GRIP = 14       # 오른쪽 아래 크기 조절 모서리(px)
MIN_W = 140
MAX_BOX = 40    # 따로 뗀 창은 칸을 달력에 붙었을 때(15)보다 크게 키울 수 있다


class MoodWindow(QWidget):
    trackerChosen = Signal(str)           # 트래커 이름을 누름(트래커 id)
    menuRequested = Signal(object, QPoint)  # 칸을 더블클릭·오른쪽 클릭(date, 화면 좌표)

    def __init__(self, cfg, data):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.setWindowTitle("Xetical 트래커")
        self.cfg, self.data = cfg, data
        self.panel = MoodPanel()
        self.adjusting = False
        self._drag = None   # {"kind": "move"|"size", "start": 누른 화면 좌표, "geo": 누를 때 창, "active": bool}
        self._owner = None
        self._allow_z = False

    def size_for(self, width):
        """너비에 맞는 창 크기: 칸이 정사각형이 되게 높이를 정한다."""
        width = max(MIN_W, width)
        h = self.panel.height(self.cfg, width, ui_scale(self.cfg), mood_font(self.cfg), date.today().year, MAX_BOX)
        return QSize(width, h)

    def relayout(self):
        """설정(글꼴·트래커·모양)이 바뀌면 높이를 다시 맞춘다."""
        size = self.size_for(self.width())
        if size != self.size():
            self.resize(size)
        self.update()

    def set_adjusting(self, on):
        self.adjusting = on
        self._drag = None
        self.setCursor(Qt.ArrowCursor)
        self.update()

    # ---------- 바탕화면 부착 ----------
    def show_on_desktop(self):
        hwnd = int(self.winId())
        self._owner = desktop.attach(hwnd)
        self.show()
        desktop.attach(hwnd)
        self._allow_z = True
        desktop.send_to_bottom(hwnd)
        self._allow_z = False

    def ensure_attached(self):
        """탐색기가 재시작되면 소유자가 사라지므로 다시 붙인다."""
        if self.isVisible() and not desktop.is_attached(int(self.winId()), self._owner):
            self.show_on_desktop()

    def nativeEvent(self, event_type, message):
        et = event_type.data() if hasattr(event_type, "data") else event_type
        if et == b"windows_generic_MSG":
            msg = wt.MSG.from_address(int(message))
            if msg.message == desktop.WM_WINDOWPOSCHANGING and not self._allow_z:
                desktop.block_zorder(msg.lParam)
            elif msg.message == desktop.WM_MOUSEACTIVATE:
                return True, desktop.MA_NOACTIVATE
        return super().nativeEvent(event_type, message)

    # ---------- 그리기 ----------
    def paintEvent(self, _):
        p = QPainter(self)
        try:
            p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
            s = ui_scale(self.cfg)
            rect = QRectF(self.rect())
            # 완전히 투명한 픽셀은 클릭이 바탕화면으로 빠지므로 1/255만 칠한다(달력의 트래커와 같음)
            self.panel.paint(p, rect, self.cfg, self.data, date.today(), mood_font(self.cfg), QColor(0, 0, 0, 1), s,
                             max_box=MAX_BOX)
            if self.adjusting:  # 달력의 조정 모드 테두리와 같은 색 + 크기 조절 모서리
                p.setPen(QPen(QColor(C_WARN), 2, Qt.DashLine))
                p.setBrush(Qt.NoBrush)
                p.drawRect(rect.adjusted(1, 1, -1, -1))
                p.setPen(QPen(QColor(C_WARN), 1.5))
                for i in (5, 9, 13):
                    p.drawLine(QPointF(rect.right() - i, rect.bottom() - 2), QPointF(rect.right() - 2, rect.bottom() - i))
        finally:
            p.end()

    # ---------- 마우스 ----------
    def _in_grip(self, pos):
        return pos.x() >= self.width() - GRIP and pos.y() >= self.height() - GRIP

    def mousePressEvent(self, e):
        pos = e.position()
        if self.adjusting:
            if e.button() == Qt.LeftButton:
                self._drag = {"kind": "size" if self._in_grip(pos) else "move", "start": e.globalPosition().toPoint(),
                              "geo": self.geometry(), "active": False}
            return
        hit = self.panel.hit(pos)
        if not hit:
            return
        if e.button() == Qt.RightButton and hit[0] == "box":
            self.menuRequested.emit(hit[1], e.globalPosition().toPoint())
        elif e.button() == Qt.LeftButton and hit[0] == "tab":
            self.trackerChosen.emit(hit[1])

    def mouseMoveEvent(self, e):
        pos = e.position()
        if self._drag is None:
            hit = None if self.adjusting else self.panel.hit(pos)
            self.setCursor(Qt.SizeFDiagCursor if self.adjusting and self._in_grip(pos) else
                           Qt.SizeAllCursor if self.adjusting else
                           Qt.PointingHandCursor if hit and hit[0] == "tab" else Qt.ArrowCursor)
            return
        delta = e.globalPosition().toPoint() - self._drag["start"]
        if not self._drag["active"]:
            if delta.manhattanLength() <= DRAG_START:
                return
            self._drag["active"] = True
        geo = self._drag["geo"]
        if self._drag["kind"] == "move":
            self.move(geo.topLeft() + delta)
        else:
            self.resize(self.size_for(geo.width() + delta.x()))

    def mouseReleaseEvent(self, _e):
        self._drag = None

    def mouseDoubleClickEvent(self, e):
        if self.adjusting:
            return
        hit = self.panel.hit(e.position())
        if hit and hit[0] == "box" and e.button() == Qt.LeftButton:
            self.menuRequested.emit(hit[1], e.globalPosition().toPoint())
