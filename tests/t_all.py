import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, ".")
SP = Path(sys.argv[1])
for f in ("outbox.json", "cache.json", "cells.json", "fired.json", "backup.json"):
    (SP / f).unlink(missing_ok=True)

from PySide6.QtCore import QRect, QPointF, Qt
from PySide6.QtWidgets import QApplication

qapp = QApplication([])
import xetical.config as C
C.Config.save = lambda self: None
C.CONFIG_PATH = SP / "config_none.json"  # 사용자 설정(data/config.json)이 아니라 기본값으로 시험한다
import xetical.jobs as J
J.OUTBOX_PATH = SP / "outbox.json"
import xetical.model as M
M.CACHE_PATH = SP / "cache.json"
import xetical.reminders as R
R.FIRED_PATH = SP / "fired.json"
import xetical.app as A
A.CELLS_PATH = SP / "cells.json"
import httplib2
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


class FakeG:
    def __init__(self):
        self.calls, self.offline, self.n = [], False, 0

    def _net(self):
        if self.offline:
            raise httplib2.ServerNotFoundError("offline")

    def _id(self):
        self.n += 1
        return f"real-{self.n}"

    def has_token(self):
        return True

    def has_client_secret(self):
        return True

    def create_event(self, v):
        self._net(); i = self._id(); self.calls.append(("create_event", v["title"], i)); return i

    def update_event(self, ev, v, scope="one"):
        self._net(); self.calls.append(("update_event", ev.id, v["title"], scope))

    def delete_event(self, ev, scope="one"):
        self._net(); self.calls.append(("delete_event", ev.id, scope))

    def apply_ops(self, ops):
        self._net()
        out = []
        for op in ops:
            if op[0] == "create":
                i = self._id(); out.append(i); self.calls.append(("create", op[2]["title"], i))
            elif op[0] == "patch":
                self.calls.append(("patch", op[2], op[3].get("summary")))
            else:
                self.calls.append(("delete", op[2]))
        return out

    def move_event(self, ev, delta):
        self._net(); self.calls.append(("move_event", ev.id, delta, ev.first_day.isoformat()))

    def set_task_done(self, ev, done):
        self._net(); self.calls.append(("set_task_done", ev.id, done))

    def create_task(self, list_id, title, notes, due, done=False):
        self._net(); i = f"task:{list_id}:t{self.n}"; self.n += 1; self.calls.append(("create_task", title, i)); return i

    def delete_task(self, ev):
        self._net(); self.calls.append(("delete_task", ev.id))

    def update_task(self, ev, title, notes, due, done):
        self._net(); self.calls.append(("update_task", ev.id, title))

    def respond_event(self, ev, status):
        self._net(); self.calls.append(("respond_event", ev.id, status))

    def fetch_all(self, start, end, want_holidays=True, region="south_korea"):
        self._net()
        self.calls.append(("fetch_all",))
        return CALS, [e for e in BASE], {}, (start, end), [], "ok"


T = date.today()
CALS = [{"id": "me", "summary": "캘린더", "color": "#4a90d9", "primary": True, "writable": True, "selected": True},
        {"id": "tasks:L1", "summary": "할 일: 내 할 일", "color": "#4285f4", "primary": False, "writable": False,
         "selected": True, "tasks": True}]
BASE = [
    Event("e1", "me", "장보기", T, T + timedelta(days=1), True, line=True, order=0),
    Event("e2", "me", "운동", T, T + timedelta(days=1), True, line=True, order=1),
    Event("trip", "me", "여행", T + timedelta(days=1), T + timedelta(days=4), True),
    Event("inv", "me", "회의", datetime.combine(T, datetime.min.time()) + timedelta(hours=14),
          datetime.combine(T, datetime.min.time()) + timedelta(hours=15), False, response="needsAction", organizer="팀장"),
    Event("task:L1:k1", "tasks:L1", "화분 물 주기", T, T + timedelta(days=1), True, task_list="L1"),
    Event("rec1", "me", "월세", T + timedelta(days=2), T + timedelta(days=3), True, recurring=True,
          recurring_event_id="rmaster"),
]

# ---------- 1. 직렬화 ----------
ev = BASE[3]
enc = J.encode(["x", ev, T, datetime(2026, 1, 1, 9), {"d": T}])
dec = J.decode(J.resolve(enc, {"inv": "INV-REAL"}))
check("직렬화 왕복(Event·날짜·임시 id 교체)", dec[1].id == "INV-REAL" and dec[2] == T and dec[3].hour == 9 and dec[4]["d"] == T)

# ---------- 앱 준비 ----------
a = A.App()
fake = FakeG()
a.gcal = fake
a.outbox.gcal = fake
a.store.set(CALS, [e for e in BASE], {}, (T - timedelta(days=60), T + timedelta(days=90)))
a._last_sync = datetime.now()

# ---------- 2. 칸 편집 + 실행 취소 ----------
e1 = next(e for e in a.store.events if e.id == "e1")
e2 = next(e for e in a.store.events if e.id == "e2")
from xetical.gcal import event_props
ops = [("patch", "me", "e1", {"summary": "장보기 (우유)", "extendedProperties": {"private": event_props({"order": 0, "line": True})}}),
       ("delete", "me", "e2"),
       ("create", "me", {"title": "새 줄", "all_day": True, "start": T, "end": T, "description": "", "location": "",
                         "done": False, "text_color": "", "order": 1, "line": True, "calendar_id": "me"})]
lines = [{"text": "장보기 (우유)", "ev": e1, "done": False, "color": ""}, {"text": "새 줄", "ev": None, "done": False, "color": ""}]
a.apply_cell(T, ops, lines, [])
check("칸 편집: 화면에 바로 반영", any(e.title == "장보기 (우유)" for e in a.store.events) and not any(e.id == "e2" for e in a.store.events)
      and any(e.title == "새 줄" and e.id.startswith("tmp-") for e in a.store.events))
pump(lambda: len(a.outbox) == 0)
created = next(c for c in fake.calls if c[0] == "create")
check("칸 편집: 구글로 전송·임시 id→진짜 id 기록", created[2] in a.outbox.id_map.values())
fake.calls.clear()
a.undo()
pump(lambda: len(a.outbox) == 0 and any(c[0] == "fetch_all" for c in fake.calls))
kinds = [c[0] for c in fake.calls]
check("실행 취소: 이름 되돌림·지운 줄 다시 만들기·새 줄 삭제(진짜 id로)",
      ("patch", "e1", "장보기") in fake.calls and any(c[0] == "create" and c[1] == "운동" for c in fake.calls)
      and ("delete", created[2]) in fake.calls, fake.calls)

# ---------- 3. 오프라인 대기 ----------
a.store.set(CALS, [e for e in BASE], {}, (T - timedelta(days=60), T + timedelta(days=90)))
fake.offline = True
fake.calls.clear()
task = next(e for e in a.store.events if e.task_list)
task.done = False
a.toggle_task(task)
pump(lambda: a.win.status.startswith("오프라인"), 3)
check("오프라인: 변경 보관 + 안내", len(a.outbox) == 1 and a.win.status.startswith("오프라인") and J.OUTBOX_PATH.exists(),
      a.win.status)
check("오프라인: 파일에 저장됨", "set_task_done" in J.OUTBOX_PATH.read_text("utf-8"))
fake.offline = False
a.outbox.retry.stop()
a.outbox.process()
pump(lambda: len(a.outbox) == 0 and any(c[0] == "fetch_all" for c in fake.calls))
check("오프라인: 다시 연결되면 보내고 동기화", ("set_task_done", "task:L1:k1", True) in fake.calls and not a.win.status.startswith("오프라인"),
      fake.calls)

# ---------- 4. 끌어서 옮기기 + 실행 취소 ----------
fake.calls.clear()
trip = next(e for e in a.store.events if e.id == "trip")
a.move_event(trip, 3)
check("옮기기: 화면에 바로 반영", trip.first_day == T + timedelta(days=4))
pump(lambda: len(a.outbox) == 0)
check("옮기기: 원래 날짜 기준으로 전송", ("move_event", "trip", 3, (T + timedelta(days=1)).isoformat()) in fake.calls, fake.calls)
fake.calls.clear()
a.undo()
pump(lambda: len(a.outbox) == 0)
check("옮기기 되돌리기", ("move_event", "trip", -3, (T + timedelta(days=4)).isoformat()) in fake.calls, fake.calls)

# ---------- 5. 복사·붙여넣기 ----------
a.store.set(CALS, [e for e in BASE], {}, (T - timedelta(days=60), T + timedelta(days=90)))
fake.calls.clear()
a.win.select(next(e for e in a.store.events if e.id == "inv"), T)
a.copy_selected()
a.paste(T + timedelta(days=7))
pasted = [e for e in a.store.events if e.id.startswith("tmp-") and e.title == "회의"]
check("붙여넣기: 같은 시각으로 다른 날에", pasted and pasted[0].start.hour == 14 and pasted[0].first_day == T + timedelta(days=7))
pump(lambda: len(a.outbox) == 0)
pid = next(c[2] for c in fake.calls if c[0] == "create_event")
a.undo()
pump(lambda: len(a.outbox) == 0)
check("붙여넣기 되돌리기(진짜 id 삭제)", ("delete_event", pid, "one") in fake.calls, fake.calls)

# ---------- 6. 초대 응답 ----------
fake.calls.clear()
inv = next(e for e in a.store.events if e.id == "inv")
a.respond(inv, "accepted")
pump(lambda: len(a.outbox) == 0)
check("초대 응답 전송", ("respond_event", "inv", "accepted") in fake.calls)

# ---------- 7. 셀 편집기: 반복 일정 줄 ----------
from xetical.editor import CellEditor
ed = CellEditor(a.cfg, a.store, T + timedelta(days=2), QRect(0, 0, 120, 90), "me", [])
rec_line = [ln for ln in ed._lines if ln.ev is not None and ln.ev.recurring]
check("편집기: 반복 일정도 줄로 나옴", len(rec_line) == 1)
ed.edit.setPlainText("월세 (집)")
ed._lines[0].ev = rec_line[0].ev
ed.edit.document().firstBlock().setUserState(0)
ops2, lines2, ask = ed.collect()
check("편집기: 반복 일정 이름 바꾸면 범위를 물음", ask and ask[0][0] == "rename" and ask[0][2] == "월세 (집)", ask)
ed.close()

# ---------- 8. 화면 그리기 ----------
a.store.set(CALS, [e for e in BASE], {}, (T - timedelta(days=60), T + timedelta(days=90)))
a._project_task_repeats()
a.cfg["task_repeats"] = {"L1|화분 물 주기": "weekly"}
for e in a.store.events:
    if e.task_list:
        e.done = False
a._project_task_repeats()
w = a.win
w.anchor = w.today_anchor()
w.resize(800, 600)
img = w.grab()
img.save(str(SP / "render.png"))
check("그리기: 할 일 ☐ 누르는 자리 생김", any(ev.task_list for _r, ev in w._check_hits))
check("그리기: 여러 날 막대 생김", any(ev.id == "trip" for _r, ev in w._event_hits))
check("그리기: 반복 할 일 예정 표시", any(e.virtual for e in a.store.events))
check("그리기: 날짜 숫자 누르는 자리", len(w._date_hits) == 42)
hit = next(r for r, ev in w._check_hits if ev.task_list)
fake.calls.clear()
w.on_press(hit.center(), w.mapToGlobal(hit.center().toPoint()), Qt.LeftButton)
pump(lambda: len(a.outbox) == 0)
check("칸에서 ☐ 누르면 완료", any(c[0] == "set_task_done" for c in fake.calls), fake.calls)
for k, v in (("time_format", "ampm"), ("month_label", "en"), ("weekday_style", "en"), ("single_line", True),
             ("max_items", 1), ("calendar_text_color", True), ("today_fill", "#335577"), ("today_border_width", 4),
             ("weekday_colors", {"6": "#ff5555", "5": "#5599ff"}), ("display_mode", "month")):
    a.cfg[k] = v
w.anchor = w.today_anchor()
w.grab().save(str(SP / "render2.png"))
check("그리기: 표시 설정 바꾼 화면", True)
from xetical.view import fmt_time, title_text
check("시간 형식(오전·오후)", fmt_time(datetime(2026, 1, 1, 14, 5), a.cfg) == "오후 2:05")
a.cfg["title_format"] = "{month}월 {day}일 {weekday} · {week}주차"
check("제목 문구 형식", "주차" in title_text(a.cfg))
a.cfg["title_format"] = "{oops}"
check("제목 문구 잘못 써도 기본값으로", title_text(a.cfg).startswith("오늘은"))
for k in ("time_format", "month_label", "weekday_style", "single_line", "max_items", "calendar_text_color",
          "today_fill", "today_border_width", "weekday_colors", "display_mode", "title_format"):
    a.cfg[k] = C.DEFAULTS[k]

# ---------- 9. 설정 창 ----------
from xetical.settings import SettingsWindow
s = SettingsWindow(a)
check("설정 창: 8개 탭", s.nav.count() == 8 and [s.nav.item(i).text() for i in range(8)][4:6] == ["할 일 · 알림", "트래커"])
for i in range(7):
    s.nav.setCurrentRow(i)
    s.grab().save(str(SP / f"set{i}.png"))
s.restyle()
check("설정 창: 테마 다시 칠하기", True)

# ---------- 10. 하루 상세 보기 ----------
from xetical.dayview import DayView
dv = DayView(a, T)
dv.grab().save(str(SP / "dayview.png"))
check("하루 상세 보기: 시간 일정·종일 일정 구분", any(e.id == "inv" for e in dv.timed) and any(e.task_list for e in dv.allday))

# ---------- 11. 테마·설정 백업 ----------
a.apply_theme("밝은 종이")
check("테마 적용", a.cfg["cell_bg"] == "#ffffff" and a.cfg["item_font"]["color"] == "#222222")
a.cfg["task_repeats"] = {"L1|월세": "monthly"}
a.export_settings(str(SP / "backup.json"))
a.apply_theme("DesktopCal 기본")
a.cfg["task_repeats"] = {"L1|화분 물 주기": "weekly", "L1|월세": "yearly"}
n = a.import_settings_file(str(SP / "backup.json"))
check("설정 백업→복원", a.cfg["cell_bg"] == "#ffffff" and n > 30 and "geometry" not in __import__("json").loads((SP / "backup.json").read_text("utf-8")))
check("설정 가져오기: 예전 반복 할 일 기억은 가져오지 않음(구글 반복 일정으로 옮긴 것을 또 옮기지 않게)",
      a.cfg["task_repeats"] == {"L1|화분 물 주기": "weekly", "L1|월세": "yearly"}, a.cfg["task_repeats"])
a.cfg["task_repeats"] = {}
a.apply_theme("DesktopCal 기본")

# ---------- 12. 화면 밖 위치 보정 ----------
r = A.fit_rect(QRect(5000, 5000, 600, 400))
scr = qapp.primaryScreen().availableGeometry()
check("화면 밖 창을 화면 안으로", scr.contains(r), r)

# ---------- 13. 아침 요약 ----------
a.reminders.check_summary(force=True)
check("아침 요약 창", a.reminders.summary.isVisible() if hasattr(a.reminders, "summary") else False)
a.reminders.summary.grab().save(str(SP / "summary.png"))
a.reminders.summary.close()

# ---------- 14. 마우스 훅 설치·해제 ----------
from xetical.mousehook import ClickThrough, IconProbe
h = ClickThrough(a.win)
h.start()
ok = bool(h.hook)
h.stop()
check("마우스 훅 설치·해제", ok and not h.hook)

a.shutdown()
print(f"\n{sum(ok for _, ok in results)}/{len(results)} 통과")
