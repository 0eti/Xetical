"""바탕화면 카운트다운 카드(DesktopCal 카운트다운 카드). 달력처럼 바탕화면에 붙고, 끌어서 옮길 수 있다."""
import ctypes.wintypes as wt

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from . import desktop
from .model import COUNTDOWN_TYPES
from .view import make_font, shade


class CountdownCard(QWidget):
    moved = Signal(str, QPoint)
    openRequested = Signal(object)

    def __init__(self, cfg, ev):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.cfg, self.ev = cfg, ev
        self._drag = None
        self._allow_z = False
        self.apply_size()

    def scale(self):
        return {"small": 0.8, "large": 1.3}.get(self.cfg.get("card_size"), 1.0)

    def apply_size(self):
        k = self.scale()
        self.resize(round(170 * k), round(110 * k))

    def key(self):
        return self.ev.recurring_event_id or self.ev.id

    def show_on_desktop(self):
        hwnd = int(self.winId())
        desktop.attach(hwnd)
        self.show()
        desktop.attach(hwnd)
        self._allow_z = True
        desktop.send_to_bottom(hwnd)
        self._allow_z = False

    def nativeEvent(self, event_type, message):
        et = event_type.data() if hasattr(event_type, "data") else event_type
        if et == b"windows_generic_MSG":
            msg = wt.MSG.from_address(int(message))
            if msg.message == desktop.WM_WINDOWPOSCHANGING and not self._allow_z:
                desktop.block_zorder(msg.lParam)
            elif msg.message == desktop.WM_MOUSEACTIVATE:
                return True, desktop.MA_NOACTIVATE
        return super().nativeEvent(event_type, message)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        alpha = max(0.0, 1 - self.cfg["card_transparency"] / 100)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QColor(255, 255, 255, 40))
        p.setBrush(shade(self.cfg["cell_bg"], 0.6, alpha))
        p.drawRoundedRect(r, 8, 8)
        kind = self.ev.countdown_parts()[0]
        num, unit = self.ev.countdown_label()
        k = self.scale()
        p.setFont(make_font(self.cfg["item_font"], 0.75 * k, bold=False))
        p.setPen(QColor(self.cfg["lunar_text"]))
        p.drawText(r.adjusted(10, 6, -10, 0), Qt.AlignLeft | Qt.AlignTop, COUNTDOWN_TYPES.get(kind, "카운트다운"))
        p.drawText(r.adjusted(10, 6, -10, 0), Qt.AlignRight | Qt.AlignTop, f"{self.ev.first_day:%Y.%m.%d}")
        big = QFont(self.cfg["date_font"]["family"])
        big.setPointSizeF(26 * k)
        big.setBold(True)
        p.setFont(big)
        p.setPen(QColor(self.cfg["date_font"]["color"]))
        p.drawText(r.adjusted(10, 18 * k, -10, -28 * k), Qt.AlignCenter, num)
        p.setFont(make_font(self.cfg["item_font"], 0.8 * k))
        p.setPen(QColor(self.ev.text_color or self.cfg["item_font"]["color"]))
        p.drawText(r.adjusted(10, 0, -10, -8), Qt.AlignHCenter | Qt.AlignBottom,
                   self.fontMetrics().elidedText(f"{self.ev.title} {unit}".strip(), Qt.ElideRight, int(r.width() - 20)))
        p.end()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            self._drag = None
            self.moved.emit(self.key(), self.pos())

    def mouseDoubleClickEvent(self, e):
        self._drag = None
        self.openRequested.emit(self.ev)


def default_position(index, calendar_geometry):
    """달력 왼쪽에 위에서부터 쌓는다."""
    g = calendar_geometry
    x = g.left() - 180
    if x < 0:
        a = QApplication.primaryScreen().availableGeometry()
        x = a.left() + 10
    return QPoint(x, g.top() + index * 120)
