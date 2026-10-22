"""알림: 정해진 시각이 되면 화면 오른쪽 아래에 알림 창을 띄운다(DesktopCal 알림 팝업)."""
from datetime import datetime, time, timedelta

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .config import FIRED_PATH, load_json, save_json
from .widgets import play_sound

WINDOW = timedelta(minutes=15)  # 놓친 알림도 15분 안이면 띄운다


def alarm_times(ev):
    out = []
    a = ev.alarm_parts()
    if a:
        h, m, days = a
        for d in days:
            out.append(datetime.combine(ev.first_day - timedelta(days=d), time(h, m)))
    elif not ev.all_day:  # 구글에서 건 팝업 알림
        out += [ev.start - timedelta(minutes=x) for x in ev.popup_minutes]
    return out


class ReminderPopup(QWidget):
    def __init__(self, ev, when, on_open, stack_index):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setStyleSheet("""
            ReminderPopup { background:#2e2e2e; border:1px solid #6b6b6b; }
            QLabel { color:#eee; }
            QPushButton { color:#fff; background:#555; border:none; border-radius:3px; padding:4px 12px; }
            QPushButton:hover { background:#666; }
            QPushButton#primary { background:#2f80ed; }
        """)
        lay = QVBoxLayout(self)
        head = QLabel("🔔 알림")
        head.setStyleSheet("color:#ffffc0;font-weight:bold;")
        title = QLabel(ev.title)
        title.setWordWrap(True)
        title.setStyleSheet(f"font-size:14px;font-weight:bold;color:{ev.text_color or '#ffffff'};")
        lay.addWidget(head)
        lay.addWidget(title)
        lay.addWidget(QLabel(when))
        row = QHBoxLayout()
        row.addStretch()
        open_btn = QPushButton("일정 보기")
        open_btn.clicked.connect(lambda: (self.close(), on_open(ev)))
        ok = QPushButton("확인")
        ok.setObjectName("primary")
        ok.clicked.connect(self.close)
        row.addWidget(open_btn)
        row.addWidget(ok)
        lay.addLayout(row)
        self.setFixedWidth(300)
        self.adjustSize()
        a = QApplication.primaryScreen().availableGeometry()
        self.move(a.right() - self.width() - 12, a.bottom() - (self.height() + 10) * (stack_index + 1) - 2)


class SummaryPopup(QWidget):
    """아침 요약 알림(2-3): 오늘 일정과 오늘까지인 할 일."""

    def __init__(self, title, lines, on_open):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setStyleSheet("""
            SummaryPopup { background:#2e2e2e; border:1px solid #6b6b6b; }
            QLabel { color:#eee; }
            QPushButton { color:#fff; background:#555; border:none; border-radius:3px; padding:4px 12px; }
            QPushButton#primary { background:#2f80ed; }
        """)
        lay = QVBoxLayout(self)
        head = QLabel(title)
        head.setStyleSheet("color:#ffffc0;font-weight:bold;font-size:14px;")
        lay.addWidget(head)
        for text, color in lines or [("오늘은 일정이 없어요 🙂", "#cccccc")]:
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            lbl.setStyleSheet(f"color:{color};")
            lay.addWidget(lbl)
        row = QHBoxLayout()
        row.addStretch()
        open_btn = QPushButton("하루 상세 보기")
        open_btn.clicked.connect(lambda: (self.close(), on_open()))
        ok = QPushButton("확인")
        ok.setObjectName("primary")
        ok.clicked.connect(self.close)
        row.addWidget(open_btn)
        row.addWidget(ok)
        lay.addLayout(row)
        self.setFixedWidth(320)
        self.adjustSize()
        a = QApplication.primaryScreen().availableGeometry()
        self.move(a.right() - self.width() - 12, a.bottom() - self.height() - 12)


class Reminders(QObject):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.fired = set(load_json(FIRED_PATH, []))
        self.popups = []
        self.timer = QTimer(self, interval=20_000, timeout=self.check)
        self.timer.start()

    def check_summary(self, force=False):
        """정한 시각이 지났고 오늘 아직 안 띄웠으면 아침 요약을 띄운다(PC를 늦게 켜도 그날 한 번)."""
        cfg, app = self.app.cfg, self.app
        now = datetime.now()
        today = now.date()
        if not force:
            if not cfg["summary_on"] or app._last_sync is None:
                return
            try:
                hh, mm = (int(x) for x in cfg["summary_time"].split(":"))
            except ValueError:
                hh, mm = 8, 30
            if cfg["summary_last"] == today.isoformat() or now.time() < time(hh, mm):
                return
            cfg["summary_last"] = today.isoformat()
            cfg.save()
        win = app.win
        evs = [e for e in win.cell_events(today) if not e.virtual]
        events = [e for e in evs if not e.task_list]
        tasks = [e for e in evs if e.task_list and not e.done]
        overdue = [e for e in app.store.events if e.task_list and not e.done and not e.virtual
                   and e.first_day < today and win.visible_calendar(e.calendar_id)]
        lines = []
        for e in events:
            lines.append(("• " + win.item_text(e, today), win.item_color(e)))
        for e in tasks:
            lines.append(("☐ " + e.title, win.item_color(e)))
        for e in overdue:
            lines.append((f"☐ {e.title}  (마감 {e.first_day:%m/%d} 지남)", "#ffb347"))
        title = f"좋은 아침이에요 · 오늘 일정 {len(events)}개, 할 일 {len(tasks) + len(overdue)}개"
        self.summary = SummaryPopup(title, lines, lambda: app.open_day_view(today))
        self.summary.show()
        play_sound(cfg["alarm_sound"])

    def check(self):
        self.check_summary()
        now = datetime.now()
        seen, new = set(), False
        for ev in self.app.store.events + self.app.win.countdowns:
            if ev.id in seen or ev.done:
                continue
            seen.add(ev.id)
            for t in alarm_times(ev):
                key = f"{self._key_id(ev)}|{t:%Y%m%d%H%M}"
                if t <= now < t + WINDOW and key not in self.fired:
                    self.fired.add(key)
                    new = True
                    self._show(ev)
        if new:
            cutoff = (now - timedelta(days=3)).strftime("%Y%m%d")
            self.fired = {k for k in self.fired if k.rsplit("|", 1)[-1][:8] >= cutoff}
            save_json(FIRED_PATH, sorted(self.fired))

    def _key_id(self, ev):
        """띄운 알림을 기억하는 이름. 새 일정은 임시 id(tmp-…)로 먼저 보이다가 동기화 뒤 진짜 id로 바뀌므로
        진짜 id로 맞춘다. 반복 일정은 회차 id 대신 원본 id를 쓴다(알림 시각이 회차를 가른다):
        임시로 그린 회차(tmp-…_날짜)와 구글에서 받은 회차의 id 모양이 달라도 같은 알림으로 친다."""
        base = ev.recurring_event_id or ev.id
        if base.startswith("tmp-"):
            base = base.split("_", 1)[0]
        return self.app.outbox.real_id(base)

    def _show(self, ev):
        if ev.all_day:
            when = f"{ev.first_day:%m월 %d일} (하루 종일)"
        else:
            when = f"{ev.start:%m월 %d일 %H:%M} ~ {ev.end:%H:%M}"
        popup = ReminderPopup(ev, when, self.app.edit_event, len(self.popups))
        self.popups.append(popup)
        # 닫으면 창이 지워진다(WA_DeleteOnClose). 목록에 남겨 두면 다음 알림 때 지워진 창을 건드려 알림이 멈춘다.
        popup.destroyed.connect(lambda *_, p=popup: setattr(self, "popups", [q for q in self.popups if q is not p]))
        popup.show()
        play_sound(self.app.cfg["alarm_sound"])
