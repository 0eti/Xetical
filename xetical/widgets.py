"""여러 창에서 같이 쓰는 작은 부품: DesktopCal식 22색 팔레트 메뉴, 켜짐/꺼짐 토글, 효과음."""
import winsound
from datetime import date

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QColorDialog, QGridLayout, QMenu, QPushButton, QToolButton, QWidget,
                               QWidgetAction)

PALETTE = [
    "#ffffff", "#c0c0c0", "#808080", "#404040", "#000000", "#ffff00", "#ffc000", "#ff8000",
    "#ff0000", "#c00000", "#ff66cc", "#cc00ff", "#7030a0", "#0070c0", "#00b0f0", "#00ffff",
    "#00b050", "#92d050", "#00ff00", "#996633", "#ffe699", "#c6e0b4",
]

SOUNDS = {"none": "없음", "default": "기본 알림음", "asterisk": "별표", "exclamation": "느낌표", "question": "물음표"}
_SOUND_ALIAS = {"default": "SystemNotification", "asterisk": "SystemAsterisk",
                "exclamation": "SystemExclamation", "question": "SystemQuestion"}


UI_THEME = {"mode": "auto"}  # 설정 '설정 창 테마'(3-6): auto | dark | light


def apply_ui_theme(mode):
    """설정 창·대화상자 테마를 정한다. Qt 6.8 이상이면 앱 전체 창(대화상자 포함)이 따라간다."""
    UI_THEME["mode"] = mode
    hints = QGuiApplication.styleHints()
    if hasattr(hints, "setColorScheme"):
        hints.setColorScheme({"dark": Qt.ColorScheme.Dark, "light": Qt.ColorScheme.Light}.get(
            mode, Qt.ColorScheme.Unknown))


def is_dark():
    """다크 모드인지. 설정에서 직접 골랐으면 그것을, 아니면 Windows 설정을 따른다."""
    if UI_THEME["mode"] in ("dark", "light"):
        return UI_THEME["mode"] == "dark"
    hints = QGuiApplication.styleHints()
    if hasattr(hints, "colorScheme"):
        return hints.colorScheme() == Qt.ColorScheme.Dark
    return QGuiApplication.palette().window().color().lightness() < 128


def app_icon(day=None, show_number=True):
    """달력 모양 앱 아이콘(창 제목줄·작업 표시줄·트레이에 쓴다). show_number=False면 숫자 대신 칸 무늬."""
    icon = QIcon()
    for size in (16, 20, 24, 32, 48, 64, 128, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        k = size / 64
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#3a3a3a"))
        p.drawRoundedRect(QRectF(4 * k, 6 * k, 56 * k, 54 * k), 8 * k, 8 * k)
        p.setBrush(QColor("#e8b100"))
        p.drawRoundedRect(QRectF(4 * k, 6 * k, 56 * k, 16 * k), 8 * k, 8 * k)
        p.drawRect(QRectF(4 * k, 14 * k, 56 * k, 8 * k))
        p.setBrush(QColor("#ffffff"))
        for x in (18, 46):  # 달력 고리
            p.drawRoundedRect(QRectF((x - 3) * k, 2 * k, 6 * k, 10 * k), 2 * k, 2 * k)
        if not show_number:
            p.setBrush(QColor("#d0d0d0"))
            for r in range(3):
                for c in range(4):
                    p.drawRect(QRectF((10 + c * 12) * k, (27 + r * 10) * k, 8 * k, 6 * k))
            p.end()
            icon.addPixmap(pm)
            continue
        p.setPen(QColor("white"))
        f = QFont("Segoe UI")
        f.setPixelSize(max(6, round(28 * k)))
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(4 * k, 22 * k, 56 * k, 38 * k), Qt.AlignCenter, str(day or date.today().day))
        p.end()
        icon.addPixmap(pm)
    return icon


def confirm(parent, text, yes="삭제", no="취소"):
    """예/아니요를 한국어 단추로 묻는다(기본 QMessageBox.question은 Yes/No 영어)."""
    from PySide6.QtWidgets import QMessageBox
    box = QMessageBox(QMessageBox.Question, "Xetical", text, parent=parent)
    box.setWindowFlag(Qt.WindowStaysOnTopHint)
    ok = box.addButton(yes, QMessageBox.AcceptRole)
    box.addButton(no, QMessageBox.RejectRole)
    box.setDefaultButton(ok)
    box.exec()
    return box.clickedButton() is ok


def play_sound(name):
    alias = _SOUND_ALIAS.get(name)
    if alias:
        try:
            winsound.PlaySound(alias, winsound.SND_ALIAS | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        except RuntimeError:
            winsound.MessageBeep()


def swatch_icon(hex_color, size=14):
    pm = QPixmap(size, size)
    if hex_color:
        pm.fill(QColor(hex_color))
    else:
        pm.fill(Qt.transparent)
    return QIcon(pm)


def color_menu(on_pick, recent=(), none_label="없음", parent=None):
    """색 고르기 메뉴: 최근 색 + 22색 격자 + 없음 + 사용자 지정."""
    menu = QMenu(parent)

    def grid_action(colors):
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(6, 4, 6, 4)
        g.setSpacing(3)
        for i, c in enumerate(colors):
            b = QToolButton()
            b.setFixedSize(QSize(20, 20))
            b.setStyleSheet(f"QToolButton{{background:{c};border:1px solid #666;}}"
                            "QToolButton:hover{border:2px solid #fff;}")
            b.setToolTip(c)
            b.clicked.connect(lambda _=False, c=c: (menu.close(), on_pick(c)))
            g.addWidget(b, i // 8, i % 8)
        act = QWidgetAction(menu)
        act.setDefaultWidget(w)
        return act

    if recent:
        menu.addSection("최근")
        menu.addAction(grid_action(list(recent)[:8]))
    menu.addSection("색상")
    menu.addAction(grid_action(PALETTE))
    menu.addSeparator()
    menu.addAction(none_label, lambda: on_pick(""))

    def custom():
        c = QColorDialog.getColor(QColor("#ffffff"), None, "색 선택")
        if c.isValid():
            on_pick(c.name())
    menu.addAction("사용자 지정…", custom)
    return menu


class OnOff(QPushButton):
    """DesktopCal 설정의 '켜짐/꺼짐' 토글 버튼."""
    toggledValue = Signal(bool)

    def __init__(self, checked=False):
        super().__init__()
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedWidth(64)
        self.setCursor(Qt.PointingHandCursor)
        self._sync()
        self.toggled.connect(self._sync)
        self.toggled.connect(self.toggledValue.emit)

    def _sync(self):
        on = self.isChecked()
        self.setText("켜짐" if on else "꺼짐")
        self.setStyleSheet(
            "QPushButton{border-radius:11px;padding:3px 0;font-weight:bold;"
            + ("background:#2f80ed;color:white;border:1px solid #2f80ed;}" if on
               else "background:#3a3a3a;color:#c8c8c8;border:1px solid #555;}" if is_dark()
               else "background:#e6e6e6;color:#666;border:1px solid #ccc;}"))


class ColorButton(QPushButton):
    """색 견본 버튼(누르면 팔레트)."""
    colorChanged = Signal(str)

    def __init__(self, color, allow_none=False):
        super().__init__()
        self.allow_none = allow_none
        self.setFixedSize(64, 24)
        self.setCursor(Qt.PointingHandCursor)
        self.set_color(color)
        self.clicked.connect(self._pick)

    def set_color(self, color):
        self.color = color
        self.setStyleSheet(f"QPushButton{{background:{color or 'transparent'};border:1px solid #999;border-radius:3px;}}")
        self.setText("" if color else "없음")

    def _pick(self):
        def picked(c):
            if c or self.allow_none:
                self.set_color(c)
                self.colorChanged.emit(c)
        color_menu(picked, none_label="없음" if self.allow_none else "기본값", parent=self).exec(
            self.mapToGlobal(self.rect().bottomLeft()))
