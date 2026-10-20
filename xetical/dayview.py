"""하루 상세 보기(2-2): 날짜 숫자를 누르면 그날의 종일 일정·할 일과 시간표(타임라인)를 보여 준다."""
from datetime import date, datetime, time, timedelta

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QScrollArea, QToolButton, QVBoxLayout, QWidget)

from .view import fmt_time, weekday_name

HOUR_PX = 46
LEFT = 58  # 시각 글자 칸 폭

STYLE = """
DayView { background:#262626; }
QLabel { color:#e8e8e8; }
QLabel#title { color:#ffffc0; font-size:15px; font-weight:bold; }
QToolButton, QPushButton { color:#eee; background:#3a3a3a; border:1px solid #4a4a4a; border-radius:3px; padding:3px 8px; }
QToolButton:hover, QPushButton:hover { background:#4a4a4a; }
QPushButton#item { background:transparent; border:none; text-align:left; padding:2px 4px; }
QPushButton#item:hover { background:#3a3a3a; }
QScrollArea { border:none; background:#262626; }
"""


class Timeline(QWidget):
    def __init__(self, view):
        super().__init__()
        self.view = view
        self.setMinimumHeight(24 * HOUR_PX + 10)
        self.setMouseTracking(True)
        self._blocks = []

    def _layout_blocks(self):
        """겹치는 일정은 나란히 놓는다."""
        evs = sorted(self.view.timed, key=lambda e: (e.start, e.end))
        placed, cols_end, group, out = [], [], [], []
        group_end = None
        for ev in evs:
            if group_end is not None and ev.start >= group_end:
                out.append((group, len(cols_end)))
                group, cols_end, group_end = [], [], None
            col = next((i for i, end in enumerate(cols_end) if end <= ev.start), None)
            if col is None:
                cols_end.append(ev.end)
                col = len(cols_end) - 1
            else:
                cols_end[col] = ev.end
            group.append((ev, col))
            group_end = max(group_end, ev.end) if group_end else ev.end
        if group:
            out.append((group, len(cols_end)))
        for grp, ncols in out:
            for ev, col in grp:
                placed.append((ev, col, ncols))
        return placed

    def _y(self, dt):
        day0 = datetime.combine(self.view.day, time.min)
        minutes = max(0, min(24 * 60, (dt - day0).total_seconds() / 60))
        return 5 + minutes / 60 * HOUR_PX

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#262626"))
        cfg = self.view.app.cfg
        small = QFont(cfg["date_font"]["family"])
        small.setPointSizeF(8.5)
        p.setFont(small)
        w = self.width()
        for h in range(25):
            y = 5 + h * HOUR_PX
            p.setPen(QPen(QColor(255, 255, 255, 40 if h % 6 else 70), 1))
            p.drawLine(QPointF(LEFT, y), QPointF(w - 6, y))
            if h < 24:
                p.setPen(QColor("#9a9a9a"))
                p.drawText(QRectF(0, y - 8, LEFT - 8, 16), Qt.AlignRight | Qt.AlignVCenter,
                           fmt_time(datetime.combine(self.view.day, time(h)), cfg))
        self._blocks = []
        font = QFont(cfg["item_font"]["family"])
        font.setPointSizeF(9)
        fm = QFontMetrics(font)
        avail = w - LEFT - 10
        for ev, col, ncols in self._layout_blocks():
            y0, y1 = self._y(ev.start), max(self._y(ev.end), self._y(ev.start) + fm.height() + 4)
            bw = avail / ncols
            r = QRectF(LEFT + 2 + col * bw, y0 + 1, bw - 4, y1 - y0 - 2)
            c = QColor(ev.text_color or ev.color)
            c.setAlphaF(0.85)
            p.setPen(Qt.NoPen)
            p.setBrush(c)
            p.drawRoundedRect(r, 4, 4)
            p.setFont(font)
            p.setPen(QColor("#ffffff"))
            label = f"{fmt_time(ev.start, cfg)} {ev.title}"
            p.drawText(r.adjusted(5, 2, -4, -2), Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, label)
            self._blocks.append((r, ev))
        if self.view.day == date.today():  # 지금 시각 선
            y = self._y(datetime.now())
            p.setPen(QPen(QColor("#ff5252"), 2))
            p.drawLine(QPointF(LEFT - 4, y), QPointF(w - 6, y))
        p.end()

    def _block_at(self, pos):
        return next((ev for r, ev in self._blocks if r.contains(pos)), None)

    def mousePressEvent(self, e):
        ev = self._block_at(e.position())
        if ev and e.button() == Qt.LeftButton:
            self.view.app.edit_event(ev)

    def mouseDoubleClickEvent(self, e):
        if self._block_at(e.position()):
            return
        minutes = max(0, (e.position().y() - 5) / HOUR_PX * 60)
        minutes = int(minutes // 30 * 30)
        self.view.app.new_event(self.view.day, start_time=time(min(23, minutes // 60), minutes % 60))

    def mouseMoveEvent(self, e):
        self.setCursor(Qt.PointingHandCursor if self._block_at(e.position()) else Qt.ArrowCursor)


class DayView(QWidget):
    def __init__(self, app, day):
        super().__init__(None, Qt.Window | Qt.WindowStaysOnTopHint)
        self.app, self.day = app, day
        self.setWindowTitle("하루 상세 보기")
        self.setStyleSheet(STYLE)
        self.resize(400, 620)
        self.timed, self.allday = [], []

        head = QHBoxLayout()
        prev = QToolButton(text="◀")
        prev.clicked.connect(lambda: self.set_day(self.day - timedelta(days=1)))
        nxt = QToolButton(text="▶")
        nxt.clicked.connect(lambda: self.set_day(self.day + timedelta(days=1)))
        self.title = QLabel()
        self.title.setObjectName("title")
        today = QPushButton("오늘")
        today.clicked.connect(lambda: self.set_day(date.today()))
        add = QPushButton("+ 새 일정")
        add.clicked.connect(lambda: self.app.new_event(self.day))
        head.addWidget(prev)
        head.addWidget(self.title, 1, Qt.AlignCenter)
        head.addWidget(nxt)
        head.addWidget(today)
        head.addWidget(add)

        self.allday_box = QVBoxLayout()
        self.allday_box.setSpacing(1)
        self.timeline = Timeline(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.timeline)
        self.scroll_area = scroll
        hint = QLabel("빈 시간대를 더블클릭하면 그 시각에 새 일정 · 일정을 누르면 편집")
        hint.setStyleSheet("color:#888;font-size:11px;")

        lay = QVBoxLayout(self)
        lay.addLayout(head)
        lay.addLayout(self.allday_box)
        lay.addWidget(scroll, 1)
        lay.addWidget(hint)
        QShortcut(QKeySequence.Undo, self, self.app.undo)
        self.minute = QTimer(self, interval=60_000, timeout=self.timeline.update)
        self.minute.start()
        self.set_day(day)

    def set_day(self, d):
        self.day = d
        if not self.app.store.covers(d, d + timedelta(days=1)):
            self.app.win.go_to(d)  # 받아 둔 범위 밖이면 달력도 그쪽으로 옮겨 동기화
        self.refresh()
        first = min((e.start for e in self.timed), default=datetime.combine(d, time(7)))
        self.scroll_area.verticalScrollBar().setValue(max(0, int(self.timeline._y(first)) - HOUR_PX))

    def refresh(self):
        cfg = self.app.cfg
        self.title.setText(f"{self.day.month}월 {self.day.day}일 ({weekday_name(self.day.weekday(), cfg, 'short')})")
        evs = self.app.win.cell_events(self.day)
        self.allday = [e for e in evs if e.all_day or e.task_list]
        self.timed = [e for e in evs if not e.all_day and not e.task_list]
        while self.allday_box.count():
            w = self.allday_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        holidays = self.app.store.holiday_names(self.day, cfg["show_holidays"], cfg["show_observances"])
        if holidays:
            lbl = QLabel(", ".join(holidays))
            lbl.setStyleSheet(f"color:{cfg['holiday_text']};")
            self.allday_box.addWidget(lbl)
        for ev in self.allday:
            text = self.app.win.item_text(ev, self.day)
            b = QPushButton(text)
            b.setObjectName("item")
            style = f"color:{self.app.win.item_color(ev)};"
            if ev.done:
                style += "text-decoration:line-through;"
            b.setStyleSheet(style)
            b.setCursor(Qt.PointingHandCursor)
            if ev.task_list:  # 할 일은 누르면 바로 완료/미완료(예정은 그 회차 완료)
                b.setToolTip("누르면 완료/미완료 · 우클릭하면 편집")
                b.clicked.connect(lambda _=False, ev=ev: self.app.toggle_task(ev))
                b.setContextMenuPolicy(Qt.CustomContextMenu)
                b.customContextMenuRequested.connect(lambda _p, ev=ev: self.app.edit_event(ev))
            else:
                b.clicked.connect(lambda _=False, ev=ev: self.app.edit_event(ev))
            self.allday_box.addWidget(b)
        self.timeline.update()
