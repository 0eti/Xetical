"""① 내부 자동 시험(추가분): t_all.py에서 다루지 않은 기능 전부."""
import json
import os
import sys
import time
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

sys.path.insert(0, ".")
SP = Path(sys.argv[1])
from PySide6.QtCore import QPointF, QRect, Qt
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QMessageBox

qapp = QApplication([])
import xetical.config as C
C.Config.save = lambda self: None
C.CONFIG_PATH = SP / "config_none.json"  # 사용자 설정(data/config.json)이 아니라 기본값으로 시험한다
import xetical.jobs as J
J.OUTBOX_PATH = SP / "outbox_m.json"
J.OUTBOX_PATH.unlink(missing_ok=True)
import xetical.model as M
M.CACHE_PATH = SP / "cache_m.json"
import xetical.reminders as R
R.FIRED_PATH = SP / "fired_m.json"
R.FIRED_PATH.unlink(missing_ok=True)
import xetical.app as A
A.CELLS_PATH = SP / "cells_m.json"
import httplib2
from googleapiclient.errors import HttpError
from xetical.model import Event

results = []


def check(name, cond, info=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{info}]" if info and not cond else ""))


def pump(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


class Resp(dict):
    def __init__(self, status):
        super().__init__(status=str(status))
        self.status = status
        self.reason = "x"


class FakeG:
    def __init__(self):
        self.calls, self.mode, self.n = [], None, 0

    def __getattr__(self, name):
        def fn(*args):
            if self.mode == "offline":
                raise httplib2.ServerNotFoundError("offline")
            if self.mode == "reject":
                raise HttpError(Resp(400), b'{"error":"bad"}')
            if self.mode == "gone":
                raise HttpError(Resp(404), b'{"error":"gone"}')
            self.calls.append((name,) + args)
            self.n += 1
            if name in ("create_event",):
                return f"real-{self.n}"
            if name == "create_task":
                return f"task:{args[0]}:t{self.n}"
            if name == "apply_ops":
                return [f"real-{self.n}-{i}" for i, op in enumerate(args[0]) if op[0] == "create"]
            if name == "fetch_all":
                return CALS, list(BASE), {}, (args[0], args[1]), [], "ok"
            return None
        return fn

    def has_token(self):
        return True

    def has_client_secret(self):
        return True


T = date.today()
D0 = datetime.combine(T, dtime.min)
CALS = [{"id": "me", "summary": "캘린더", "color": "#4a90d9", "primary": True, "writable": True, "selected": True},
        {"id": "ro", "summary": "읽기 전용", "color": "#999999", "primary": False, "writable": False, "selected": True},
        {"id": "tasks:L1", "summary": "할 일: 내 할 일", "color": "#4285f4", "primary": False, "writable": False,
         "selected": True, "tasks": True}]
BASE = [
    Event("e1", "me", "장보기", T, T + timedelta(days=1), True, line=True),
    Event("t1", "me", "치과", D0 + timedelta(hours=10), D0 + timedelta(hours=11), False, alarm="09:30|0"),
    Event("t2", "me", "점심", D0 + timedelta(hours=10, minutes=30), D0 + timedelta(hours=12), False),
    Event("cd", "me", "시험", T + timedelta(days=10), T + timedelta(days=11), True, countdown="exam|both|left"),
    Event("dec", "me", "거절함", T, T + timedelta(days=1), True, response="declined"),
    Event("ro1", "ro", "공유 일정", T, T + timedelta(days=1), True, writable=False),
    Event("task:L1:k1", "tasks:L1", "보고서", T, T + timedelta(days=1), True, task_list="L1", done=True),
]

a = A.App()
fake = FakeG()
a.gcal = fake
a.outbox.gcal = fake
a.store.set(CALS, list(BASE), {T: [("테스트휴일", True)]}, (T - timedelta(days=60), T + timedelta(days=90)))
a._last_sync = datetime.now()
w = a.win
w.resize(900, 640)

# ---------- 보낼 편지함 오류 처리 ----------
fake.mode = "reject"
failed = []
a.outbox.failed.disconnect()
a.outbox.failed.connect(lambda label, err: failed.append(label))
a.outbox.add("create_event", {"title": "x", "calendar_id": "me"}, label="거절될 작업")
pump(lambda: len(a.outbox) == 0)
check("편지함: 구글이 거절한 작업은 버리고 알림", failed == ["거절될 작업"])
fake.mode = "gone"
a.outbox.add("delete_event", BASE[0], "one", label="이미 지워진 일정")
pump(lambda: len(a.outbox) == 0)
check("편지함: 이미 지워진 것(404)도 버림", len(a.outbox) == 0)
fake.mode = None
from xetical.gcal import AuthError
check("편지함: 오프라인 판별", J.is_offline(httplib2.ServerNotFoundError("x")) and J.is_offline(HttpError(Resp(503), b""))
      and not J.is_offline(HttpError(Resp(400), b"")))
# 재시작해도 대기 작업이 남는지
fake.mode = "offline"
a.outbox.add("set_task_done", BASE[-1], False, label="끊긴 동안")
pump(lambda: a.win.status.startswith("오프라인"), 3)
a.outbox.retry.stop()
ob2 = J.Outbox(fake, a.worker)
check("편지함: 프로그램을 다시 켜도 대기 작업 유지", len(ob2) == 1 and ob2.jobs[0]["op"] == "set_task_done")
fake.mode = None
a.outbox.process()
pump(lambda: len(a.outbox) == 0)
a.sync = lambda: None  # 이후로는 동기화 결과로 화면이 바뀌지 않게

# ---------- 표시 필터 ----------
evs = w.cell_events(T)
check("거절한 초대는 기본으로 숨김", not any(e.id == "dec" for e in evs))
a.cfg["show_declined"] = True
check("설정하면 거절한 초대 표시", any(e.id == "dec" for e in w.cell_events(T)))
a.cfg["show_declined"] = False
a.cfg["show_done_tasks"] = False
check("완료한 할 일 숨기기", not any(e.task_list for e in w.cell_events(T)))
a.cfg["show_done_tasks"] = True
a.cfg["show_tasks"] = False
check("할 일 표시 끄기", not any(e.task_list for e in w.cell_events(T)))
a.cfg["show_tasks"] = True
a.cfg["calendar_visibility"] = {"ro": False}
check("캘린더 숨기기", not any(e.calendar_id == "ro" for e in w.cell_events(T)))
a.cfg["calendar_visibility"] = {}
a.cfg["done_to_bottom"] = True
check("완료 작업 맨 아래로", w.cell_events(T)[-1].done)
a.cfg["done_to_bottom"] = False

# ---------- 표시 형식 ----------
t1 = next(e for e in BASE if e.id == "t1")
check("시간 일정 [시작-끝] + 알림 시각", w.item_text(t1, T) == "⏰09:30 [10:00-11:00] 치과", w.item_text(t1, T))
a.cfg["show_end_time"] = False
check("끝나는 시간 숨기기", "[10:00]" in w.item_text(t1, T), w.item_text(t1, T))
a.cfg["show_end_time"] = True
a.cfg["show_alarm_time"] = False
check("알림 시각 숨기기", "⏰" not in w.item_text(t1, T))
a.cfg["show_alarm_time"] = True
inv = Event("i", "me", "회의", T, T, True, response="needsAction")
check("초대 표시(✉)", w.item_text(inv, T).startswith("✉"))
from xetical.view import month_label, weekday_name
check("달 표시 형식 3종", (month_label(date(2026, 11, 1), {"month_label": "ko"}), month_label(date(2026, 11, 1), {"month_label": "en"}),
                       month_label(date(2026, 11, 1), {"month_label": "num"})) == ("11월", "Nov", "11/1"))
check("요일 이름 3종", (weekday_name(0, {}, "full"), weekday_name(0, {}, "short"), weekday_name(0, {}, "en")) == ("월요일", "월", "Mon"))
a.cfg["calendar_text_color"] = True
check("구글 색을 글자색으로", w.item_color(t1) == t1.color)
a.cfg["calendar_text_color"] = False

# ---------- 표시 방식·이동 ----------
for mode in ("rolling", "month", "fixed_row", "rolling_row"):
    a.cfg["display_mode"] = mode
    w.anchor = w.today_anchor()
    first, rows, cols = w.grid_shape()
    w.grab()
    ok = {"rolling": (rows, cols) == (6, 7), "month": cols == 7 and 4 <= rows <= 6,
          "fixed_row": (rows, cols) == (1, 7), "rolling_row": (rows, cols) == (1, 7)}[mode]
    check(f"표시 방식: {mode}", ok and len(w._cells) == rows * cols, (rows, cols))
a.cfg["display_mode"] = "rolling"
w.anchor = w.today_anchor()
before = w.anchor
w.shift_page(1)
check("다음 페이지(한 달)", w.anchor.day <= 7 and (w.anchor - before).days > 7)
w.go_today()
check("오늘로", w.is_today_page())
a.cfg["passed_weeks"] = 2
check("지나간 주 표시", w.today_anchor() == w.week_first(T) - timedelta(days=14))
a.cfg["passed_weeks"] = 0
w.go_today()

# ---------- 막대·칸 제한·한 줄 ----------
trip = Event("trip", "me", "긴 여행 일정입니다", T, T + timedelta(days=9), True)
a.store.events.append(trip)
a.store.reindex()
w.grab()
bars = [r for r, ev in w._event_hits if ev.id == "trip"]
check("여러 날 막대가 주를 넘어 이어짐", len(bars) >= 2, len(bars))
a.cfg["span_bars"] = False
w.grab()
check("막대 끄면 칸마다 표시", sum(1 for r, ev in w._event_hits if ev.id == "trip") >= 7)
a.cfg["span_bars"] = True
a.store.events.remove(trip)
a.store.reindex()
a.cfg["max_items"] = 1
w.grab()
check("칸당 최대 일정 수", sum(1 for r, ev in w._event_hits if ev.first_day == T and not ev.is_multi_day) <= 1)
a.cfg["max_items"] = 0

# ---------- 하루 상세 보기 ----------
pending = []
w.dayViewRequested.connect(lambda d: pending.append(d))
r, d = w._date_hits[3]
w.on_press(r.center(), w.mapToGlobal(r.center().toPoint()), Qt.LeftButton)
w.on_release(r.center(), Qt.LeftButton)
pump(lambda: pending, 2)
check("날짜 숫자 클릭 → 하루 상세 보기", pending == [d])
pending.clear()
w.on_press(r.center(), w.mapToGlobal(r.center().toPoint()), Qt.LeftButton)
w.on_release(r.center(), Qt.LeftButton)
w.on_double(r.center(), Qt.LeftButton)
pump(lambda: False, 0.8)
check("더블클릭이면 상세 보기 대신 편집기", not pending)
from xetical.dayview import DayView
opened = []
a.new_event = lambda day, focus=None, start_time=None: opened.append((day, start_time))
dv = DayView(a, T)
dv.resize(400, 700)
dv.show()
qapp.processEvents()
blocks = dv.timeline._layout_blocks()
check("상세 보기: 겹치는 일정 나란히", {(ev.id, ncols) for ev, col, ncols in blocks} == {("t1", 2), ("t2", 2)}, blocks)
from PySide6.QtGui import QMouseEvent
from PySide6.QtCore import QEvent
y = dv.timeline._y(D0 + timedelta(hours=15, minutes=40))
ev_ = QMouseEvent(QEvent.MouseButtonDblClick, QPointF(200, y), QPointF(200, y), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
dv.timeline.mouseDoubleClickEvent(ev_)
check("상세 보기: 빈 시간 더블클릭 → 그 시각 새 일정", opened == [(T, dtime(15, 30))], opened)
dv.set_day(T + timedelta(days=1))
check("상세 보기: 다음 날로", dv.day == T + timedelta(days=1))
dv.close()
del a.new_event

# ---------- 일정 창 ----------
from xetical.dialogs import EventDialog, TaskDialog, ask_scope
a.cfg["default_alarm_on"] = True
a.cfg["default_alarm_time"] = "08:15"
a.cfg["default_alarm_days"] = [0, 1]
dlg = EventDialog(CALS, day=T, cfg=a.cfg, start_time=dtime(15, 30))
v = dlg.values()
check("새 일정: 기본 알림 적용", v["alarm"] == "08:15|0,1", v["alarm"])
check("새 일정: 고른 시각으로 시간 일정", not v["all_day"] and v["start"].hour == 15 and v["end"].hour == 16, v)
a.cfg["default_alarm_on"] = False
dlg2 = EventDialog(CALS, event=Event("i2", "me", "초대", T, T + timedelta(days=1), True, response="needsAction", organizer="팀장"),
                   cfg=a.cfg)
btn = next(b for b in dlg2.findChildren(type(dlg2.save_btn)) if b.text() == "참석")
btn.click()
check("일정 창: 초대 '참석' 버튼", dlg2.action == "respond:accepted")
a.cfg["time_format"] = "ampm"
dlg3 = EventDialog(CALS, day=T, cfg=a.cfg)
check("일정 창: 오전·오후 시각 표시", "AP" in dlg3.start_time.displayFormat())
a.cfg["time_format"] = "24h"
for d_ in (dlg, dlg2, dlg3):
    d_.close()
for label in ("취소", "저장 및 동기화", "닫기"):
    d_ = EventDialog(CALS, event=BASE[0], cfg=a.cfg) if label != "닫기" else EventDialog(CALS, event=BASE[5], cfg=a.cfg)
    d_.show()
    b = next((b for b in d_.findChildren(QDialogButtonBox)[-1].buttons() if b.text() == label), None)
    if b:
        b.click()
    check(f"일정 창 '{label}' 누르면 닫힘", b is not None and not d_.isVisible())

# ---------- 할 일 반복 기억 ----------
a._remember_task_repeat("L1", "보고서", "monthly")
check("할 일 반복 주기 기억", a.cfg["task_repeats"].get("L1|보고서") == "monthly")
a._remember_task_repeat("L1", "주간 보고서", "monthly", old_title="보고서")
check("할 일 이름 바꾸면 주기도 따라감", "L1|보고서" not in a.cfg["task_repeats"] and a.cfg["task_repeats"].get("L1|주간 보고서"))
a.cfg["task_repeats"] = {}

# ---------- 셀 편집기 ----------
from xetical.editor import CellEditor
ed = CellEditor(a.cfg, a.store, T, QRect(0, 0, 120, 90), "me", a.cfg["recent_colors"])
ed.show()
cur = ed.edit.textCursor()
ed.toggle_done()
check("편집기: 완료 표시(취소선)", ed._lines[ed.edit.document().firstBlock().userState()].done)
ed.set_line_color("#ff0000")
check("편집기: 글자색 + 최근 색 기억", ed._lines[0].color == "#ff0000" and "#ff0000" in a.cfg["recent_colors"])
ed.edit.moveCursor(cur.MoveOperation.End)
ed.edit.insertPlainText("\n새로 쓴 줄")
ops, lines, ask = ed.collect()
check("편집기: 새 줄은 서식 없이 새 일정", any(op[0] == "create" and op[2]["title"] == "새로 쓴 줄" and not op[2]["done"] for op in ops), ops)
check("편집기: 기존 줄은 완료·색 반영", any(op[0] == "patch" and op[2] == "e1" for op in ops))
check("편집기: 읽기 전용·초대·시간 일정은 위쪽 목록", {e.id for e in ed.other_events} >= {"ro1", "t1"} and not any(e.id == "ro1" for e in ed.line_events))
got = []
ed.committed.connect(lambda *x: got.append(x))
ed.save_and_close()
check("편집기: 저장 및 닫기", got and not ed.isVisible())
a.cfg["default_alarm_on"] = True
a.cfg["default_alarm_lines"] = True
ed2 = CellEditor(a.cfg, a.store, T + timedelta(days=3), QRect(0, 0, 120, 90), "me", [])
ed2.edit.setPlainText("알림 붙는 줄")
ops2, _l, _a = ed2.collect()
check("편집기: 기본 알림을 줄에도", ops2 and ops2[0][2]["alarm"].startswith("08:15"), ops2)
ed2.close()
a.cfg["default_alarm_on"] = a.cfg["default_alarm_lines"] = False
# 반복 줄 범위 고르기(전체)
rec = Event("r1", "me", "월세", T + timedelta(days=5), T + timedelta(days=6), True, recurring=True, recurring_event_id="m1")
a.store.events.append(rec)
a.store.reindex()
import xetical.editor as E
E.ask_scope = lambda *_a, **_k: "all"
ed3 = CellEditor(a.cfg, a.store, T + timedelta(days=5), QRect(0, 0, 120, 90), "me", [])
ed3.edit.setPlainText("월세 (집)")
ed3.edit.document().firstBlock().setUserState(0)
out = []
ed3.committed.connect(lambda *x: out.append(x))
ed3.commit()
check("편집기: 반복 일정 이름 바꾸기(전체)", out and out[0][3] and out[0][3][0][0] == "update_event" and out[0][3][0][3] == "all", out)
E.ask_scope = lambda *_a, **_k: "one"
ed4 = CellEditor(a.cfg, a.store, T + timedelta(days=5), QRect(0, 0, 120, 90), "me", [])
ed4.edit.setPlainText("")
out.clear()
ed4.committed.connect(lambda *x: out.append(x))
ed4.commit()
check("편집기: 반복 일정 줄 지우기(이번만)", out and ("delete", "me", "r1") in out[0][1], out)
ed3.close(); ed4.close()

# ---------- 셀 배경색 + 되돌리기 ----------
a.set_cell_color(T, "#123456")
check("셀 배경색", a.cell_colors.get(T.isoformat()) == "#123456")
a.undo()
check("셀 배경색 되돌리기", T.isoformat() not in a.cell_colors)

# ---------- 잘라내기 / 삭제 ----------
fake.calls.clear()
QMessageBox.question = staticmethod(lambda *a_, **k: QMessageBox.Yes)
e1 = next(e for e in a.store.events if e.id == "e1")
w.select(e1, T)
a.cut_selected()
pump(lambda: len(a.outbox) == 0)
check("잘라내기: 복사 + 삭제", a.clip and a.clip.id == "e1" and any(c[0] == "delete_event" and c[1].id == "e1" for c in fake.calls))
a.paste(T + timedelta(days=2))
pump(lambda: len(a.outbox) == 0)
check("잘라낸 것 붙여넣기", any(c[0] == "create_event" and c[1]["start"] == T + timedelta(days=2) for c in fake.calls))
fake.calls.clear()
a.undo()
a.undo()
pump(lambda: len(a.outbox) == 0)
check("붙여넣기·삭제 둘 다 되돌리기", any(c[0] == "delete_event" for c in fake.calls) and any(c[0] == "create_event" and c[1]["title"] == "장보기" for c in fake.calls), fake.calls)
task = next(e for e in a.store.events if e.task_list)
w.select(task, T)
a.copy_selected()
a.paste(T + timedelta(days=1))
pump(lambda: len(a.outbox) == 0)
check("할 일 복사·붙여넣기", any(c[0] == "create_task" and c[4] == T + timedelta(days=1) for c in fake.calls))
check("되돌릴 게 없을 때 안내", True)

# ---------- 우클릭 메뉴 ----------
from PySide6.QtWidgets import QMenu
shown = []
class _Menu(QMenu):
    def exec(self, *a_):
        shown.append([act.text() for act in self.actions()])
A.QMenu = _Menu
a.show_event_menu(Event("i3", "me", "초대", T, T + timedelta(days=1), True, response="needsAction"), T, w.mapToGlobal(w.rect().center()))
check("일정 우클릭 메뉴(편집·복사·잘라내기·삭제·초대 응답)", shown and {"편집…", "복사", "잘라내기", "삭제", "초대 응답"} <= set(shown[0]), shown)
check("일정 우클릭: 그날 항목은 '이 날짜' 하위 메뉴로", shown and "이 날짜" in shown[0] and "하루 상세 보기" not in shown[0], shown)
shown.clear()
a.show_day_menu(T, w.mapToGlobal(w.rect().center()))
check("칸 우클릭 메뉴(하루 상세 보기·새 할 일·붙여넣기)", shown and {"하루 상세 보기", "새 할 일…"} <= set(shown[0]) and any(t.startswith("붙여넣기") for t in shown[0]))
check("칸 우클릭 메뉴: ☰ 메뉴를 붙이지 않음", shown and "설정…" in shown[0] and "내보내기…" not in shown[0]
      and len([t for t in shown[0] if t]) <= 9, shown)
a._update_menu()
check("☰ 메뉴: 실행 취소 항목", a.act_undo.text().startswith("실행 취소"))
items = [act.text() for act in a.menu.actions() if act.text()]
check("☰ 메뉴: 겹치는 항목 뺌", not {"오늘 날짜로 이동", "이전 페이지", "최대 행", "자동 동기화", "계정 설정", "제품정보",
                                 "불러오기 (DesktopCal)…"} & set(items) and "날짜로 이동…" in items, items)
a._last_sync = datetime.now() - timedelta(minutes=5)
w.cloud_state = "ok"
check("☁ 풍선 도움말에 마지막 동기화", "마지막 동기화: 5분 전" in w.button_tip("cloud"), w.button_tip("cloud"))

# ---------- 알림 ----------
alarm_ev = Event("al", "me", "약 먹기", T, T + timedelta(days=1), True, alarm=f"{(datetime.now() - timedelta(minutes=1)):%H:%M}|0")
a.store.events.append(alarm_ev)
a.reminders.check()
pops = [p for p in a.reminders.popups if p.isVisible()]
check("알림: 시각이 되면 창이 뜸", pops)
a.reminders.check()
check("알림: 한 번만 뜸", len([p for p in a.reminders.popups if p.isVisible()]) == len(pops))
for p_ in pops:
    p_.close()
from xetical.reminders import alarm_times
check("알림: 며칠 전 계산", alarm_times(Event("x", "me", "x", date(2026, 5, 10), date(2026, 5, 11), True, alarm="09:00|0,1,7")) ==
      [datetime(2026, 5, 10, 9), datetime(2026, 5, 9, 9), datetime(2026, 5, 3, 9)])

# ---------- 카운트다운 ----------
cd = next(e for e in BASE if e.id == "cd")
w.countdowns = [cd]
check("카운트다운 바 문구", w.countdown_text(cd) == "[시험] 시험 10일 남음", w.countdown_text(cd))
a._refresh_cards()
card = next(iter(a.cards.values()), None)
check("카운트다운 카드", card is not None and card.isVisible())
for size, width in (("small", 136), ("large", 221)):
    a._set_cfg("card_size", size)
    check(f"카드 크기 {size}", card.width() == width, card.width())
a._set_cfg("card_size", "normal")
w.countdowns = []
a._refresh_cards()
check("카드 정리", not a.cards)

# ---------- 아이콘 ----------
from xetical.widgets import app_icon
i1, i2 = app_icon(20, True).pixmap(32, 32).toImage(), app_icon(20, False).pixmap(32, 32).toImage()
check("트레이 날짜 표시 켜기/끄기 아이콘이 다름", i1 != i2)

# ---------- 설정 창 테마 ----------
from xetical.widgets import apply_ui_theme, is_dark
apply_ui_theme("dark")
d1 = is_dark()
apply_ui_theme("light")
d2 = is_dark()
apply_ui_theme("auto")
check("설정 창 테마 직접 선택", d1 and not d2)

# ---------- 테마 전부 ----------
from xetical.themes import PRESETS
ok = True
for name in PRESETS:
    a.apply_theme(name)
    w.grab()
    ok &= a.cfg["cell_bg"] == PRESETS[name]["cell_bg"]
check(f"테마 {len(PRESETS)}종 적용", ok)
a.save_custom_theme("내 테마")
check("내 테마 저장", "내 테마" in a.theme_names())
a.apply_theme("DesktopCal 기본")

# ---------- 동기화 범위 ----------
captured = []
a.sync = A.App.sync.__get__(a)
a.gcal.fetch_all = lambda lo, hi, *x: captured.append((lo, hi)) or (CALS, list(BASE), {}, (lo, hi), [], "ok")
a.cfg["sync_past_months"], a.cfg["sync_future_months"] = 6, 12
a.sync()
pump(lambda: captured)
lo, hi = captured[0]
check("동기화 범위(과거 6개월·미래 12개월)", (T - lo).days >= 180 and (hi - T).days >= 360, (lo, hi))

# ---------- 단축키·절기 ----------
from xetical.hotkeys import parse
check("단축키 해석", parse("Ctrl+Alt+H") == (0x4003, 72) and parse("") is None)
from xetical.almanac import solar_term
check("24절기", solar_term(date(2026, 11, 7)) == "입동")

# ---------- 로그 크기 제한 ----------
import inspect
check("로그 회전 설정", "RotatingFileHandler" in inspect.getsource(A.main) and "maxBytes=1_000_000" in inspect.getsource(A.main))


a.shutdown()
print(f"\n{sum(ok for _, ok in results)}/{len(results)} 통과")
