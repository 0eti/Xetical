"""바탕화면 달력 창: 그리기와 마우스 처리. 화면 구성은 DesktopCal을 따른다."""
import calendar as pycal
import ctypes.wintypes as wt
import math
import time
from datetime import date, timedelta

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QGuiApplication, QKeySequence, QLinearGradient, QPainter,
                           QPainterPath, QPen, QShortcut)
from PySide6.QtWidgets import QToolTip, QWidget

from . import desktop
from .almanac import lunar, solar_term, week_number
from .model import COUNTDOWN_TYPES
from .mood import MoodPanel, active_tracker, color_label

WEEKDAY_NAMES = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
WEEKDAY_EN = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MONTH_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
DEFAULT_TITLE = "오늘은 {year}년{month}월{day}일 {weekday}"
C_WARN = "#ffb347"
EDGE = 8          # 크기 조절 모드에서 테두리 잡는 폭
DRAG_START = 6    # 이만큼 움직여야 끌기로 본다(px)
INVITE_MARK = {"needsAction": "✉ ", "tentative": "? "}


def shade(hex_color, factor, alpha=1.0):
    c = QColor(hex_color)
    c = QColor(min(255, int(c.red() * factor)), min(255, int(c.green() * factor)), min(255, int(c.blue() * factor)))
    c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


def make_font(spec, scale=1.0, bold=None):
    f = QFont(spec.get("family", "Malgun Gothic"))
    f.setPointSizeF(max(5.0, spec.get("size", 9) * scale))
    f.setBold(spec.get("bold", False) if bold is None else bold)
    return f


# ---------- 표시 형식(설정 3-3, 4-6) ----------
def fmt_time(dt, cfg):
    if cfg.get("time_format") == "ampm":
        h = dt.hour % 12 or 12
        return f"{'오전' if dt.hour < 12 else '오후'} {h}:{dt.minute:02d}"
    return f"{dt:%H:%M}"


def weekday_name(wd, cfg, style=None):
    style = style or cfg.get("weekday_style", "full")
    if style == "short":
        return WEEKDAY_NAMES[wd][0]
    if style == "en":
        return WEEKDAY_EN[wd]
    return WEEKDAY_NAMES[wd]


def month_label(d, cfg):
    style = cfg.get("month_label", "ko")
    if style == "en":
        return MONTH_EN[d.month - 1]
    if style == "num":
        return f"{d.month}/{d.day}"
    return f"{d.month}월"


def title_text(cfg, t=None):
    t = t or date.today()
    values = {"year": t.year, "month": t.month, "day": t.day, "weekday": weekday_name(t.weekday(), cfg),
              "week": week_number(t, cfg["week_start"], cfg["first_week"])}
    try:
        return (cfg.get("title_format") or DEFAULT_TITLE).format(**values)
    except (KeyError, ValueError, IndexError):
        return DEFAULT_TITLE.format(**values)


class CalendarWindow(QWidget):
    cellEditRequested = Signal(object, QRect)  # date, 셀의 화면 좌표
    eventEditRequested = Signal(object)        # Event
    dayMenuRequested = Signal(object, QPoint)  # date(또는 None), 화면 좌표
    eventMenuRequested = Signal(object, object, QPoint)  # Event, date, 화면 좌표
    dayViewRequested = Signal(object)          # date (날짜 숫자를 누름)
    taskToggleRequested = Signal(object)       # 칸에서 할 일 ☐ 를 누름
    eventMoveRequested = Signal(object, int)   # Event, 옮길 날 수
    undoRequested = Signal()
    copyRequested = Signal()
    cutRequested = Signal()
    pasteRequested = Signal(object)            # 붙여 넣을 날짜
    deleteRequested = Signal()
    menuRequested = Signal(QPoint)
    pluginsRequested = Signal(QPoint)
    accountRequested = Signal(QPoint)
    mobileRequested = Signal()
    cloudClicked = Signal()
    statusClicked = Signal()
    navigated = Signal()
    adjustFinished = Signal()
    moodTrackerChosen = Signal(str)            # 트래커 이름을 누름(트래커 id)
    moodMenuRequested = Signal(object, QPoint) # 트래커 칸을 누름(date, 화면 좌표)

    def __init__(self, cfg, store, cell_colors):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.setWindowTitle("Xetical")
        self.cfg, self.store, self.cell_colors = cfg, store, cell_colors
        self.countdowns = []     # 카운트다운 일정(아래쪽 바)
        self.moods = None        # 트래커 칠한 날(MoodData, app이 넣어 준다)
        self.mood = MoodPanel()
        self.anchor = self.today_anchor()
        self.status, self.status_clickable = "", False
        self.sync_info = None  # ☁ 풍선 도움말에 덧붙일 동기화 정보(app이 넣어 준다)
        self.cloud_state = "login"  # login | syncing | ok | error
        self.adjusting = False
        self.click_through = False
        self.selected_event = None
        self.selected_day = None
        self._owner = None
        self._allow_z = False
        self._event_hits, self._check_hits, self._date_hits, self._cells = [], [], [], []
        self._scroll = {}        # date -> 셀 내용 스크롤(px)
        self._overflow = set()
        self._hover_btn = self._hover_day = self._tip = None
        self._drag = None
        self._pending_day = None
        self._last_click = (0.0, QPointF())
        self._dayview_timer = QTimer(self, singleShot=True, timeout=self._fire_dayview)
        # 키보드 단축키: 달력을 클릭해서 활성화했을 때만 동작한다(1-3)
        for seq, fn in ((QKeySequence.Undo, self.undoRequested.emit), (QKeySequence.Copy, self.copyRequested.emit),
                        (QKeySequence.Cut, self.cutRequested.emit), (QKeySequence.Delete, self.deleteRequested.emit),
                        (QKeySequence.Paste, lambda: self.pasteRequested.emit(self.selected_day or self._hover_day))):
            QShortcut(QKeySequence(seq), self, fn)
        QShortcut(QKeySequence("Esc"), self, self._cancel_drag)

    # ---------- 바탕화면 부착 ----------
    def show_on_desktop(self):
        hwnd = int(self.winId())
        self._owner = desktop.attach(hwnd)
        self.show()
        desktop.attach(hwnd)  # show() 중에 Qt가 확장 스타일을 다시 쓸 수 있어서 한 번 더
        self._to_bottom()
        desktop.set_blur(hwnd, self.cfg["glass"])
        desktop.set_click_through(hwnd, self.click_through and not self.adjusting)

    def _to_bottom(self):
        self._allow_z = True
        desktop.send_to_bottom(int(self.winId()))
        self._allow_z = False

    def ensure_attached(self):
        """탐색기가 재시작되면 소유자가 사라지므로 주기적으로 다시 붙인다."""
        if self.isVisible() and not desktop.is_attached(int(self.winId()), self._owner):
            self.show_on_desktop()

    def set_click_through(self, on):
        self.click_through = on
        if self.isVisible():
            desktop.set_click_through(int(self.winId()), on and not self.adjusting)

    def activate_keys(self):
        """달력을 누르면 키보드 입력(Ctrl+Z 등)을 받도록 활성화한다. Z순서는 잠겨 있어 앞으로 나오지 않는다."""
        if self.adjusting:
            return
        hwnd = int(self.winId())
        if self.click_through:
            desktop.force_foreground(hwnd)  # 클릭은 탐색기가 받았으므로 입력 권한을 빌려 온다
        else:
            desktop.user32.SetForegroundWindow(wt.HWND(hwnd))
        self._to_bottom()  # 혹시라도 위로 올라갔으면 다시 바탕화면 높이로

    def nativeEvent(self, event_type, message):
        et = event_type.data() if hasattr(event_type, "data") else event_type
        if et == b"windows_generic_MSG":
            msg = wt.MSG.from_address(int(message))
            if msg.message == desktop.WM_WINDOWPOSCHANGING and not self._allow_z:
                desktop.block_zorder(msg.lParam)
            elif msg.message == desktop.WM_MOUSEACTIVATE:
                return True, desktop.MA_NOACTIVATE
        return super().nativeEvent(event_type, message)

    # ---------- 날짜 계산 ----------
    @property
    def mode(self):
        return self.cfg["display_mode"]

    def week_first(self, d):
        return d - timedelta(days=(d.weekday() - self.cfg["week_start"]) % 7)

    def today_anchor(self, today=None):
        t = today or date.today()
        mode = self.cfg["display_mode"]
        if mode == "month":
            return t.replace(day=1)
        if mode == "rolling_row":
            return t
        if mode == "fixed_row":
            return self.week_first(t)
        return self.week_first(t) - timedelta(days=7 * self.cfg["passed_weeks"])

    def grid_shape(self):
        if self.mode == "month":
            first = self.week_first(self.anchor)
            days = pycal.monthrange(self.anchor.year, self.anchor.month)[1]
            rows = math.ceil(((self.anchor - first).days + days) / 7)
            return first, rows, 7
        if self.mode == "fixed_row":
            return self.anchor, 1, 7
        if self.mode == "rolling_row":
            return self.anchor, 1, max(1, self.cfg["max_columns"])
        return self.anchor, max(1, self.cfg["weeks"]), 7

    def view_range(self):
        first, rows, cols = self.grid_shape()
        return first, first + timedelta(days=rows * cols)

    def go_today(self):
        self.go_to(date.today())

    def go_to(self, d):
        if self.mode == "month":
            self.anchor = d.replace(day=1)
        elif self.mode == "rolling_row":
            self.anchor = d
        else:
            self.anchor = self.week_first(d)
            if d == date.today() and self.mode == "rolling":
                self.anchor = self.today_anchor()
        self._scroll.clear()
        self.navigated.emit()
        self.update()

    def shift_weeks(self, n):
        self.anchor += timedelta(days=7 * n)
        self.navigated.emit()
        self.update()

    def shift_page(self, n):
        """이전/다음 페이지. 월 모드는 한 달씩, 단일 행은 한 화면씩."""
        if self.mode == "fixed_row":
            self.anchor += timedelta(days=7 * n)
        elif self.mode == "rolling_row":
            self.anchor += timedelta(days=self.cfg["max_columns"] * n)
        else:
            if self.mode == "month":
                ref = self.anchor
            else:
                week = [self.anchor + timedelta(days=i) for i in range(7)]
                ref = next((d for d in week if d.day == 1), self.anchor)
            y, m = divmod(ref.year * 12 + ref.month - 1 + n, 12)
            target = date(y, m + 1, 1)
            self.anchor = target if self.mode == "month" else self.week_first(target)
        self._scroll.clear()
        self.navigated.emit()
        self.update()

    def is_today_page(self):
        return self.anchor == self.today_anchor()

    def is_off_day(self, d):
        return self.cfg["show_off_days"] and d.weekday() in self.cfg["off_days"]

    def visible_calendar(self, cal_id):
        vis = self.cfg["calendar_visibility"]
        if cal_id in vis:
            return vis[cal_id]
        cal = self.store.calendar(cal_id)
        return cal["selected"] if cal else True

    def _shown(self, ev):
        c = self.cfg
        if ev.task_list:
            if not c["show_tasks"] or (ev.virtual and not c["show_task_projection"]):
                return False
            if ev.done and not c["show_done_tasks"]:
                return False
        elif ev.done and not c["show_done"]:
            return False
        if ev.response == "declined" and not c["show_declined"]:
            return False
        return True

    def cell_events(self, d):
        evs = [e for e in self.store.on_day(d, self.visible_calendar) if self._shown(e)]
        if self.cfg["done_to_bottom"]:
            evs = sorted(evs, key=lambda e: e.done)  # 안정 정렬이라 나머지 순서는 유지
        return evs

    def _uses_bars(self):
        return self.cfg["span_bars"]

    # ---------- 레이아웃 ----------
    def _margin(self):
        return round(7 * self._s()) if self.cfg["shadow"] else 0

    def _s(self):
        return max(0.7, self.cfg["date_font"]["size"] / 9)

    def _layout(self):
        s, m = self._s(), self._margin()
        W, H = self.width() - 2 * m, self.height() - 2 * m
        show_title = self.cfg["show_title_bar"] or self.adjusting
        header_h = round(40 * s) if show_title else 0
        dow_h = round(22 * s)
        cd_h = round(QFontMetrics(make_font(self.cfg["item_font"], 0.85)).height() + 8 * s) if self._bar_items() else 0
        mood_h = self.mood.height(self.cfg, W, s, self._mood_font(), date.today().year) if self.cfg["mood_on"] else 0
        wn_w = round(26 * s) if self.cfg["week_number"] and self.mode != "rolling_row" else 0
        first, rows, cols = self.grid_shape()
        grid = QRectF(m + wn_w, m + header_h + dow_h, W - wn_w, H - header_h - dow_h - cd_h - mood_h)
        return {"s": s, "m": m, "W": W, "H": H, "header_h": header_h, "dow_h": dow_h, "cd_h": cd_h, "wn_w": wn_w,
                "mood_h": mood_h,
                "first": first, "rows": rows, "cols": cols, "grid": grid,
                "cw": grid.width() / cols, "ch": grid.height() / rows}

    def _cell_rect(self, L, i):
        r, c = divmod(i, L["cols"])
        g = L["grid"]
        rect = QRectF(g.left() + c * L["cw"], g.top() + r * L["ch"], L["cw"], L["ch"])
        gap = self.cfg["cell_gap"] / 2
        return rect.adjusted(gap, gap, -gap, -gap)

    def _buttons(self):
        L = self._layout()
        if not L["header_h"]:
            return []
        s, m = L["s"], L["m"]
        if self.adjusting:
            keys = ["done"]
        else:  # DesktopCal 제목줄과 같은 순서. '오늘로'는 다른 페이지를 볼 때만 나타난다.
            keys = [] if self.is_today_page() else ["today"]
            keys += ["plugins", "mobile", "cloud", "account", "prev", "next", "menu"]
        bw, gap = 30 * s, 6 * s
        x = m + L["W"] - 6 * s
        rects = []
        for k in reversed(keys):
            w = bw * (2.2 if k == "done" else 1)
            x -= w
            rects.append((k, QRectF(x, m + (L["header_h"] - 26 * s) / 2, w, 26 * s)))
            x -= gap
        return list(reversed(rects))

    def _button_at(self, pos):
        return next((k for k, r in self._buttons() if r.contains(pos)), None)

    def _day_at(self, pos):
        return next((d for r, d in self._cells if r.contains(pos)), None)

    def _event_at(self, pos):
        return next((ev for r, ev in self._event_hits if r.contains(pos)), None)

    def _check_at(self, pos):
        return next((ev for r, ev in self._check_hits if r.contains(pos)), None)

    def _date_label_at(self, pos):
        return next((d for r, d in self._date_hits if r.contains(pos)), None)

    def cell_global_rect(self, d):
        r = next((r for r, dd in self._cells if dd == d), None)
        if r is None:
            return QRect()
        return QRect(self.mapToGlobal(r.topLeft().toPoint()), r.size().toSize())

    # ---------- 여러 날 막대(1-6) ----------
    def _row_bars(self, L):
        """행마다 여러 날 일정을 겹치지 않는 '줄(lane)'에 배치한다. {행: (줄 수, [(ev, 시작칸, 끝칸, 줄)])}"""
        out = {}
        if not self._uses_bars():
            return out
        for r in range(L["rows"]):
            row_first = L["first"] + timedelta(days=r * L["cols"])
            row_last = row_first + timedelta(days=L["cols"] - 1)
            seen, evs = set(), []
            for i in range(L["cols"]):
                for ev in self.cell_events(row_first + timedelta(days=i)):
                    if ev.is_multi_day and ev.id not in seen:
                        seen.add(ev.id)
                        evs.append(ev)
            evs.sort(key=lambda e: (e.first_day, -len(e.days())))
            lanes, placed = [], []
            for ev in evs:
                s = max(ev.first_day, row_first)
                e = min(ev.days()[-1], row_last)
                lane = next((i for i, end in enumerate(lanes) if end < s), None)
                if lane is None:
                    lanes.append(e)
                    lane = len(lanes) - 1
                else:
                    lanes[lane] = e
                placed.append((ev, (s - row_first).days + r * L["cols"], (e - row_first).days + r * L["cols"], lane))
            out[r] = (len(lanes), placed)
        return out

    # ---------- 그리기 ----------
    def paintEvent(self, _):
        p = QPainter(self)
        try:
            self._paint(p)
        finally:
            p.end()

    def _bg(self, hex_color, factor=1.0):
        return shade(hex_color, factor, self.cfg.opacity)

    def _vgrad(self, rect, color, top, bot):
        g = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        g.setColorAt(0, self._bg(color, top))
        g.setColorAt(1, self._bg(color, bot))
        return g

    def _paint(self, p):
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        L = self._layout()
        s, m = L["s"], L["m"]
        base = self.cfg["cell_bg"]
        self._event_hits, self._check_hits, self._date_hits, self._cells, self._overflow = [], [], [], [], set()

        if m:  # 그림자
            for i in range(m, 0, -1):
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(0, 0, 0, int(70 * (1 - i / m) ** 2) + 4))
                p.drawRoundedRect(QRectF(m - i, m - i + 2, L["W"] + 2 * i, L["H"] + 2 * i), i, i)

        if L["header_h"]:
            self._paint_header(p, L)

        # 요일 줄 (+ 주 번호 칸 머리)
        dow_top = m + L["header_h"]
        p.fillRect(QRectF(m, dow_top, L["W"], L["dow_h"]), self._bg(base, 0.48))
        date_font = make_font(self.cfg["date_font"])
        p.setFont(make_font(self.cfg["date_font"], bold=False))
        for c in range(L["cols"]):
            d = L["first"] + timedelta(days=c)
            r = QRectF(L["grid"].left() + c * L["cw"], dow_top, L["cw"], L["dow_h"])
            p.setPen(QColor(self.cfg["weekday_colors"].get(str(d.weekday())) or "#e8e8e8"))
            label = weekday_name(d.weekday(), self.cfg)
            if self.mode == "rolling_row":
                label = f"{d.month}/{d.day} {weekday_name(d.weekday(), self.cfg, 'short')}"
            p.drawText(r, Qt.AlignCenter, label)
        if L["wn_w"]:
            p.fillRect(QRectF(m, L["grid"].top(), L["wn_w"], L["grid"].height()), self._bg(base, 0.42))

        bars = self._row_bars(L)
        bar_h = QFontMetrics(make_font(self.cfg["item_font"], 0.9)).height() + 2 * s
        today = date.today()
        for i in range(L["rows"] * L["cols"]):
            d = L["first"] + timedelta(days=i)
            cell = self._cell_rect(L, i)
            self._cells.append((cell, d))
            lanes = bars.get(i // L["cols"], (0, []))[0]
            self._paint_cell(p, L, d, cell, today, date_font, lanes * (bar_h + 1 * s))
            if L["wn_w"] and i % L["cols"] == 0:
                wr = QRectF(m, cell.top(), L["wn_w"], cell.height())
                p.setFont(make_font(self.cfg["date_font"], 0.85, bold=False))
                p.setPen(QColor(self.cfg["lunar_text"]))
                p.drawText(wr.adjusted(0, 3 * s, 0, 0), Qt.AlignHCenter | Qt.AlignTop,
                           str(week_number(d, self.cfg["week_start"], self.cfg["first_week"])))

        if self.cfg["show_content"]:
            self._paint_bars(p, L, bars, bar_h, date_font)
        if self.cfg["month_interval"] and self.mode != "month":
            self._paint_month_lines(p, L)
        if L["mood_h"]:
            r = QRectF(m, m + L["H"] - L["cd_h"] - L["mood_h"], L["W"], L["mood_h"])
            # 배경은 투명(사용자 요청). 완전히 투명한 픽셀은 클릭이 바탕화면으로 빠지므로 1/255만 칠한다.
            self.mood.paint(p, r, self.cfg, self.moods, date.today(), self._mood_font(), QColor(0, 0, 0, 1), s)
        else:
            self.mood.tabs, self.mood.boxes = [], []
        if L["cd_h"]:
            self._paint_countdown_bar(p, L)
        self._paint_drag(p, L)
        if self.adjusting:
            p.setPen(QPen(QColor(C_WARN), 2, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRect(QRectF(m + 1, m + 1, L["W"] - 2, L["H"] - 2))

    def _paint_header(self, p, L):
        s, m = L["s"], L["m"]
        hdr = QRectF(m, m, L["W"], L["header_h"])
        p.fillRect(hdr, self._vgrad(hdr, self.cfg["cell_bg"], 0.58, 0.38))
        p.setPen(QPen(QColor(255, 255, 255, 30), 1))
        p.drawLine(QPointF(m, m + 0.5), QPointF(m + L["W"], m + 0.5))
        btns = self._buttons()
        right = btns[0][1].left() if btns else m + L["W"]
        title_rect = QRectF(m + 8 * s, m, right - m - 16 * s, L["header_h"])
        title_font = QFont(self.cfg["date_font"]["family"])
        title_font.setPointSizeF(10 * s)
        title_font.setBold(True)
        p.setFont(title_font)
        if self.adjusting:
            p.setPen(QColor(C_WARN))
            p.drawText(title_rect, Qt.AlignCenter, "제목줄을 끌어 이동 · 테두리를 끌어 크기 조절")
        else:
            title = title_text(self.cfg)
            p.setPen(QColor("#ffffff"))
            if self.status:
                fm = QFontMetrics(title_font)
                tw = fm.horizontalAdvance(title)
                p.drawText(title_rect, Qt.AlignVCenter | Qt.AlignLeft, title)
                p.setPen(QColor(C_WARN))
                st = QRectF(title_rect.left() + tw + 12 * s, m, title_rect.width() - tw - 12 * s, L["header_h"])
                p.drawText(st, Qt.AlignVCenter | Qt.AlignRight,
                           fm.elidedText(self.status, Qt.ElideRight, int(st.width())))
            else:
                p.drawText(title_rect, Qt.AlignCenter, title)
        for key, r in btns:
            if key == self._hover_btn:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(255, 255, 255, 40))
                p.drawRoundedRect(r, 4 * s, 4 * s)
            self._draw_icon(p, key, r, s)

    def _paint_cell(self, p, L, d, cell, today, date_font, bars_h):
        s = L["s"]
        holiday = self.store.holiday_names(d, self.cfg["show_holidays"], self.cfg["show_observances"])
        public = self.store.holiday_names(d, True, False) if self.cfg["show_holidays"] else []
        custom = self.cell_colors.get(d.isoformat())
        if custom:
            color, top, bot = custom, 0.95, 0.85
        elif self.is_off_day(d) or public:
            color, top, bot = self.cfg["holiday_bg"], 0.72, 0.64
        else:
            color, top, bot = self.cfg["cell_bg"], 0.72, 0.62
        if d == today:
            top, bot = top * 1.12, bot * 1.12
        p.fillRect(cell, self._vgrad(cell, color, top, bot))
        if d == today and self.cfg["today_fill"]:
            p.fillRect(cell, shade(self.cfg["today_fill"], 1.0, 0.45 * self.cfg.opacity + 0.15))
        if self._drag and self._drag.get("active") and d == self._drag.get("target"):
            p.fillRect(cell, QColor(255, 255, 255, 40))
        # 3D 테두리(위·왼쪽 밝게, 아래·오른쪽 어둡게)
        p.setPen(QPen(QColor(255, 255, 255, 34), 1))
        p.drawLine(QPointF(cell.left() + 0.5, cell.top() + 0.5), QPointF(cell.right() - 0.5, cell.top() + 0.5))
        p.drawLine(QPointF(cell.left() + 0.5, cell.top() + 0.5), QPointF(cell.left() + 0.5, cell.bottom() - 0.5))
        p.setPen(QPen(QColor(0, 0, 0, 70), 1))
        p.drawLine(QPointF(cell.left() + 0.5, cell.bottom() - 0.5), QPointF(cell.right() - 0.5, cell.bottom() - 0.5))
        p.drawLine(QPointF(cell.right() - 0.5, cell.top() + 0.5), QPointF(cell.right() - 0.5, cell.bottom() - 0.5))
        if d == today:
            bw = max(1, self.cfg["today_border_width"]) * s
            p.setPen(QPen(QColor(self.cfg["today_border"]), bw))
            p.setBrush(Qt.NoBrush)
            p.drawRect(cell.adjusted(bw / 2, bw / 2, -bw / 2, -bw / 2))

        dim = self.mode == "month" and d.month != self.anchor.month
        p.setOpacity(0.45 if dim else 1.0)
        pad = 3 * s
        fm = QFontMetrics(date_font)
        lh = fm.height()
        x, y, w = cell.left() + pad, cell.top() + pad - 1, cell.width() - 2 * pad
        first_label = d.day == 1 and self.mode != "month"
        label = month_label(d, self.cfg) if first_label else str(d.day)
        if first_label:
            p.fillRect(QRectF(cell.left(), cell.top(), cell.width(), lh + pad), self._bg(color, 0.42))
        p.setFont(date_font)
        day_color = self.cfg["weekday_colors"].get(str(d.weekday())) or self.cfg["date_font"]["color"]
        p.setPen(QColor(self.cfg["holiday_text"]) if (public and not first_label) else QColor(day_color))
        p.drawText(QRectF(x, y, w, lh), Qt.AlignLeft | Qt.AlignVCenter, label)
        used = fm.horizontalAdvance(label) + 8 * s
        self._date_hits.append((QRectF(x - 2, y, used, lh), d))
        right = x + w
        side = ""
        if self.cfg["show_solar_terms"]:
            side = solar_term(d)
        if not side and self.cfg["show_lunar"]:
            side = lunar(d)
        if side:
            small = make_font(self.cfg["date_font"], 0.85, bold=False)
            p.setFont(small)
            p.setPen(QColor(self.cfg["lunar_text"]))
            p.drawText(QRectF(x, y, w, lh), Qt.AlignRight | Qt.AlignVCenter, side)
            right -= QFontMetrics(small).horizontalAdvance(side) + 4 * s
            p.setFont(date_font)
        if holiday:
            p.setPen(QColor(self.cfg["holiday_text"]))
            hr = QRectF(x + used, y, max(0.0, right - x - used), lh)
            p.drawText(hr, Qt.AlignLeft | Qt.AlignVCenter, fm.elidedText(", ".join(holiday), Qt.ElideRight,
                                                                         int(hr.width())))
        if self.cfg["show_content"]:
            top_y = y + lh + 2 * s + bars_h
            self._paint_items(p, L, d, cell, QRectF(x, top_y, w, cell.bottom() - pad - top_y))
        p.setOpacity(1.0)

    def item_text(self, ev, d):
        cfg = self.cfg
        text = ev.title
        if ev.task_list:
            return ("☑ " if ev.done else "☐ ") + text
        if ev.response in INVITE_MARK:
            text = INVITE_MARK[ev.response] + text
        if not ev.all_day:
            start_today, end_today = ev.start.date() == d, ev.end.date() == d
            if start_today and end_today and cfg["show_end_time"]:
                text = f"[{fmt_time(ev.start, cfg)}-{fmt_time(ev.end, cfg)}] {text}"
            elif start_today:
                text = f"[{fmt_time(ev.start, cfg)}{'~' if not end_today and cfg['show_end_time'] else ''}] {text}"
        if cfg["show_alarm_time"]:
            a = ev.alarm_parts()
            if a:
                text = f"⏰{a[0]:02d}:{a[1]:02d} {text}"
        return text

    def item_color(self, ev):
        if ev.text_color:
            return ev.text_color
        if self.cfg["calendar_text_color"]:
            return ev.color
        return self.cfg["item_font"]["color"]

    def _paint_items(self, p, L, d, cell, area):
        s = L["s"]
        events = [e for e in self.cell_events(d) if not (self._uses_bars() and e.is_multi_day)]
        if not events or area.height() <= 0:
            return
        limit = self.cfg["max_items"]
        hidden = 0
        if limit and len(events) > limit:
            hidden = len(events) - limit
            events = events[:limit]
        font = make_font(self.cfg["item_font"])
        fm = QFontMetrics(font)
        lh = fm.height()
        gap = self.cfg["line_gap"] + 1 * s
        bullet = self.cfg["bullet"]
        bar = 4 * s if self.cfg["show_color_bar"] else 0
        single = self.cfg["single_line"]
        flags = Qt.AlignLeft | Qt.AlignTop | (0 if single else Qt.TextWordWrap)
        offset = self._scroll.get(d, 0)
        base_opacity = p.opacity()
        p.save()
        p.setClipRect(area)
        p.setFont(font)
        ey = area.top() - offset
        tw = area.width() - bar
        for idx, ev in enumerate(events):
            prefix = f"{idx + 1}. " if bullet == "number" else "• " if bullet == "dot" else ""
            text = prefix + self.item_text(ev, d)
            if single:
                text = fm.elidedText(text, Qt.ElideRight, int(tw))
                h = lh
            else:
                h = fm.boundingRect(QRectF(0, 0, tw, 10000).toRect(), int(flags), text).height()
            tr = QRectF(area.left() + bar, ey, tw, h)
            vis = QRectF(area.left(), ey, area.width(), h)
            if vis.bottom() > area.top() and vis.top() < area.bottom():
                if ev is self.selected_event or (self.selected_event and ev.id == self.selected_event.id):
                    p.fillRect(vis, QColor(255, 255, 255, 38))
                if bar:
                    p.fillRect(QRectF(area.left(), ey + 2 * s, 2.5 * s, h - 4 * s), QColor(ev.color))
                f = QFont(font)
                f.setStrikeOut(ev.done or ev.response == "declined")
                f.setItalic(ev.response in ("needsAction", "tentative"))
                p.setFont(f)
                dimmed = ev.done or ev.response == "declined"
                p.setOpacity(base_opacity * (0.5 if dimmed else 0.45 if ev.virtual else 1.0))
                p.setPen(QColor(self.item_color(ev)))
                p.drawText(tr, flags, text)
                p.setOpacity(base_opacity)
                self._event_hits.append((vis.intersected(area), ev))
                if ev.task_list:  # ☐ 누르는 자리(1-1). 예정(virtual)을 누르면 지금 차례 할 일이 완료된다
                    cx = area.left() + bar + fm.horizontalAdvance(prefix)
                    self._check_hits.append((QRectF(cx - 2, ey, fm.horizontalAdvance("☐ ") + 4, lh), ev))
            ey += h + gap
        if hidden:
            p.setFont(make_font(self.cfg["item_font"], 0.8, bold=False))
            p.setPen(QColor(255, 255, 255, 170))
            p.drawText(QRectF(area.left(), ey, area.width(), lh), Qt.AlignLeft | Qt.AlignTop, f"…외 {hidden}개")
            ey += lh
        p.restore()
        total = ey + offset - area.top()
        if total > area.height() + 1:
            self._overflow.add(d)
            if d == self._hover_day:  # 자동 숨김 스크롤바
                ratio = area.height() / total
                bh = max(10 * s, area.height() * ratio)
                by = area.top() + (area.height() - bh) * (offset / max(1, total - area.height()))
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(255, 255, 255, 90))
                p.drawRoundedRect(QRectF(cell.right() - 4 * s, by, 3 * s, bh), 1.5 * s, 1.5 * s)
            elif offset < total - area.height():
                p.setPen(QColor(255, 255, 255, 120))
                p.setFont(make_font(self.cfg["item_font"], 0.7, bold=False))
                p.drawText(QRectF(cell.left(), cell.bottom() - lh * 0.8, cell.width() - 3 * s, lh * 0.8),
                           Qt.AlignRight | Qt.AlignVCenter, "▼")

    def _paint_bars(self, p, L, bars, bar_h, date_font):
        s = L["s"]
        font = make_font(self.cfg["item_font"], 0.9)
        fm = QFontMetrics(font)
        top_off = 3 * s - 1 + QFontMetrics(date_font).height() + 2 * s
        for r, (_n, placed) in bars.items():
            for ev, i0, i1, lane in placed:
                c0, c1 = self._cell_rect(L, i0), self._cell_rect(L, i1)
                y = c0.top() + top_off + lane * (bar_h + 1 * s)
                if y + bar_h > c0.bottom() - 2:
                    continue  # 칸에 자리가 없으면 그리지 않는다
                rect = QRectF(c0.left() + 2 * s, y, c1.right() - c0.left() - 4 * s, bar_h)
                cont_left = ev.first_day < L["first"] + timedelta(days=i0)
                cont_right = ev.days()[-1] > L["first"] + timedelta(days=i1)
                dimmed = ev.done or ev.response == "declined"
                p.setOpacity(0.5 if dimmed else 1.0)
                fill = QColor(ev.text_color or ev.color)
                fill.setAlphaF(0.78)
                p.setPen(Qt.NoPen)
                p.setBrush(fill)
                p.drawRoundedRect(rect, 3 * s, 3 * s)
                if self.selected_event and ev.id == self.selected_event.id:
                    p.setPen(QPen(QColor("#ffffff"), 1.5))
                    p.setBrush(Qt.NoBrush)
                    p.drawRoundedRect(rect, 3 * s, 3 * s)
                f = QFont(font)
                f.setStrikeOut(dimmed)
                p.setFont(f)
                p.setPen(QColor("#ffffff"))
                text = ("◀ " if cont_left else "") + self.item_text(ev, ev.first_day) + (" ▶" if cont_right else "")
                p.drawText(rect.adjusted(4 * s, 0, -4 * s, 0), Qt.AlignVCenter | Qt.AlignLeft,
                           fm.elidedText(text, Qt.ElideRight, int(rect.width() - 8 * s)))
                p.setOpacity(1.0)
                self._event_hits.append((rect, ev))

    def _paint_drag(self, p, L):
        if not (self._drag and self._drag.get("active")):
            return
        pos, ev = self._drag["cur"], self._drag["ev"]
        font = make_font(self.cfg["item_font"], 0.9)
        fm = QFontMetrics(font)
        text = ev.title
        w = min(fm.horizontalAdvance(text) + 16, 220)
        r = QRectF(pos.x() + 10, pos.y() + 8, w, fm.height() + 6)
        p.setPen(QPen(QColor(255, 255, 255, 160), 1))
        p.setBrush(QColor(30, 30, 30, 220))
        p.drawRoundedRect(r, 4, 4)
        p.setFont(font)
        p.setPen(QColor(self.item_color(ev)))
        p.drawText(r.adjusted(8, 0, -8, 0), Qt.AlignVCenter, fm.elidedText(text, Qt.ElideRight, int(w - 16)))

    def _paint_month_lines(self, p, L):
        """월 간격: 달이 바뀌는 경계에 선을 그어 구분한다."""
        p.setPen(QPen(shade(self.cfg["title_button_color"], 1.0, 0.75), 2 * L["s"]))
        n = L["rows"] * L["cols"]
        for i in range(n):
            d = L["first"] + timedelta(days=i)
            r = self._cell_rect(L, i)
            nxt = d + timedelta(days=1)
            if i % L["cols"] != L["cols"] - 1 and nxt.month != d.month:
                p.drawLine(QPointF(r.right(), r.top()), QPointF(r.right(), r.bottom()))
            below = d + timedelta(days=L["cols"])
            if i + L["cols"] < n and below.month != d.month:
                p.drawLine(QPointF(r.left(), r.bottom()), QPointF(r.right(), r.bottom()))

    def _mood_font(self):
        return make_font(self.cfg["item_font"], 0.8, bold=False)

    def mood_tip(self, d):
        tracker = active_tracker(self.cfg)
        color = self.moods.get(tracker["id"], d) if tracker and self.moods else ""
        label = color_label(tracker, color) if color else ""
        text = f"{d.month}월 {d.day}일 ({weekday_name(d.weekday(), self.cfg, 'short')})"
        return f"{text} · {label or '색 칠함'}" if color else f"{text} — 눌러서 색 칠하기"

    def _bar_items(self):
        out = []
        for ev in self.countdowns:
            parts = ev.countdown_parts()
            if parts and parts[1] in ("bar", "both") and self.visible_calendar(ev.calendar_id):
                out.append(ev)
        return out

    def countdown_text(self, ev):
        kind, _show, fmt = ev.countdown_parts()
        days = (ev.first_day - date.today()).days
        if fmt == "passed" or days < 0:
            when = f"{abs(days)}일 경과" if days < 0 else "오늘"
        else:
            when = "오늘" if days == 0 else f"{days}일 남음"
        return f"[{COUNTDOWN_TYPES.get(kind, '카운트다운')}] {ev.title} {when}"

    def _paint_countdown_bar(self, p, L):
        s, m = L["s"], L["m"]
        r = QRectF(m, m + L["H"] - L["cd_h"], L["W"], L["cd_h"])
        p.fillRect(r, self._vgrad(r, self.cfg["cell_bg"], 0.5, 0.38))
        font = make_font(self.cfg["item_font"], 0.85)
        p.setFont(font)
        p.setPen(QColor(self.cfg["item_font"]["color"]))
        texts = [self.countdown_text(ev) for ev in self._bar_items()]
        p.drawText(r.adjusted(8 * s, 0, -8 * s, 0), Qt.AlignVCenter | Qt.AlignLeft,
                   QFontMetrics(font).elidedText("     ".join(texts), Qt.ElideRight, int(r.width() - 16 * s)))

    def button_tip(self, key):
        if key == "cloud":
            text = {"login": "로그인 후, 데이터를 동기화하고 백업할 수 있습니다",
                    "syncing": "데이터를 동기화중...",
                    "error": "동기화 실패 — 클릭해서 다시 시도",
                    }.get(self.cloud_state, "구글 캘린더와 동기화됨 — 클릭해서 지금 동기화")
            extra = self.sync_info() if self.sync_info and self.cloud_state != "login" else ""
            return text + (f"\n{extra}" if extra else "")
        return {"plugins": "플러그인 (표시할 캘린더)", "mobile": "캘린더 목록 모바일 버전", "account": "계정 센터",
                "today": "오늘로 돌아가기", "prev": "이전 페이지", "next": "다음 페이지",
                "menu": "추가 옵션", "done": "완전한 위치및 크기 조정"}.get(key, "")

    def _draw_icon(self, p, key, r, s):
        p.save()
        dim = key == "cloud" and self.cloud_state in ("login", "syncing")
        col = QColor(self.cfg["title_button_color"])
        col.setAlpha(120 if dim else 230)
        pen = QPen(col, 1.5 * s, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        c = r.center()
        k = 6 * s

        def union(*shapes):
            result = QPainterPath()
            for kind, rect in shapes:
                part = QPainterPath()
                (part.addRect if kind == "rect" else part.addEllipse)(rect)
                result = result.united(part)
            return result

        if key == "plugins":  # 퍼즐 조각
            w, kn = k * 1.3, k * 0.33
            x0, y0 = c.x() - w / 2 - kn * 0.5, c.y() - w / 2 + kn * 0.5
            p.drawPath(union(("rect", QRectF(x0, y0, w, w)),
                             ("ellipse", QRectF(x0 + w / 2 - kn, y0 - kn * 1.5, 2 * kn, 2 * kn)),
                             ("ellipse", QRectF(x0 + w - kn * 0.5, y0 + w / 2 - kn, 2 * kn, 2 * kn))))
        elif key == "mobile":  # 휴대폰
            p.drawRoundedRect(QRectF(c.x() - k * 0.6, c.y() - k, k * 1.2, k * 2), 2 * s, 2 * s)
            p.drawLine(QPointF(c.x() - k * 0.2, c.y() + k * 0.65), QPointF(c.x() + k * 0.2, c.y() + k * 0.65))
        elif key == "cloud":  # 구름 + 상태 표시
            p.drawPath(union(("ellipse", QRectF(c.x() - k * 1.05, c.y() - k * 0.25, k * 0.95, k * 0.95)),
                             ("ellipse", QRectF(c.x() - k * 0.55, c.y() - k * 0.85, k * 1.25, k * 1.25)),
                             ("ellipse", QRectF(c.x() + k * 0.2, c.y() - k * 0.3, k * 0.9, k * 0.9)),
                             ("rect", QRectF(c.x() - k * 0.58, c.y() + k * 0.1, k * 1.25, k * 0.6))))
            mark = QPen(pen)
            mark.setWidthF(1.3 * s)
            if self.cloud_state == "ok":
                mark.setColor(QColor("#8be28b"))
                p.setPen(mark)
                p.drawPolyline([QPointF(c.x() - k * 0.35, c.y() + k * 0.15), QPointF(c.x() - k * 0.05, c.y() + k * 0.42),
                                QPointF(c.x() + k * 0.4, c.y() - k * 0.15)])
            elif self.cloud_state == "error":
                mark.setColor(QColor(C_WARN))
                p.setPen(mark)
                p.drawLine(QPointF(c.x(), c.y() - k * 0.35), QPointF(c.x(), c.y() + k * 0.15))
                p.drawPoint(QPointF(c.x(), c.y() + k * 0.42))
        elif key == "account":  # 사람
            p.drawEllipse(QPointF(c.x(), c.y() - k * 0.4), k * 0.42, k * 0.42)
            p.drawArc(QRectF(c.x() - k * 0.85, c.y() + k * 0.2, k * 1.7, k * 1.5), 0, 180 * 16)
        elif key in ("prev", "next"):
            sgn = -1 if key == "prev" else 1
            p.drawLine(QPointF(c.x() - k * sgn, c.y()), QPointF(c.x() + k * sgn, c.y()))
            p.drawLine(QPointF(c.x() + k * sgn, c.y()), QPointF(c.x() + k * sgn * 0.2, c.y() - k * 0.8))
            p.drawLine(QPointF(c.x() + k * sgn, c.y()), QPointF(c.x() + k * sgn * 0.2, c.y() + k * 0.8))
        elif key == "today":  # 집(오늘로 돌아가기)
            path = QPainterPath()
            path.moveTo(c.x() - k, c.y() - k * 0.05)
            path.lineTo(c.x(), c.y() - k * 0.95)
            path.lineTo(c.x() + k, c.y() - k * 0.05)
            p.drawPath(path)
            p.drawPolyline([QPointF(c.x() - k * 0.7, c.y() - k * 0.3), QPointF(c.x() - k * 0.7, c.y() + k * 0.85),
                            QPointF(c.x() + k * 0.7, c.y() + k * 0.85), QPointF(c.x() + k * 0.7, c.y() - k * 0.3)])
        elif key == "menu":
            for dy in (-k * 0.7, 0, k * 0.7):
                p.drawLine(QPointF(c.x() - k, c.y() + dy), QPointF(c.x() + k, c.y() + dy))
        elif key == "done":
            p.setFont(make_font(self.cfg["date_font"], bold=True))
            p.setPen(QColor(C_WARN))
            p.drawText(r, Qt.AlignCenter, "✓ 완료")
        p.restore()

    # ---------- 조정 모드 ----------
    def set_adjusting(self, on):
        self.adjusting = on
        self.setCursor(Qt.ArrowCursor)
        if self.isVisible():  # 조정하는 동안에는 클릭 통과를 끈다(창을 직접 끌어야 하므로)
            desktop.set_click_through(int(self.winId()), self.click_through and not on)
        self.update()

    def _edges_at(self, pos):
        e = Qt.Edges()
        if pos.x() < EDGE:
            e |= Qt.LeftEdge
        elif pos.x() > self.width() - EDGE:
            e |= Qt.RightEdge
        if pos.y() < EDGE:
            e |= Qt.TopEdge
        elif pos.y() > self.height() - EDGE:
            e |= Qt.BottomEdge
        return e

    @staticmethod
    def _edge_cursor(e):
        if e in (Qt.LeftEdge | Qt.TopEdge, Qt.RightEdge | Qt.BottomEdge):
            return Qt.SizeFDiagCursor
        if e in (Qt.RightEdge | Qt.TopEdge, Qt.LeftEdge | Qt.BottomEdge):
            return Qt.SizeBDiagCursor
        if e & (Qt.LeftEdge | Qt.RightEdge):
            return Qt.SizeHorCursor
        if e & (Qt.TopEdge | Qt.BottomEdge):
            return Qt.SizeVerCursor
        return Qt.ArrowCursor

    def _in_header(self, pos):
        L = self._layout()
        return L["m"] <= pos.y() < L["m"] + L["header_h"]

    # ---------- 마우스 ----------
    # Qt 마우스 이벤트와 '아이콘 클릭 통과' 모드의 마우스 훅이 같은 처리 함수를 쓴다.
    def mousePressEvent(self, e):
        self.on_press(e.position(), e.globalPosition().toPoint(), e.button())

    def mouseMoveEvent(self, e):
        self.on_move(e.position(), e.globalPosition().toPoint(), bool(e.buttons() & Qt.LeftButton))

    def mouseReleaseEvent(self, e):
        self.on_release(e.position(), e.button())

    def mouseDoubleClickEvent(self, e):
        # Qt가 준 더블클릭도 '두 번째 누름'으로 보고 on_press에서 직접 판정한다(한 번만 처리되게)
        self.on_press(e.position(), e.globalPosition().toPoint(), e.button())

    def _is_second_click(self, pos):
        """바로 전 클릭과 시간·거리가 가까우면 더블클릭으로 본다."""
        now = time.monotonic()
        t0, p0 = self._last_click
        interval = QGuiApplication.styleHints().mouseDoubleClickInterval() / 1000
        if t0 and now - t0 <= interval and (pos - p0).manhattanLength() <= 8:
            self._last_click = (0.0, QPointF())
            return True
        self._last_click = (now, QPointF(pos))
        return False

    def wheelEvent(self, e):
        self.on_wheel(e.position(), e.angleDelta().y())

    def leaveEvent(self, _):
        self._hover_btn = self._hover_day = self._tip = None
        self.update()

    def on_move(self, pos, gpos, left_down=False):
        if self.adjusting:
            edges = self._edges_at(pos)
            in_header = self._in_header(pos) and not self._button_at(pos)
            self.setCursor(self._edge_cursor(edges) if edges else (Qt.SizeAllCursor if in_header else Qt.ArrowCursor))
        if self._drag and left_down:
            if not self._drag["active"] and (pos - self._drag["pos"]).manhattanLength() > DRAG_START:
                self._drag["active"] = True
                QToolTip.hideText()
                if not self.click_through:
                    self.setCursor(Qt.ClosedHandCursor)
            if self._drag["active"]:
                self._drag["cur"] = pos
                self._drag["target"] = self._day_at(pos)
                self.update()
                return
        btn, day = self._button_at(pos), self._day_at(pos)
        if (btn, day) != (self._hover_btn, self._hover_day):
            self._hover_btn, self._hover_day = btn, day
            self.update()
        ev = None if self.adjusting else self._event_at(pos)
        mood = None if self.adjusting or ev or btn else self.mood.hit(pos)
        mood_day = mood[1] if mood and mood[0] == "box" else None
        tip = ev or (("btn", btn) if btn else None) or (("mood", mood_day) if mood_day else None)
        if tip != self._tip:
            self._tip = tip
            if ev:
                QToolTip.showText(gpos, tooltip_text(ev, self.store, self.cfg), self)
            elif mood_day:
                QToolTip.showText(gpos, self.mood_tip(mood_day), self)
            elif btn:
                QToolTip.showText(gpos, self.button_tip(btn), self)
            else:
                QToolTip.hideText()

    def on_press(self, pos, gpos, button):
        if not self.adjusting:
            self.activate_keys()
        mood = None if self.adjusting else self.mood.hit(pos)
        if mood:  # 트래커: 이름을 누르면 바꾸고, 칸을 누르면(왼쪽·오른쪽 모두) 색 고르기
            if mood[0] == "tab" and button == Qt.LeftButton:
                self.moodTrackerChosen.emit(mood[1])
            elif mood[0] == "box":
                self.moodMenuRequested.emit(mood[1], gpos)
            return
        if button == Qt.RightButton and not self.adjusting:
            ev, d = self._event_at(pos), self._day_at(pos)
            if ev:
                self.select(ev, d)
                self.eventMenuRequested.emit(ev, d, gpos)
            else:
                self.select(None, d)
                self.dayMenuRequested.emit(d, gpos)
            return
        if button != Qt.LeftButton:
            return
        btn = self._button_at(pos)
        if not self.adjusting and not btn and not self._in_header(pos) and self._is_second_click(pos):
            self.on_double(pos, button)  # 더블클릭은 직접 판정한다(첫 클릭의 창 활성화가 Qt 판정을 흐트러뜨림)
            return
        if self.adjusting:
            edges = self._edges_at(pos)
            if btn == "done":
                self.adjustFinished.emit()
            elif edges:
                self.windowHandle().startSystemResize(edges)
            elif self._in_header(pos):
                self.windowHandle().startSystemMove()
            return
        below = self.mapToGlobal(dict(self._buttons())[btn].bottomLeft().toPoint()) if btn else None
        if btn == "prev":
            self.shift_page(-1)
        elif btn == "next":
            self.shift_page(1)
        elif btn == "today":
            self.go_today()
        elif btn == "plugins":
            self.pluginsRequested.emit(below)
        elif btn == "mobile":
            self.mobileRequested.emit()
        elif btn == "cloud":
            self.cloudClicked.emit()
        elif btn == "account":
            self.accountRequested.emit(below)
        elif btn == "menu":
            self.menuRequested.emit(below)
        elif self._in_header(pos):
            if self.status_clickable:
                self.statusClicked.emit()
        elif self._check_at(pos):
            self.taskToggleRequested.emit(self._check_at(pos))
        else:
            ev, d = self._event_at(pos), self._day_at(pos)
            self.select(ev, d)
            if ev and not ev.virtual and (ev.writable or ev.task_list) and not ev.id.startswith("tmp-"):
                self._drag = {"ev": ev, "pos": pos, "cur": pos, "day": d, "active": False, "target": d}
            elif not ev and self._date_label_at(pos):
                self._pending_day = self._date_label_at(pos)

    def on_release(self, pos, button):
        if button != Qt.LeftButton:
            return
        drag, self._drag = self._drag, None
        if drag and drag["active"]:
            self.setCursor(Qt.ArrowCursor)
            target = self._day_at(pos)
            if target and drag["day"] and target != drag["day"]:
                self.eventMoveRequested.emit(drag["ev"], (target - drag["day"]).days)
            self.update()
            return
        if self._pending_day:  # 날짜 숫자 한 번 클릭 → 하루 상세 보기(더블클릭이면 취소)
            self._dayview_timer.start(QGuiApplication.styleHints().mouseDoubleClickInterval())

    def _fire_dayview(self):
        d, self._pending_day = self._pending_day, None
        if d:
            self.dayViewRequested.emit(d)

    def _cancel_drag(self):
        if self._drag:
            self._drag = None
            self.setCursor(Qt.ArrowCursor)
            self.update()

    def on_double(self, pos, button):
        self._dayview_timer.stop()
        self._pending_day = None
        if button != Qt.LeftButton or self.adjusting:
            return
        if self._button_at(pos) or self._in_header(pos) or self._check_at(pos):
            return
        d = self._day_at(pos)
        if d:
            self.cellEditRequested.emit(d, self.cell_global_rect(d))

    def on_wheel(self, pos, dy):
        if self.adjusting or not dy:
            return
        d = self._day_at(pos)
        if d in self._overflow or self._scroll.get(d):  # 셀 내용이 넘치면 그 셀만 스크롤
            step = QFontMetrics(make_font(self.cfg["item_font"])).height()
            self._scroll[d] = max(0, self._scroll.get(d, 0) + (-step if dy > 0 else step))
            self.update()
        elif self.cfg["wheel_paging"]:
            self.shift_weeks(-1 if dy > 0 else 1)

    def select(self, ev, d):
        self.selected_event, self.selected_day = ev, d
        self.update()


def tooltip_text(ev, store, cfg=None):
    cfg = cfg or {}

    def t(dt):
        return fmt_time(dt, cfg) if cfg else f"{dt:%H:%M}"
    if ev.virtual:
        when = f"{ev.start:%m/%d} 예정 · 반복 할 일 (누르면 지금 차례 할 일을 엽니다)"
    elif ev.task_list:
        when = f"{ev.start:%m/%d} 마감 · {'완료한 할 일' if ev.done else '할 일'} — ☐를 누르면 완료"
    elif ev.all_day:
        last = ev.end - timedelta(days=1)
        when = f"{ev.start:%m/%d}" + (f" ~ {last:%m/%d}" if last != ev.start else "") + " (하루 종일)"
    elif ev.start.date() == ev.end.date():
        when = f"{ev.start:%m/%d} {t(ev.start)} ~ {t(ev.end)}"
    else:
        when = f"{ev.start:%m/%d} {t(ev.start)} ~ {ev.end:%m/%d} {t(ev.end)}"
    cal = store.calendar(ev.calendar_id)
    lines = [f"<b>{'✓ ' if ev.done else ''}{_esc(ev.title)}</b>", when]
    if ev.response:
        state = {"needsAction": "응답 필요", "accepted": "참석", "tentative": "미정", "declined": "거절"}
        lines.append(f"✉ 초대 · {state.get(ev.response, ev.response)}" + (f" · 주최: {_esc(ev.organizer)}" if ev.organizer else ""))
    if ev.recurring:
        lines.append("↻ 반복 일정")
    a = ev.alarm_parts()
    if a:
        adv = ", ".join("당일" if x == 0 else f"{x}일 전" for x in a[2])
        lines.append(f"⏰ {a[0]:02d}:{a[1]:02d} ({adv})")
    if ev.location:
        lines.append("📍 " + _esc(ev.location))
    if cal:
        lines.append(f"<span style='color:{cal['color']}'>●</span> {_esc(cal['summary'])}")
    if ev.description:
        desc = ev.description if len(ev.description) < 300 else ev.description[:300] + "…"
        lines.append("<br>" + _esc(desc).replace("\n", "<br>"))
    if ev.writable and not ev.virtual:
        lines.append("<span style='color:gray'>끌어서 다른 날로 옮기기 · 우클릭으로 복사</span>")
    return "<br>".join(lines)


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
