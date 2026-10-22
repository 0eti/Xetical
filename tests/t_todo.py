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

print(f"\n{sum(ok for _, ok in results)}/{len(results)} 통과")
