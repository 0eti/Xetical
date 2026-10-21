"""일정 편집 / 모바일 / 날짜 선택 대화상자. 모두 부모 없는 최상위 창이어야 한다
(달력 창의 자식이 되면 함께 바탕화면 아래에 깔려 버린다)."""
import calendar as pycal
import webbrowser
from datetime import date, datetime, time, timedelta, timezone

from PySide6.QtCore import QDate, Qt, QTime
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QCalendarWidget, QCheckBox, QComboBox, QDateEdit, QDialog,
                               QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QTimeEdit, QVBoxLayout,
                               QWidget)

from .gcal import is_holiday_calendar
from .model import COUNTDOWN_TYPES, KEEP, NO_REPEAT
from .widgets import ColorButton, confirm

WD_CODES = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
WD_NAMES = "월화수목금토일"


def bring_to_front(w):
    w.show()
    w.raise_()
    w.activateWindow()


def color_icon(hex_color):
    pm = QPixmap(12, 12)
    pm.fill(QColor(hex_color))
    return QIcon(pm)


def to_qdate(d):
    return QDate(d.year, d.month, d.day)


def from_qdate(q):
    return date(q.year(), q.month(), q.day())


def ask_scope(parent, verb, allow_one=True):
    """반복 일정 수정/삭제 범위. 'one' | 'following' | 'all' | None(취소)"""
    box = QMessageBox(QMessageBox.Question, "반복 일정", f"반복 일정을 {verb}합니다. 어느 범위에 적용할까요?", parent=parent)
    box.setWindowFlag(Qt.WindowStaysOnTopHint)
    btns = {}
    if allow_one:
        btns[box.addButton("이번 일정만", QMessageBox.AcceptRole)] = "one"
    btns[box.addButton("이번 및 이후 일정", QMessageBox.AcceptRole)] = "following"
    btns[box.addButton("모든 일정", QMessageBox.AcceptRole)] = "all"
    box.addButton("취소", QMessageBox.RejectRole)
    box.exec()
    return btns.get(box.clickedButton())


# ---------------------------------------------------------------- 반복
REPEAT_KINDS = [
    ("none", "안 함"), ("daily", "매일"), ("workday", "근무일 (월~금)"), ("weekly", "매주"),
    ("monthly", "매월"), ("month_last", "매월 말일"), ("quarterly", "분기마다"), ("yearly", "매년"),
    ("c_day", "맞춤: N일마다"), ("c_week", "맞춤: N주마다 (요일 선택)"), ("c_month", "맞춤: N개월마다 (날짜)"),
    ("c_month_wd", "맞춤: N개월마다 (N번째 요일)"), ("c_year", "맞춤: N년마다"),
]


def parse_rrule(rule):
    if not rule:
        return {}
    out = {}
    for part in rule.split(":", 1)[-1].split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.upper()] = v
    return out


def rule_dates(rule, start, until):
    """RRULE의 회차 날짜(start 포함, until 전까지). 저장하자마자 다음 회차들을 화면에 보여 주는 데 쓴다
    (진짜 회차는 동기화 때 구글에서 받는다). 이 창이 만드는 규칙 모양만 다룬다: DAILY·WEEKLY(BYDAY)·
    MONTHLY(BYMONTHDAY, -1, N번째 요일)·YEARLY, INTERVAL·COUNT·UNTIL."""
    p = parse_rrule(rule)
    freq, n = p.get("FREQ"), int(p.get("INTERVAL", 1) or 1)
    if freq not in ("DAILY", "WEEKLY", "MONTHLY", "YEARLY"):
        return [start]
    if "UNTIL" in p:
        u = p["UNTIL"].upper()
        last = (datetime.strptime(u[:15], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc).astimezone().date()
                if u.endswith("Z") else date(int(u[:4]), int(u[4:6]), int(u[6:8])))
        until = min(until, last + timedelta(days=1))
    count = int(p["COUNT"]) if p.get("COUNT", "").isdigit() else None
    days = [WD_CODES.index(x[-2:]) for x in p.get("BYDAY", "").split(",") if x[-2:] in WD_CODES]
    nth = [int(x[:-2]) for x in p.get("BYDAY", "").split(",") if x[:-2].lstrip("-").isdigit()]
    mdays = [int(x) for x in p.get("BYMONTHDAY", "").split(",") if x.lstrip("-").isdigit()]

    def hit(d):
        months = (d.year - start.year) * 12 + d.month - start.month
        last_day = pycal.monthrange(d.year, d.month)[1]
        if freq == "DAILY":
            return (d - start).days % n == 0
        if freq == "WEEKLY":
            weeks = ((d - timedelta(days=d.weekday())) - (start - timedelta(days=start.weekday()))).days // 7
            return weeks % n == 0 and d.weekday() in (days or [start.weekday()])
        if freq == "MONTHLY":
            if months % n:
                return False
            if nth:  # 예: 2MO(둘째 월요일), -1FR(마지막 금요일)
                k = (d.day - 1) // 7 + 1 if nth[0] > 0 else -((last_day - d.day) // 7 + 1)
                return d.weekday() in days and k == nth[0]
            return any(d.day == (m if m > 0 else last_day + m + 1) for m in (mdays or [start.day]))
        return (d.year - start.year) % n == 0 and (d.month, d.day) == (start.month, start.day)

    out, d = [], start
    while d < until and (count is None or len(out) < count):
        if d == start or hit(d):
            out.append(d)
        d += timedelta(days=1)
    return out


class RepeatEditor(QGroupBox):
    def __init__(self):
        super().__init__("반복")
        self.setCheckable(False)
        self.kind = QComboBox()
        for key, label in REPEAT_KINDS:
            self.kind.addItem(label, key)
        self.interval = QSpinBox(minimum=1, maximum=99, value=1)
        self.days = []
        day_row = QHBoxLayout()
        for i, n in enumerate(WD_NAMES):
            cb = QCheckBox(n)
            self.days.append(cb)
            day_row.addWidget(cb)
        self.day_box = QWidget()
        self.day_box.setLayout(day_row)
        day_row.setContentsMargins(0, 0, 0, 0)
        self.nth = QComboBox()
        for v, label in ((1, "첫째"), (2, "둘째"), (3, "셋째"), (4, "넷째"), (-1, "마지막")):
            self.nth.addItem(label, v)
        self.end_kind = QComboBox()
        self.end_kind.addItem("영구", "never")
        self.end_kind.addItem("횟수 지정", "count")
        self.end_kind.addItem("종료일 지정", "until")
        self.count = QSpinBox(minimum=1, maximum=999, value=10, suffix=" 회")
        self.until = QDateEdit(calendarPopup=True)
        self.until.setDisplayFormat("yyyy-MM-dd")
        self.raw = ""

        def boxed(*widgets):
            box = QWidget()
            row = QHBoxLayout(box)
            row.setContentsMargins(0, 0, 0, 0)
            for w in widgets:
                row.addWidget(w)
            row.addStretch()
            return box

        self.interval_label = QLabel("")
        self.interval_box = boxed(self.interval, self.interval_label)
        self.end_box = boxed(self.end_kind, self.count, self.until)
        form = QFormLayout(self)
        form.addRow("반복", self.kind)
        form.addRow("간격", self.interval_box)
        form.addRow("요일", self.day_box)
        form.addRow("몇째 주", self.nth)
        form.addRow("종료", self.end_box)
        self.form = form
        self.kind.currentIndexChanged.connect(self._sync)
        self.end_kind.currentIndexChanged.connect(self._sync)
        self._sync()
        self.loaded, self.dirty = "", False
        for sig in (self.kind.currentIndexChanged, self.end_kind.currentIndexChanged, self.interval.valueChanged,
                    self.nth.currentIndexChanged, self.count.valueChanged, self.until.dateChanged):
            sig.connect(self._touch)
        for cb in self.days:
            cb.toggled.connect(self._touch)

    def _touch(self, *_):
        if not getattr(self, "_loading", False):
            self.dirty = True

    def _row_visible(self, field, on):
        label = self.form.labelForField(field)
        if label:
            label.setVisible(on)
        field.setVisible(on)

    def _sync(self):
        k = self.kind.currentData()
        self._row_visible(self.interval_box, k.startswith("c_"))
        self.interval_label.setText({"c_day": "일마다", "c_week": "주마다", "c_month": "개월마다",
                                     "c_month_wd": "개월마다", "c_year": "년마다"}.get(k, ""))
        self._row_visible(self.day_box, k == "c_week")
        self._row_visible(self.nth, k == "c_month_wd")
        on = k not in ("none", "keep")
        self._row_visible(self.end_box, on)
        self.count.setVisible(self.end_kind.currentData() == "count")
        self.until.setVisible(self.end_kind.currentData() == "until")

    def load(self, rule, start):
        self._loading = True
        try:
            self._load(rule, start)
        finally:
            self._loading = False
        self.loaded, self.dirty = rule or "", False

    def _load(self, rule, start):
        self.until.setDate(to_qdate(start + timedelta(days=90)))
        self.days[start.weekday()].setChecked(True)
        p = parse_rrule(rule)
        if not p:
            self.kind.setCurrentIndex(0)
            return
        freq, interval = p.get("FREQ", ""), int(p.get("INTERVAL", "1") or 1)
        byday = [x for x in p.get("BYDAY", "").split(",") if x]
        key = None
        if freq == "DAILY" and not byday:
            key = "daily" if interval == 1 else "c_day"
        elif freq == "WEEKLY":
            if interval == 1 and sorted(byday) == sorted(WD_CODES[:5]):
                key = "workday"
            elif interval == 1 and (not byday or byday == [WD_CODES[start.weekday()]]):
                key = "weekly"
            elif all(b in WD_CODES for b in byday):
                key = "c_week"
                for i, cb in enumerate(self.days):
                    cb.setChecked(WD_CODES[i] in byday or (not byday and i == start.weekday()))
        elif freq == "MONTHLY":
            if p.get("BYMONTHDAY") == "-1" and interval == 1:
                key = "month_last"
            elif byday and len(byday) == 1 and byday[0][-2:] in WD_CODES:
                key = "c_month_wd"
                n = byday[0][:-2]
                idx = self.nth.findData(int(n) if n.lstrip("-").isdigit() else 1)
                self.nth.setCurrentIndex(max(0, idx))
            elif interval == 3 and not p.get("BYMONTHDAY"):
                key = "quarterly"
            elif interval == 1:
                key = "monthly"
            else:
                key = "c_month"
        elif freq == "YEARLY":
            key = "yearly" if interval == 1 else "c_year"
        if key is None:  # 해석할 수 없는 규칙은 그대로 유지
            self.raw = rule
            self.kind.addItem(f"기존 규칙 유지 ({rule[6:][:40]})", "keep")
            key = "keep"
        self.kind.setCurrentIndex(self.kind.findData(key))
        self.interval.setValue(interval)
        if "COUNT" in p:
            self.end_kind.setCurrentIndex(1)
            self.count.setValue(int(p["COUNT"]))
        elif "UNTIL" in p:
            self.end_kind.setCurrentIndex(2)
            u = p["UNTIL"]
            if u.upper().endswith("Z") and len(u) >= 16:  # UTC 시각 → 이 PC 날짜
                d = datetime.strptime(u[:15].upper(), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc).astimezone()
                self.until.setDate(QDate(d.year, d.month, d.day))
            else:
                self.until.setDate(QDate(int(u[:4]), int(u[4:6]), int(u[6:8])))

    def rrule(self, start, all_day):
        """KEEP = 손대지 않았으니 규칙 그대로, NO_REPEAT = 반복 안 함, "RRULE:…" = 새 규칙."""
        if not self.dirty:
            return KEEP
        k = self.kind.currentData()
        if k == "none":
            return NO_REPEAT
        if k == "keep":
            return self.raw
        wd = WD_CODES[start.weekday()]
        n = self.interval.value()
        parts = {
            "daily": "FREQ=DAILY", "workday": "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
            "weekly": f"FREQ=WEEKLY;BYDAY={wd}", "monthly": f"FREQ=MONTHLY;BYMONTHDAY={start.day}",
            "month_last": "FREQ=MONTHLY;BYMONTHDAY=-1", "quarterly": f"FREQ=MONTHLY;INTERVAL=3;BYMONTHDAY={start.day}",
            "yearly": "FREQ=YEARLY", "c_day": f"FREQ=DAILY;INTERVAL={n}",
            "c_week": f"FREQ=WEEKLY;INTERVAL={n};BYDAY=" + (",".join(
                WD_CODES[i] for i, cb in enumerate(self.days) if cb.isChecked()) or wd),
            "c_month": f"FREQ=MONTHLY;INTERVAL={n};BYMONTHDAY={start.day}",
            "c_month_wd": f"FREQ=MONTHLY;INTERVAL={n};BYDAY={self.nth.currentData()}{wd}",
            "c_year": f"FREQ=YEARLY;INTERVAL={n}",
        }[k]
        end = self.end_kind.currentData()
        if end == "count":
            parts += f";COUNT={self.count.value()}"
        elif end == "until":
            u = from_qdate(self.until.date())
            if all_day:
                parts += f";UNTIL={u:%Y%m%d}"
            else:  # 시간 일정의 UNTIL은 UTC다: 그날 밤 23:59:59(이 PC 시각)를 UTC로 바꿔 적는다
                end = datetime.combine(u, time(23, 59, 59)).astimezone(timezone.utc)
                parts += f";UNTIL={end:%Y%m%dT%H%M%SZ}"
        return "RRULE:" + parts


# ---------------------------------------------------------------- 알림 / 카운트다운
ADVANCE = [(0, "당일"), (1, "1일 전"), (2, "2일 전"), (3, "3일 전"), (5, "5일 전"), (7, "1주 전")]


class AlarmEditor(QGroupBox):
    def __init__(self):
        super().__init__("알림")
        self.setCheckable(True)
        self.setChecked(False)
        self.time = QTimeEdit()
        self.time.setDisplayFormat("HH:mm")
        now = datetime.now() + timedelta(minutes=15)
        self.time.setTime(QTime(now.hour, (now.minute // 15) * 15))
        self.checks = []
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("알림 시각"))
        row.addWidget(self.time)
        row.addStretch()
        lay.addLayout(row)
        row2 = QHBoxLayout()
        for days, label in ADVANCE:
            cb = QCheckBox(label)
            cb.setChecked(days == 0)
            self.checks.append((days, cb))
            row2.addWidget(cb)
        lay.addLayout(row2)
        row3 = QHBoxLayout()
        self.custom_on = QCheckBox("사용자 지정")
        self.custom = QSpinBox(minimum=1, maximum=365, value=10, suffix=" 일 전")
        row3.addWidget(self.custom_on)
        row3.addWidget(self.custom)
        row3.addStretch()
        lay.addLayout(row3)

    def load(self, ev):
        a = ev.alarm_parts() if ev else None
        if not a:
            return
        self.setChecked(True)
        self.time.setTime(QTime(a[0], a[1]))
        for days, cb in self.checks:
            cb.setChecked(days in a[2])
        extra = [d for d in a[2] if d not in dict(ADVANCE)]
        if extra:
            self.custom_on.setChecked(True)
            self.custom.setValue(extra[0])

    def value(self):
        if not self.isChecked():
            return ""
        days = [d for d, cb in self.checks if cb.isChecked()]
        if self.custom_on.isChecked():
            days.append(self.custom.value())
        t = self.time.time()
        return f"{t.hour():02d}:{t.minute():02d}|{','.join(str(d) for d in sorted(set(days)) or [0])}"


class CountdownEditor(QGroupBox):
    def __init__(self):
        super().__init__("카운트다운")
        self.setCheckable(True)
        self.setChecked(False)
        self.kind = QComboBox()
        for k, label in COUNTDOWN_TYPES.items():
            self.kind.addItem(label, k)
        self.show_where = QComboBox()
        self.show_where.addItem("달력 하단 바", "bar")
        self.show_where.addItem("바탕화면 카드", "card")
        self.show_where.addItem("둘 다", "both")
        self.fmt = QComboBox()
        self.fmt.addItem("N일 남음", "left")
        self.fmt.addItem("N일 경과", "passed")
        form = QFormLayout(self)
        form.addRow("유형", self.kind)
        form.addRow("표시 위치", self.show_where)
        form.addRow("표시 형식", self.fmt)

    def load(self, ev):
        parts = ev.countdown_parts() if ev else None
        if not parts:
            return
        self.setChecked(True)
        for combo, val in ((self.kind, parts[0]), (self.show_where, parts[1]), (self.fmt, parts[2])):
            combo.setCurrentIndex(max(0, combo.findData(val)))

    def value(self):
        if not self.isChecked():
            return ""
        return f"{self.kind.currentData()}|{self.show_where.currentData()}|{self.fmt.currentData()}"


# ---------------------------------------------------------------- 일정 편집
class EventDialog(QDialog):
    """새 일정(event=None, day 지정) 또는 기존 일정 수정. focus='repeat'|'alarm'|'countdown'이면 그 항목을 켜고 시작."""

    def __init__(self, calendars, event=None, day=None, default_cal="", rrule="", focus=None, cfg=None,
                 start_time=None):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        cfg = cfg or {}
        self.setWindowTitle("일정 수정" if event else "새 일정")
        self.setMinimumWidth(460)
        self.action = None
        self.event = event
        self.orig_rrule = rrule
        writable = [c for c in calendars if c["writable"] and not is_holiday_calendar(c["id"])]
        cal = next((c for c in calendars if event and c["id"] == event.calendar_id), None)
        self.read_only = bool(event) and not (cal and cal["writable"])

        self.title = QLineEdit(event.title if event else "")
        self.title.setPlaceholderText("내용")
        self.done_box = QCheckBox("완료")
        self.done_box.setChecked(bool(event and event.done))
        self.text_color = ColorButton(event.text_color if event else "", allow_none=True)
        self.all_day = QCheckBox("하루 종일")
        self.start_date, self.end_date = QDateEdit(calendarPopup=True), QDateEdit(calendarPopup=True)
        self.start_time, self.end_time = QTimeEdit(), QTimeEdit()
        for w in (self.start_date, self.end_date):
            w.setDisplayFormat("yyyy-MM-dd (ddd)")
        for w in (self.start_time, self.end_time):
            w.setDisplayFormat("AP h:mm" if cfg.get("time_format") == "ampm" else "HH:mm")
        self.calendar = QComboBox()
        self.location = QLineEdit(event.location if event else "")
        self.description = QPlainTextEdit(event.description if event else "")
        self.description.setFixedHeight(70)

        if event:
            self.all_day.setChecked(event.all_day)
            if event.all_day:
                s, e = event.start, event.end - timedelta(days=1)
                st, et = time(9), time(10)
            else:
                s, e, st, et = event.start.date(), event.end.date(), event.start.time(), event.end.time()
        else:
            self.all_day.setChecked(start_time is None)  # 하루 상세 보기에서 시각을 골랐으면 시간 일정으로
            s = e = day or date.today()
            st = start_time or time(9)
            et = (datetime.combine(s, st) + timedelta(hours=1)).time()
        self.start_date.setDate(to_qdate(s))
        self.end_date.setDate(to_qdate(e))
        self.start_time.setTime(QTime(st.hour, st.minute))
        self.end_time.setTime(QTime(et.hour, et.minute))
        self._prev_start = s

        shown = writable if not self.read_only else [cal] if cal else []
        for c in shown:
            self.calendar.addItem(color_icon(c["color"]), c["summary"], c["id"])
        want = event.calendar_id if event else (default_cal or next((c["id"] for c in writable if c["primary"]), ""))
        self.calendar.setCurrentIndex(max(0, self.calendar.findData(want)))
        if event and event.recurring:
            self.calendar.setEnabled(False)  # 반복 일정은 캘린더 이동을 지원하지 않는다

        self.repeat = RepeatEditor()
        self.repeat.load(rrule, s)
        self.alarm = AlarmEditor()
        self.alarm.load(event)
        self.countdown = CountdownEditor()
        self.countdown.load(event)
        if focus == "repeat" and not rrule:
            self.repeat.kind.setCurrentIndex(self.repeat.kind.findData("weekly"))
        elif focus == "alarm":
            self.alarm.setChecked(True)
        if not event and cfg.get("default_alarm_on"):  # 새 일정 기본 알림(3-4)
            self.alarm.setChecked(True)
            hh, mm = (int(x) for x in cfg.get("default_alarm_time", "09:00").split(":"))
            self.alarm.time.setTime(QTime(hh, mm))
            for days, cb in self.alarm.checks:
                cb.setChecked(days in cfg.get("default_alarm_days", [0]))
        elif focus == "countdown":
            self.countdown.setChecked(True)

        form = QFormLayout()
        top = QHBoxLayout()
        top.addWidget(self.title, 1)
        top.addWidget(self.done_box)
        form.addRow("내용", top)
        crow = QHBoxLayout()
        crow.addWidget(self.text_color)
        crow.addStretch()
        crow.addWidget(self.all_day)
        form.addRow("글자색", crow)
        row_s, row_e = QHBoxLayout(), QHBoxLayout()
        row_s.addWidget(self.start_date, 1)
        row_s.addWidget(self.start_time)
        row_e.addWidget(self.end_date, 1)
        row_e.addWidget(self.end_time)
        form.addRow("시작", row_s)
        form.addRow("종료", row_e)
        form.addRow("캘린더", self.calendar)
        form.addRow("장소", self.location)
        form.addRow("메모", self.description)

        lay = QVBoxLayout(self)
        if event and event.response:  # 초대받은 일정: 참석 여부 응답(1-11)
            box = QGroupBox("초대" + (f" · 주최: {event.organizer}" if event.organizer else ""))
            row = QHBoxLayout(box)
            names = {"needsAction": "아직 응답 안 함", "accepted": "참석", "tentative": "미정", "declined": "거절"}
            row.addWidget(QLabel(f"내 응답: {names.get(event.response, event.response)}"))
            row.addStretch()
            for status, label in (("accepted", "참석"), ("tentative", "미정"), ("declined", "거절")):
                b = QPushButton(label)
                b.setEnabled(status != event.response)
                b.clicked.connect(lambda _=False, s=status: self._respond(s))
                row.addWidget(b)
            lay.addWidget(box)
        lay.addLayout(form)
        lay.addWidget(self.repeat)
        lay.addWidget(self.alarm)
        lay.addWidget(self.countdown)
        if self.read_only:
            lay.addWidget(self._note("읽기 전용 캘린더라 수정할 수 없습니다."))
        if not writable and not event:
            lay.addWidget(self._note("일정을 쓸 수 있는 캘린더가 없습니다. 먼저 로그인해 주세요."))

        buttons = QDialogButtonBox()
        self.save_btn = buttons.addButton("저장 및 동기화", QDialogButtonBox.AcceptRole)
        buttons.addButton("닫기" if self.read_only else "취소", QDialogButtonBox.RejectRole)
        if event and not self.read_only:
            delete = buttons.addButton("삭제", QDialogButtonBox.DestructiveRole)
            delete.clicked.connect(self._delete)
        if event and event.html_link:
            link = buttons.addButton("브라우저에서 열기", QDialogButtonBox.HelpRole)
            link.clicked.connect(lambda: webbrowser.open(event.html_link))
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

        self.all_day.toggled.connect(self._sync_time_visibility)
        self.start_date.dateChanged.connect(self._start_changed)
        self._sync_time_visibility()
        if self.read_only or not shown:
            for w in (self.title, self.done_box, self.text_color, self.all_day, self.start_date, self.end_date,
                      self.start_time, self.end_time, self.calendar, self.location, self.save_btn,
                      self.repeat, self.alarm, self.countdown):
                w.setEnabled(False)
            self.description.setReadOnly(True)
        self.title.setFocus()

    def _respond(self, status):
        self.action = "respond:" + status
        self.accept()

    @staticmethod
    def _note(text):
        lbl = QLabel(text)
        lbl.setStyleSheet("color:#b26a00")
        lbl.setWordWrap(True)
        return lbl

    def _sync_time_visibility(self):
        timed = not self.all_day.isChecked()
        self.start_time.setVisible(timed)
        self.end_time.setVisible(timed)

    def _start_changed(self, q):
        new = from_qdate(q)
        self.end_date.setDate(to_qdate(from_qdate(self.end_date.date()) + (new - self._prev_start)))
        self._prev_start = new

    def values(self):
        sd, ed = from_qdate(self.start_date.date()), from_qdate(self.end_date.date())
        ev = self.event
        v = {
            "title": self.title.text().strip() or "(제목 없음)",
            "all_day": self.all_day.isChecked(),
            "calendar_id": self.calendar.currentData(),
            "location": self.location.text().strip(),
            "description": self.description.toPlainText(),
            "done": self.done_box.isChecked(),
            "text_color": self.text_color.color,
            "order": ev.order if ev else 0,
            "line": ev.line if ev else False,
            "alarm": self.alarm.value(),
            "countdown": self.countdown.value(),
        }
        if v["all_day"]:
            v["start"], v["end"] = sd, ed
        else:
            st, et = self.start_time.time(), self.end_time.time()
            v["start"] = datetime.combine(sd, time(st.hour(), st.minute()))
            v["end"] = datetime.combine(ed, time(et.hour(), et.minute()))
        v["recurrence"] = self.repeat.rrule(sd, v["all_day"])
        return v

    def recurrence_changed(self):
        rule = self.values()["recurrence"]
        return rule is not KEEP and rule != (self.orig_rrule or NO_REPEAT)

    def _save(self):
        v = self.values()
        if not v["calendar_id"]:
            return
        if v["end"] < v["start"] or (not v["all_day"] and v["end"] == v["start"]):
            QMessageBox.warning(self, "Xetical", "종료가 시작보다 뒤여야 합니다.")
            return
        self.action = "save"
        self.accept()

    def _delete(self):
        if confirm(self, f"'{self.event.title}' 일정을 삭제할까요?"):
            self.action = "delete"
            self.accept()


# ---------------------------------------------------------------- 할 일(Google Tasks)
TASK_REPEATS = {"none": "안 함", "daily": "매일", "weekly": "매주", "biweekly": "2주마다",
                "monthly": "매월", "yearly": "매년"}


def repeat_dates(base, rule, until):
    """반복 할 일의 base 다음 회차들(until 전까지). 월·연 반복은 원래 날짜 기준이라 31일이 30일로 밀리지 않는다."""
    days = {"daily": 1, "weekly": 7, "biweekly": 14}.get(rule)
    months = {"monthly": 1, "yearly": 12}.get(rule)
    out, k = [], 1
    while days or months:
        if days:
            d = base + timedelta(days=days * k)
        else:
            y, m = divmod(base.year * 12 + base.month - 1 + months * k, 12)
            d = date(y, m + 1, min(base.day, pycal.monthrange(y, m + 1)[1]))
        if d >= until:
            break
        out.append(d)
        k += 1
    return out


class TaskDialog(QDialog):
    """구글 할 일 편집. task_lists = [{id: 'tasks:<목록id>', summary}] (새로 만들 때 목록 선택용)"""

    def __init__(self, task_lists, event=None, day=None, repeat="none"):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setWindowTitle("할 일 수정" if event else "새 할 일")
        self.setMinimumWidth(380)
        self.action = None
        self.event = event
        self.title = QLineEdit(event.title if event else "")
        self.title.setPlaceholderText("할 일")
        self.done_box = QCheckBox("완료")
        self.done_box.setChecked(bool(event and event.done))
        self.due = QDateEdit(calendarPopup=True)
        self.due.setDisplayFormat("yyyy-MM-dd (ddd)")
        self.due.setDate(to_qdate(event.start if event else day or date.today()))
        self.list = QComboBox()
        for c in task_lists:
            self.list.addItem(c["summary"].removeprefix("할 일: "), c["id"].split(":", 1)[1])
        if event:
            self.list.setCurrentIndex(max(0, self.list.findData(event.task_list)))
            self.list.setEnabled(False)  # 목록 옮기기는 지원하지 않는다
        self.notes = QPlainTextEdit(event.description if event else "")
        self.notes.setFixedHeight(80)
        self.repeat = QComboBox()
        for k, label in TASK_REPEATS.items():
            self.repeat.addItem(label, k)
        self.repeat.setCurrentIndex(max(0, self.repeat.findData(repeat)))
        repeat_hint = QLabel("구글은 할 일의 반복 정보를 다른 프로그램에 알려주지 않아요.\n"
                             "여기서 고르면 Xetical이 앞으로의 날짜에 흐리게 '예정'을 그려 줘요.")
        repeat_hint.setStyleSheet("color:gray;font-size:11px;")
        form = QFormLayout()
        top = QHBoxLayout()
        top.addWidget(self.title, 1)
        top.addWidget(self.done_box)
        form.addRow("할 일", top)
        form.addRow("마감일", self.due)
        form.addRow("반복 (Xetical 표시용)", self.repeat)
        form.addRow("", repeat_hint)
        form.addRow("목록", self.list)
        form.addRow("메모", self.notes)
        buttons = QDialogButtonBox()
        buttons.addButton("저장 및 동기화", QDialogButtonBox.AcceptRole)
        buttons.addButton("취소", QDialogButtonBox.RejectRole)
        if event:
            delete = buttons.addButton("삭제", QDialogButtonBox.DestructiveRole)
            delete.clicked.connect(self._delete)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(buttons)
        self.title.setFocus()

    def values(self):
        return {"title": self.title.text().strip() or "(제목 없음)", "notes": self.notes.toPlainText(),
                "due": from_qdate(self.due.date()), "done": self.done_box.isChecked(), "list_id": self.list.currentData(),
                "repeat": self.repeat.currentData()}

    def _save(self):
        if self.list.currentData():
            self.action = "save"
            self.accept()

    def _delete(self):
        if confirm(self, f"'{self.event.title}' 할 일을 삭제할까요?"):
            self.action = "delete"
            self.accept()


# ---------------------------------------------------------------- 모바일 / 날짜 선택
MOBILE_APPS = [
    ("Android", "https://play.google.com/store/apps/details?id=com.google.android.calendar"),
    ("iPhone", "https://apps.apple.com/app/google-calendar/id909319292"),
]


def qr_pixmap(text, size=170):
    import qrcode
    qr = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(text)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    n = len(matrix)
    cell = max(1, size // n)
    pm = QPixmap(n * cell, n * cell)
    pm.fill(QColor("white"))
    p = QPainter(pm)
    for y, row in enumerate(matrix):
        for x, on in enumerate(row):
            if on:
                p.fillRect(x * cell, y * cell, cell, cell, QColor("black"))
    p.end()
    return pm


class MobileDialog(QDialog):
    """DesktopCal '모바일 버전' 버튼 대응: 휴대폰용 구글 캘린더 앱 설치 QR."""

    def __init__(self):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setWindowTitle("캘린더 목록 모바일 버전")
        row = QHBoxLayout()
        for name, url in MOBILE_APPS:
            col = QVBoxLayout()
            img = QLabel()
            img.setPixmap(qr_pixmap(url))
            img.setAlignment(Qt.AlignCenter)
            link = QLabel(f"<a href='{url}'>{name}</a>")
            link.setOpenExternalLinks(True)
            link.setAlignment(Qt.AlignCenter)
            col.addWidget(img)
            col.addWidget(link)
            row.addLayout(col)
        info = QLabel("휴대폰 카메라로 QR 코드를 찍어 구글 캘린더 앱을 설치하세요.\n"
                      "같은 구글 계정으로 로그인하면 이 달력과 일정이 자동으로 맞춰집니다.")
        info.setAlignment(Qt.AlignCenter)
        close = QPushButton("닫기")
        close.clicked.connect(self.accept)
        lay = QVBoxLayout(self)
        lay.addWidget(info)
        lay.addLayout(row)
        lay.addWidget(close, 0, Qt.AlignRight)


class DatePickDialog(QDialog):
    """☰ '날짜로 이동'. 달력 위쪽에서 월·연도도 바로 고를 수 있다(예전 '월분 선택' 겸용)."""

    def __init__(self, current, on_pick):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setWindowTitle("날짜로 이동")
        cal = QCalendarWidget()
        cal.setSelectedDate(to_qdate(current))
        cal.setGridVisible(True)
        cal.activated.connect(lambda q: (on_pick(from_qdate(q)), self.accept()))
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("이동")
        buttons.button(QDialogButtonBox.Cancel).setText("취소")
        buttons.accepted.connect(lambda: (on_pick(from_qdate(cal.selectedDate())), self.accept()))
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(cal)
        lay.addWidget(buttons)
