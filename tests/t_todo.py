"""반복 할 일 = 구글 캘린더의 하루 종일 반복 일정 + '할 일' 표시(xeticalTodo). 구글 대신 가짜를 쓴다."""
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, ".")
SP = Path(sys.argv[1])
from PySide6.QtWidgets import QApplication

qapp = QApplication([])
import xetical.config as C
C.Config.save = lambda self: None
C.CONFIG_PATH = SP / "config_none.json"  # 사용자 설정(data/config.json)이 아니라 기본값으로 시험한다
C.CACHE_PATH = SP / "cache_t.json"
import xetical.jobs as J
J.OUTBOX_PATH = SP / "outbox_t.json"
J.OUTBOX_PATH.unlink(missing_ok=True)
import xetical.model as M
M.CACHE_PATH = SP / "cache_t.json"
import xetical.reminders as R
R.FIRED_PATH = SP / "fired_t.json"
import xetical.mood as MD
MD.MOODS_PATH = SP / "moods_t.json"
import xetical.app as A
A.CELLS_PATH = SP / "cells_t.json"
A.config.CACHE_PATH = C.CACHE_PATH
A.MoodData = lambda: MD.MoodData(SP / "moods_t.json")
from xetical.dialogs import rule_dates
from xetical.gcal import GCal, _props_values, event_props
from xetical.model import Event
from xetical.todos import TODO_RULES, rule_of, todo_rrule, todo_values

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


# ---------------------------------------------------------------- 가짜 구글 API (gcal.py 안쪽 시험용)
class Req:
    def __init__(self, svc, kind, kw):
        self.svc, self.kind, self.kw = svc, kind, kw
        self.method = {"insert": "POST", "patch": "PATCH", "delete": "DELETE"}.get(kind.split(".")[1], "GET")

    def execute(self, num_retries=0):
        return self.svc.handle(self.kind, self.kw)


class Coll:
    def __init__(self, svc, name):
        self.svc, self.name = svc, name

    def __getattr__(self, verb):
        return lambda **kw: Req(self.svc, f"{self.name}.{verb}", kw)


class Batch:
    def __init__(self, cb):
        self.cb, self.reqs = cb, []

    def add(self, r, request_id):
        self.reqs.append((r, request_id))

    def execute(self):
        for r, rid in self.reqs:
            self.cb(rid, r.execute(), None)


class FakeSvc:
    """캘린더(events.*)와 할 일(tasks.*)을 함께 흉내 낸다. 돌려줄 목록은 함수로 정해 둔다."""

    def __init__(self):
        self.log = []
        self.master = {"id": "rt", "start": {"date": "2027-01-15"}, "end": {"date": "2027-01-16"},
                       "recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=FR"]}
        self.instances = lambda kw: []
        self.lists = lambda kw: []
        self.task_items = lambda kw: []

    def __getattr__(self, name):
        return lambda: Coll(self, name)

    def new_batch_http_request(self, callback):
        return Batch(callback)

    def handle(self, kind, kw):
        self.log.append((kind, kw))
        if kind == "settings.get":
            return {"value": "Asia/Seoul"}
        if kind == "events.get":
            return self.master
        if kind == "events.instances":
            return {"items": self.instances(kw)}
        if kind == "events.list":
            return {"items": self.lists(kw)}
        if kind == "events.insert":
            return {"id": kw["body"].get("id") or "new1"}
        if kind == "tasks.list":
            return {"items": self.task_items(kw)}
        if kind == "tasks.insert":
            return {"id": "t-new"}
        return {}

    def calls(self, kind):
        return [kw for k, kw in self.log if k == kind]


def fresh():
    g, svc = GCal(), FakeSvc()
    g._service = g._tasks = lambda: svc
    return g, svc


def private(body):
    return (body or {}).get("extendedProperties", {}).get("private", {})


# ---------- 1: 할 일 표시와 반복 규칙 ----------
FRI = date(2027, 1, 15)
check("1 매일", todo_rrule("daily", FRI) == "RRULE:FREQ=DAILY")
check("1 매주는 그 요일", todo_rrule("weekly", FRI) == "RRULE:FREQ=WEEKLY;BYDAY=FR")
check("1 2주마다", todo_rrule("biweekly", FRI) == "RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=FR")
check("1 매월 15일", todo_rrule("monthly", FRI) == "RRULE:FREQ=MONTHLY;BYMONTHDAY=15")
check("1 매월 31일은 짧은 달 말일", todo_rrule("monthly", date(2027, 1, 31))
      == "RRULE:FREQ=MONTHLY;BYMONTHDAY=28,29,30,31;BYSETPOS=-1", todo_rrule("monthly", date(2027, 1, 31)))
check("1 매월 30일", todo_rrule("monthly", date(2027, 1, 30)) == "RRULE:FREQ=MONTHLY;BYMONTHDAY=28,29,30;BYSETPOS=-1")
check("1 매년", todo_rrule("yearly", FRI) == "RRULE:FREQ=YEARLY")
check("1 매년 2월 29일은 평년 28일", todo_rrule("yearly", date(2028, 2, 29))
      == "RRULE:FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=28,29;BYSETPOS=-1")
check("1 규칙 되읽기", all(rule_of(todo_rrule(r, d), d) == r for r in TODO_RULES
                       for d in (FRI, date(2027, 1, 31), date(2028, 2, 29))))
check("1 다른 모양 규칙은 None", rule_of("RRULE:FREQ=WEEKLY;BYDAY=MO,WE", FRI) is None)
got = rule_dates(todo_rrule("monthly", date(2027, 1, 31)), date(2027, 1, 31), date(2027, 5, 1))
check("1 rule_dates: 31일 매월은 2월 28일·4월 30일",
      got == [date(2027, 1, 31), date(2027, 2, 28), date(2027, 3, 31), date(2027, 4, 30)], got)
got = rule_dates(todo_rrule("yearly", date(2028, 2, 29)), date(2028, 2, 29), date(2030, 3, 1))
check("1 rule_dates: 2월 29일 매년은 평년 2월 28일", got == [date(2028, 2, 29), date(2029, 2, 28), date(2030, 2, 28)], got)
v = todo_values("손톱 깎기", "메모", FRI, "weekly", "me")
check("1 할 일 표시가 구글 속성에", event_props(v)["xeticalTodo"] == "1" and _props_values(event_props(v))["todo"])
g, svc = fresh()
g.create_event(v)
body = svc.calls("events.insert")[0]["body"]
check("1 만들 때 휴대폰 알림 끔", body.get("reminders") == {"useDefault": False, "overrides": []}, body.get("reminders"))
check("1 하루 종일 반복", body["start"] == {"date": "2027-01-15"} and body["recurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=FR"],
      body)
ev = Event("x", "me", "손톱 깎기", FRI, FRI + timedelta(days=1), True, todo=True)
check("1 반복 할 일은 한 줄 일정이 아님", not ev.is_line_candidate)
check("1 보통 하루 종일 일정은 그대로 한 줄", Event("y", "me", "t", FRI, FRI + timedelta(days=1), True).is_line_candidate)
check("1 일정 값에 할 일 표시", ev.values()["todo"] is True)


# ---------------------------------------------------------------- 앱 수준(가짜 구글이 받은 작업을 적어 둔다)
class FakeG:
    def __init__(self):
        self.calls, self.rrule = [], ""

    def __getattr__(self, name):
        def fn(*args):
            self.calls.append((name, args))
            if name == "create_event":
                return args[0].get("event_id") or "real"
            if name == "apply_ops":
                return [op[2].get("event_id") for op in args[0] if op[0] == "create"]
            if name == "create_task":
                return f"task:{args[0]}:new"
            if name == "fetch_all":
                return CALS, [], {}, (args[0], args[1]), [], "ok"
            if name == "get_rrule":
                return self.rrule, {}
            return None
        return fn

    def has_token(self):
        return True

    def has_client_secret(self):
        return True

    def has_drive_scope(self):
        return False


def jobs_named(op):
    """가짜 구글이 받은 그 작업들의 인자(보낼 편지함을 다 보낸 뒤)."""
    pump(lambda: len(a.outbox) == 0)
    return [args for name, args in fake.calls if name == op]


CALS = [{"id": "me", "summary": "캘린더", "color": "#4a90d9", "primary": True, "writable": True, "selected": True},
        {"id": "tasks:L1", "summary": "할 일: 목록", "color": "#4285f4", "primary": False, "writable": False,
         "selected": True, "tasks": True}]
RANGE = (date(2026, 12, 1), date(2027, 4, 1))
a = A.App()
fake = FakeG()
a.gcal = fake
a.outbox.gcal = fake
a.store.set(CALS, [], {}, RANGE)
a._last_sync = __import__("datetime").datetime.now()
w = a.win

# ---------- 2: '새 할 일' 창에서 반복을 고르면 반복 일정으로 ----------
a.new_task(FRI)
dlg = a._dialogs[-1]
dlg.title.setText("손톱 깎기")
dlg.repeat.setCurrentIndex(dlg.repeat.findData("weekly"))
check("2 반복을 고르면 '목록' 줄이 숨음", not dlg.list.isVisibleTo(dlg))
dlg._save()
shown = any(e.title == "손톱 깎기" and e.todo and e.first_day == FRI + timedelta(days=7) for e in a.store.events)
made = jobs_named("create_event")
check("2 반복을 고르면 구글 할 일 대신 반복 일정", made and made[-1][0]["todo"] is True
      and made[-1][0]["recurrence"] == "RRULE:FREQ=WEEKLY;BYDAY=FR" and made[-1][0]["calendar_id"] == "me"
      and made[-1][0]["start"] == FRI and not jobs_named("create_task"), made)
check("2 반복 할 일은 이 PC의 예전 반복 기억에 넣지 않음", a.cfg["task_repeats"] == {}, a.cfg["task_repeats"])
check("2 저장하자마자 다음 회차도 보임", shown)
check("2 되돌리기는 반복 일정 지우기", a.undo_stack[-1]["jobs"][0][0] == "delete_event")

a.new_task(FRI)
dlg = a._dialogs[-1]
dlg.title.setText("장보기")
check("2 반복 안 함이면 '목록' 줄이 보임", dlg.list.isVisibleTo(dlg))
dlg._save()
check("2 반복 안 함이면 지금처럼 구글 할 일", [x[1] for x in jobs_named("create_task")] == ["장보기"],
      jobs_named("create_task"))

plain = Event("task:L1:p1", "tasks:L1", "물 주기", FRI, FRI + timedelta(days=1), True, task_list="L1")
a.store.events.append(plain)
a._open_task_dialog(plain)
dlg = a._dialogs[-1]
dlg.repeat.setCurrentIndex(dlg.repeat.findData("monthly"))
dlg.action = "save"
dlg.accepted.emit()
gone = not any(e.id == "task:L1:p1" for e in a.store.events)  # 동기화가 화면을 다시 채우기 전에 본다
conv = jobs_named("task_to_todo")
check("2 보통 할 일을 반복으로 바꾸면 반복 일정을 만들고 그 할 일은 지움(한 작업: 만든 뒤에만 지움)",
      len(conv) == 1 and conv[0][0].id == "task:L1:p1"
      and conv[0][1]["recurrence"] == "RRULE:FREQ=MONTHLY;BYMONTHDAY=15"
      and conv[0][1]["title"] == "물 주기" and conv[0][1]["todo"] and conv[0][1].get("event_id"), conv)
g, svc = fresh()
g.task_to_todo(plain, dict(todo_values("물 주기", "", FRI, "monthly", "me"), event_id="abc"))
check("2 구글에는 반복 일정을 먼저 만들고 그다음 할 일을 지움",
      [k for k, _ in svc.log if k in ("events.insert", "tasks.delete")] == ["events.insert", "tasks.delete"], svc.log)
check("2 바꾼 할 일은 화면에서도 바로 빠짐", gone)
check("2 되돌리기: 반복 일정 지우고 할 일 되살리기",
      [j[0] for j in a.undo_stack[-1]["jobs"]] == ["delete_event", "create_task"], a.undo_stack[-1]["jobs"])

# ---------- 3: 칸에 ☐로 보이고 눌러서 그 회차 완료 ----------
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QPushButton
from xetical.view import tooltip_text

pump(lambda: len(a.outbox) == 0)


def todo_ev(eid, d, title="손톱 깎기", **kw):
    return Event(eid, "me", title, d, d + timedelta(days=1), True, recurring=True, recurring_event_id=eid.split("_")[0],
                 todo=True, **kw)


todo = todo_ev("rt_20270115", FRI)
plain_rec = Event("rm_20270115", "me", "주간 회의", FRI, FRI + timedelta(days=1), True, recurring=True,
                  recurring_event_id="rm")
a.store.set(CALS, [todo, plain_rec], {}, RANGE)
check("3 칸 글자: 반복 할 일만 ☐", w.item_text(todo, FRI) == "☐ 손톱 깎기" and w.item_text(plain_rec, FRI) == "주간 회의")
w.go_to(FRI)
w.grab()
check("3 (Review 4) ☐ 누르는 자리는 반복 할 일에만",
      any(ev is todo for _r, ev in w._check_hits) and not any(ev is plain_rec for _r, ev in w._check_hits))
tip = tooltip_text(todo, a.store, a.cfg)
check("3 풍선 도움말", "↻ 반복 할 일" in tip and "01/15 마감 · 할 일" in tip, tip)
check("3 보통 반복 일정 풍선 도움말은 그대로", "↻ 반복 일정" in tooltip_text(plain_rec, a.store, a.cfg))

n = len(fake.calls)
a.toggle_task(todo)
done_jobs = jobs_named("set_event_done")
check("3 ☐ 누르면 그 회차만 완료", todo.done and done_jobs and done_jobs[-1][0].id == "rt_20270115"
      and done_jobs[-1][1] is True, done_jobs)
check("3 되돌리기", a.undo_stack[-1]["jobs"][0][0] == "set_event_done" and a.undo_stack[-1]["jobs"][0][1][1] is False)
check("3 칸 글자: 완료면 ☑", w.item_text(todo, FRI) == "☑ 손톱 깎기")
g, svc = fresh()
g.set_event_done(todo, True)
p = private(svc.calls("events.patch")[0]["body"])
check("3 구글에는 그 회차 완료 + 할 일 표시 유지", svc.calls("events.patch")[0]["eventId"] == "rt_20270115"
      and p.get("xeticalDone") == "1" and p.get("xeticalTodo") == "1", p)
tmp_todo = todo_ev("tmp-abc_20270122", FRI + timedelta(days=7))
n = len(a.outbox.jobs) + len(fake.calls)
a.toggle_task(tmp_todo)
check("3 (Review 1) 동기화 전 회차의 ☐는 무시", not tmp_todo.done and len(a.outbox.jobs) + len(fake.calls) == n)

a.cfg["show_done_tasks"] = False
check("3 (Review 5) 완료한 할 일 표시를 끄면 완료 회차가 숨음", todo not in w.cell_events(FRI))
a.cfg["show_done_tasks"] = True
todo.done = False
a.cfg["show_tasks"] = False
check("3 (Review 5) 할 일 표시를 끄면 반복 할 일도 숨음(보통 반복 일정은 그대로)",
      todo not in w.cell_events(FRI) and plain_rec in w.cell_events(FRI))
a.cfg["show_tasks"] = True

ed = __import__("xetical.editor", fromlist=["CellEditor"]).CellEditor(a.cfg, a.store, FRI, QRect(0, 0, 160, 120), "me", [])
items = [b.text() for b in ed.findChildren(QPushButton) if b.objectName() == "item"]
check("3 칸 편집기: 반복 할 일은 위쪽 목록에 ☐로(한 줄로 고치지 않음)",
      items == ["☐ 손톱 깎기"] and ed.edit.toPlainText() == "주간 회의", (items, ed.edit.toPlainText()))
ed.close()

texts = []
orig_add = a._add
a._add = lambda m, text, fn, checked=None, enabled=True: (texts.append(text), orig_add(m, text, fn, checked, enabled))[1]
real_menu = A.QMenu


class NoExecMenu(real_menu):  # 메뉴를 띄우지 않고(시험이 멈추지 않게) 항목만 만든다
    def exec(self, *args):
        return None


A.QMenu = NoExecMenu
a.show_event_menu(todo, FRI, w.mapToGlobal(w.rect().center()))
A.QMenu = real_menu
check("3 오른쪽 클릭 메뉴에 완료 표시", "완료로 표시" in texts, texts)
a._add = orig_add

a.open_day_view(FRI)
btn = next((b for b in a._dayview.findChildren(QPushButton) if b.text() == "☐ 손톱 깎기"), None)
btn and btn.click()
check("3 하루 상세 보기: 누르면 ☐가 바뀜", btn is not None and todo.done)
a._dayview.close()
pump(lambda: len(a.outbox) == 0)

# 아침 요약: 오늘 회차는 할 일로, 지난 30일 안의 미완료 회차는 '(마감 지남)'으로
T = date.today()
a.store.set(CALS, [todo_ev("s1_a", T, "오늘 할 일"), todo_ev("s2_a", T - timedelta(days=10), "지난 할 일"),
                   todo_ev("s3_a", T - timedelta(days=40), "오래된 할 일"),
                   todo_ev("s4_a", T - timedelta(days=10), "끝낸 할 일", done=True),
                   Event("rm_x", "me", "회의", T, T + timedelta(days=1), True, recurring=True, recurring_event_id="rm")],
            {}, (T - timedelta(days=60), T + timedelta(days=90)))
captured = []


class _Popup:
    def show(self):
        pass


R.SummaryPopup = lambda title, lines, on_open: (captured.append((title, [t for t, _c in lines])), _Popup())[1]
a.reminders.check_summary(force=True)
title, lines = captured[-1]
past = T - timedelta(days=10)
check("3 아침 요약: 오늘 회차는 할 일, 지난 30일 안 미완료는 마감 지남",
      "☐ 오늘 할 일" in lines and f"☐ 지난 할 일  (마감 {past:%m/%d} 지남)" in lines
      and not any("오래된" in t or "끝낸" in t for t in lines) and "할 일 2개" in title and "일정 1개" in title,
      (title, lines))

# ---------- 4: '할 일 수정' 창으로 고치기·지우기 ----------
import xetical.editing as ED
import xetical.tasks as TK

pump(lambda: len(a.outbox) == 0)
a.store.set(CALS, [], {}, RANGE)
RULE = "RRULE:FREQ=WEEKLY;BYDAY=FR"
asked, answer = [], ["following"]
TK.ask_scope = ED.ask_scope = lambda parent, verb, allow_one=True: (asked.append((verb, allow_one)), answer[0])[1]
ED.ask_confirm = lambda *args, **kw: True


def opened(rrule=RULE, ev=None):
    asked.clear()
    ev = ev or todo_ev("rt_20270115", FRI)
    a._open_todo_dialog(ev, rrule)
    return ev, a._dialogs[-1]


def save(dlg, action="save"):
    dlg.action = action
    dlg.accepted.emit()
    pump(lambda: len(a.outbox) == 0)


ev, dlg = opened()
check("4 창의 반복 칸에 지금 규칙", dlg.repeat.currentData() == "weekly" and not dlg.list.isVisibleTo(dlg))
n_upd = len(jobs_named("update_event"))
dlg.done_box.setChecked(True)
save(dlg)
check("4 완료만 바꾸면 묻지 않고 그 회차만", asked == [] and ev.done and jobs_named("set_event_done")[-1][1] is True
      and len(jobs_named("update_event")) == n_upd, asked)

ev, dlg = opened()
answer[0] = "following"
dlg.title.setText("발톱 깎기")
dlg.due.setDate(dlg.due.date().addDays(1))
save(dlg)
u = jobs_named("update_event")[-1]
check("4 이후 범위로 날짜를 바꾸면 규칙도 새 요일", asked == [("수정", True)] and u[2] == "following"
      and u[1]["recurrence"] == "RRULE:FREQ=WEEKLY;BYDAY=SA" and u[1]["todo"] and u[1]["title"] == "발톱 깎기"
      and u[1]["start"] == FRI + timedelta(days=1) and u[1]["all_day"], (asked, u[1:]))

ev, dlg = opened()
answer[0] = "one"
dlg.due.setDate(dlg.due.date().addDays(2))
save(dlg)
u = jobs_named("update_event")[-1]
check("4 '이번만' 날짜 바꾸기는 그 회차만 옮기고 규칙은 그대로", u[2] == "one" and u[1]["recurrence"] is None
      and u[1]["start"] == FRI + timedelta(days=2), u[1:])

ev, dlg = opened()
answer[0] = "all"
dlg.repeat.setCurrentIndex(dlg.repeat.findData("monthly"))
save(dlg)
u = jobs_named("update_event")[-1]
check("4 반복을 바꾸면 '이번만' 없이 묻고 새 규칙", asked == [("수정", False)] and u[2] == "all"
      and u[1]["recurrence"] == "RRULE:FREQ=MONTHLY;BYMONTHDAY=15", (asked, u[1:]))

ev, dlg = opened()
n_del = len(jobs_named("delete_event"))
dlg.repeat.setCurrentIndex(dlg.repeat.findData("none"))
save(dlg)
d = jobs_named("delete_event")
check("4 반복을 '안 함'으로 바꾸면 묻지 않고 이 회차부터 끝냄", asked == [] and len(d) == n_del + 1
      and d[-1][0].id == "rt_20270115" and d[-1][1] == "following", (asked, d[-1:]))

ev, dlg = opened()
answer[0] = "all"
save(dlg, "delete")
d = jobs_named("delete_event")
check("4 삭제는 범위를 물어 그 범위로", asked == [("삭제", True)] and d[-1][1] == "all", (asked, d[-1:]))

ev, dlg = opened("RRULE:FREQ=WEEKLY;BYDAY=MO,WE")
check("4 (Review 2) 휴대폰에서 바꾼 규칙은 '기존 규칙 유지'", dlg.repeat.currentData() == "keep")
answer[0] = "all"
dlg.title.setText("손발톱 깎기")
save(dlg)
u = jobs_named("update_event")[-1]
check("4 (Review 2) 제목만 바꾸면 규칙은 그대로", u[2] == "all" and u[1]["recurrence"] is None
      and u[1]["title"] == "손발톱 깎기", u[1:])

fake.rrule = RULE
n_dlg = len(a._dialogs)
a.edit_event(todo_ev("rt_20270122", FRI + timedelta(days=7)))
pump(lambda: len(a._dialogs) > n_dlg)
check("4 반복 할 일을 열면 규칙을 읽어 '할 일 수정' 창", type(a._dialogs[-1]).__name__ == "TaskDialog"
      and a._dialogs[-1].repeat.currentData() == "weekly")
n_dlg = len(a._dialogs)
a.edit_event(plain_rec)
pump(lambda: len(a._dialogs) > n_dlg)
check("4 (Review 4) 보통 반복 일정은 지금처럼 일정 창", type(a._dialogs[-1]).__name__ == "EventDialog")

a.clip = todo_ev("rt_20270115", FRI, description="메모")
a.paste(FRI + timedelta(days=3))
c = jobs_named("create_task")[-1]
check("4 붙여넣기는 그 날짜에 반복 없는 구글 할 일", c == ("L1", "손톱 깎기", "메모", FRI + timedelta(days=3)), c)

target = todo_ev("rt_20270129", FRI + timedelta(days=14))
a.store.events.append(target)
a.win.selected_event = target
answer[0] = "one"
asked.clear()
a.delete_selected()
d = jobs_named("delete_event")
check("4 Delete 키는 범위를 물어 지움", asked == [("삭제", True)] and d[-1][0].id == "rt_20270129" and d[-1][1] == "one")
asked.clear()
a.move_event(todo_ev("rt_20270205", FRI + timedelta(days=21)), 2)
mv = jobs_named("move_event")
check("4 끌어서 옮기기는 그 회차만, 묻지 않음", asked == [] and mv[-1][0].id == "rt_20270205" and mv[-1][1] == 2)

# ---------- 5: 지금 있는 반복 할 일 자동으로 옮기기 ----------
import json

TODAY = date(2027, 1, 20)


def task_item(tid, due, done=False, title="손톱 깎기", notes=""):
    return {"id": tid, "title": title, "notes": notes, "due": f"{due.isoformat()}T00:00:00.000Z",
            "status": "completed" if done else "needsAction"}


def mig(items, series=False):
    g, svc = fresh()
    svc.task_items = lambda kw: items
    svc.lists = lambda kw: ([{"id": "s1", "summary": "손톱 깎기", "recurrence": [RULE]}]
                            if series and kw.get("privateExtendedProperty") == "xeticalTodo=1" else [])
    return g, svc


def deleted(svc):
    return [kw["task"] for kw in svc.calls("tasks.delete")]


g, svc = mig([task_item("new_task_id", FRI, notes="메모"), task_item("old", date(2027, 1, 8), done=True)])
r = g.migrate_repeat("L1", "손톱 깎기", "weekly", "me", TODAY)
ins = svc.calls("events.insert")
check("5 미완료 최신 회차부터 시작하고 그 할 일은 지움(지난 완료 기록은 둠)", r == "created" and len(ins) == 1
      and ins[0]["body"]["start"] == {"date": "2027-01-15"} and ins[0]["body"]["recurrence"] == [RULE]
      and private(ins[0]["body"]).get("xeticalTodo") == "1" and ins[0]["body"]["description"] == "메모"
      and ins[0]["calendarId"] == "me" and deleted(svc) == ["new_task_id"], (r, ins, deleted(svc)))
check("5 할 일은 날짜 제한 없이 찾음", svc.calls("tasks.list")
      and all("dueMin" not in kw and "dueMax" not in kw and kw["tasklist"] == "L1" for kw in svc.calls("tasks.list")))
g, svc = mig([task_item("done1", date(2026, 8, 7), done=True)])
r = g.migrate_repeat("L1", "손톱 깎기", "weekly", "me", TODAY)
ins = svc.calls("events.insert")
check("5 최신이 완료면 오늘 이후 첫 회차부터, 할 일은 안 지움", r == "created"
      and ins[0]["body"]["start"] == {"date": "2027-01-22"} and deleted(svc) == [], (ins, deleted(svc)))
g, svc = mig([task_item("other", FRI, title="장보기")])
check("5 그 제목의 할 일이 없으면 아무것도 안 만듦",
      g.migrate_repeat("L1", "손톱 깎기", "weekly", "me", TODAY) == "none" and not svc.calls("events.insert"))
g, svc = mig([task_item("new_task_id", FRI)], series=True)
r = g.migrate_repeat("L1", "손톱 깎기", "weekly", "me", TODAY)
check("5 (Review 3) 이미 옮긴 반복 일정이 있으면 다시 만들지 않고 할 일만 지움(다른 PC·다시 보내기)",
      r == "exists" and not svc.calls("events.insert") and deleted(svc) == ["new_task_id"], (r, svc.log))

pump(lambda: len(a.outbox) == 0)
a.cfg["task_repeats"] = {"L1|손톱 깎기": "weekly", "L1|월세 납부일": "monthly"}
a._on_synced((CALS, [], {}, RANGE, [], "scope"), None)
check("5 할 일을 못 읽은 동기화에서는 옮기지 않음", len(a.cfg["task_repeats"]) == 2)
n = len(jobs_named("migrate_repeat"))
a._on_synced((CALS, [], {}, RANGE, [], "ok"), None)
m = jobs_named("migrate_repeat")[n:]
check("5 첫 동기화 때 한 번 옮기고 예전 기억은 지움",
      sorted(x[:4] for x in m) == [("L1", "손톱 깎기", "weekly", "me"), ("L1", "월세 납부일", "monthly", "me")]
      and all(x[4] == date.today() for x in m) and a.cfg["task_repeats"] == {}, (m, a.cfg["task_repeats"]))
n = len(jobs_named("migrate_repeat"))
a._on_synced((CALS, [], {}, RANGE, [], "ok"), None)
check("5 다음 동기화에서는 다시 옮기지 않음", len(jobs_named("migrate_repeat")) == n)
bk = SP / "old_backup.json"
bk.write_text(json.dumps({"task_repeats": {"L1|손톱 깎기": "weekly"}}, ensure_ascii=False), "utf-8")
a.import_settings_file(str(bk))
check("5 예전 백업을 가져와도 반복 기억은 들어오지 않음(이미 옮긴 반복을 또 옮기지 않게)", a.cfg["task_repeats"] == {})

print(f"\n{sum(ok for _, ok in results)}/{len(results)} 통과")
