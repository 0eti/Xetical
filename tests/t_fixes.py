"""리뷰(2026-10-21)에서 찾은 문제 A1~A6, B7~B12가 고쳐졌는지 보는 시험. 구글 대신 가짜를 쓴다."""
import sys
import time
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, ".")
SP = Path(sys.argv[1])
from PySide6.QtCore import QDate, QRect, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

qapp = QApplication([])
import xetical.config as C
C.Config.save = lambda self: None
import xetical.jobs as J
J.OUTBOX_PATH = SP / "outbox_f.json"
J.OUTBOX_PATH.unlink(missing_ok=True)
import xetical.model as M
M.CACHE_PATH = SP / "cache_f.json"
import xetical.reminders as R
R.FIRED_PATH = SP / "fired_f.json"
import xetical.app as A
A.CELLS_PATH = SP / "cells_f.json"
import httplib2
from googleapiclient.errors import HttpError
from xetical.gcal import GCal
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


# ---------------------------------------------------------------- 가짜 구글 API (gcal.py 안쪽 시험용)
METHODS = {"insert": "POST", "patch": "PATCH", "delete": "DELETE", "update": "PUT"}


class Req:
    def __init__(self, svc, kind, kw):
        self.svc, self.kind, self.kw = svc, kind, kw
        self.method = METHODS.get(kind.split(".")[1], "GET")

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
            try:
                self.cb(rid, r.execute(), None)
            except HttpError as e:
                self.cb(rid, None, e)


class FakeSvc:
    def __init__(self):
        self.log, self.existing, self.fail = [], set(), {}
        self.master = {"id": "m1", "start": {"dateTime": "2026-11-02T10:00:00+09:00"},
                       "end": {"dateTime": "2026-11-02T11:00:00+09:00"},
                       "recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=MO;COUNT=10"]}
        self.instances = 3

    def __getattr__(self, name):
        return lambda: Coll(self, name)

    def new_batch_http_request(self, callback):
        return Batch(callback)

    def handle(self, kind, kw):
        self.log.append((kind, kw))
        if kind in self.fail:
            raise HttpError(Resp(self.fail[kind]), b'{"error":"x"}')
        if kind == "settings.get":
            return {"value": "Asia/Seoul"}
        if kind == "events.get":
            return self.master
        if kind == "events.instances":
            return {"items": [{}] * self.instances}
        if kind == "events.patch" and "recurrence" in kw["body"] and kw["eventId"] == "m1":
            self.master = dict(self.master, recurrence=kw["body"]["recurrence"])
        if kind == "events.insert":
            body_id = kw["body"].get("id")
            if body_id in self.existing:
                raise HttpError(Resp(409), b'{"error":"duplicate"}')
            if body_id:
                self.existing.add(body_id)
            return {"id": body_id or "new1"}
        return {}

    def bodies(self, kind):
        return [kw.get("body") for k, kw in self.log if k == kind]


def fresh():
    g, svc = GCal(), FakeSvc()
    g._service = lambda: svc
    return g, svc


S0 = datetime(2026, 11, 16, 10, 0)
inst = Event("i1", "me", "운동", S0, S0 + timedelta(hours=1), False, recurring=True, recurring_event_id="m1")


def vals(**kw):
    v = {"title": "운동", "all_day": False, "calendar_id": "me", "start": S0, "end": S0 + timedelta(hours=1),
         "description": "", "location": "", "alarm": "", "countdown": "", "recurrence": None}
    v.update(kw)
    return v


# ---------- A1: 시간 일정에 시간대 ----------
g, svc = fresh()
g.create_event(vals(recurrence="RRULE:FREQ=WEEKLY;BYDAY=MO"))
b = svc.bodies("events.insert")[0]
check("A1 시간 반복 일정 만들 때 시간대(timeZone) 포함", b["start"].get("timeZone") == "Asia/Seoul"
      and b["end"].get("timeZone") == "Asia/Seoul", b)
g.create_event(vals(all_day=True, start=S0.date(), end=S0.date()))
check("A1 종일 일정에는 시간대 없음", "timeZone" not in svc.bodies("events.insert")[1]["start"])

# ---------- A2: '이번과 이후' / '전체'의 반복 규칙 ----------
g, svc = fresh()
g.update_event(inst, vals(), "following")
new = svc.bodies("events.insert")[0]
check("A2 이후: 규칙 그대로면 COUNT를 남은 횟수로(10-3=7)", new.get("recurrence") == ["RRULE:FREQ=WEEKLY;BYDAY=MO;COUNT=7"], new)
cut = [b_ for b_ in svc.bodies("events.patch") if "recurrence" in (b_ or {})][0]["recurrence"][-1]
check("A2 이후: 앞쪽 규칙은 이 회차 전에서 끝남", "UNTIL=20261116T005959Z" in cut and "COUNT" not in cut, cut)

g, svc = fresh()
svc.master["recurrence"] = ["RRULE:FREQ=WEEKLY;BYDAY=MO;UNTIL=20261231T145959Z"]
g.update_event(inst, vals(), "following")
check("A2 이후: 종료일(UNTIL)은 지킴", svc.bodies("events.insert")[0].get("recurrence")
      == ["RRULE:FREQ=WEEKLY;BYDAY=MO;UNTIL=20261231T145959Z"], svc.bodies("events.insert"))

g, svc = fresh()
g.update_event(inst, vals(recurrence=""), "following")
check("A2 이후 + '반복 안 함': 새 일정은 반복하지 않음", "recurrence" not in svc.bodies("events.insert")[0])

g, svc = fresh()
g.update_event(inst, vals(recurrence="RRULE:FREQ=DAILY;COUNT=3"), "following")
check("A2 이후 + 새 규칙: 사용자가 정한 그대로", svc.bodies("events.insert")[0]["recurrence"] == ["RRULE:FREQ=DAILY;COUNT=3"])

g, svc = fresh()
g.update_event(inst, vals(recurrence=""), "all")
check("A2 전체 + '반복 안 함': 반복을 없앰", svc.bodies("events.patch")[0].get("recurrence") == [], svc.bodies("events.patch"))
g, svc = fresh()
g.update_event(inst, vals(title="수영"), "all")
check("A2 전체 + 규칙 그대로(None): 반복은 건드리지 않음", "recurrence" not in svc.bodies("events.patch")[0])

# ---------- A5: 휴대폰에서 건 알림 지키기 ----------
g, svc = fresh()
plain = Event("p1", "me", "회의", S0, S0 + timedelta(hours=1), False, popup_minutes=[10])
g.update_event(plain, vals(title="회의 (변경)", recurrence=""))
check("A5 Xetical 알림 없는 일정 수정: 구글 알림(reminders)은 건드리지 않음", "reminders" not in svc.bodies("events.patch")[0])
had = Event("p2", "me", "회의", S0, S0 + timedelta(hours=1), False, alarm="09:30|0")
g.update_event(had, vals(recurrence=""))
check("A5 Xetical 알림을 지우면 구글 기본 알림으로", svc.bodies("events.patch")[1].get("reminders", {}).get("useDefault") is True)

# ---------- A6: 구글 앱에서 완료한 할 일 ----------
g, svc = fresh()
g._tasks = lambda: svc
GCal.has_tasks_scope = staticmethod(lambda: True)
svc.tasklists = lambda: Coll(svc, "tasklists")
orig = svc.handle
svc.handle = lambda kind, kw: {"items": [{"id": "L", "title": "내 할 일"}]} if kind == "tasklists.list" else orig(kind, kw)
g.fetch_tasks(date(2026, 11, 1), date(2026, 12, 1))
check("A6 할 일 받을 때 숨겨진(완료된) 것도 받음", [kw for k, kw in svc.log if k == "tasks.list"][0].get("showHidden") is True)

# ---------- B9: 일괄 저장 다시 보내기 ----------
g, svc = fresh()
ops = [("create", "me", vals(all_day=True, start=S0.date(), end=S0.date(), event_id="aaa111")),
       ("patch", "me", "x1", {"summary": "y"})]
ids = g.apply_ops(ops)
ids2 = g.apply_ops(ops)  # 같은 작업을 다시 보냄(앞에서 연결이 끊겼다고 가정)
check("B9 다시 보내도 같은 id면 409 → 성공, 한 번만 만들어짐", ids == ids2 == ["aaa111"] and len(svc.existing) == 1, (ids, ids2))
svc.fail = {"events.insert": 404}
try:
    g.apply_ops([("create", "gone-cal", vals(event_id="bbb222"))])
    ok = False
except HttpError as e:
    ok = e.resp.status == 404
check("B9 만들기가 404면 실패로 올림(조용히 사라지지 않게)", ok)
svc.fail = {"events.delete": 404}
check("B9 지우기가 404면 성공으로 침", g.apply_ops([("delete", "me", "old")]) == [])
svc.fail = {"events.patch": 503, "events.delete": 400}
try:
    g.apply_ops([("delete", "me", "d"), ("patch", "me", "p", {})])
    st = None
except HttpError as e:
    st = e.resp.status
check("B9 실패가 섞이면 다시 보낼 오류(503)를 먼저 올림", st == 503, st)

# ---------------------------------------------------------------- 앱 수준 (가짜 GCal 객체)
class FakeG:
    def __init__(self):
        self.calls, self.mode, self.n = [], None, 0

    def __getattr__(self, name):
        def fn(*args):
            if self.mode == "offline":
                raise httplib2.ServerNotFoundError("offline")
            if self.mode == "gone":
                raise HttpError(Resp(404), b'{"error":"gone"}')
            self.calls.append((name,) + args)
            if name == "create_event":
                return args[0].get("event_id") or "real"
            if name == "apply_ops":
                return [op[2].get("event_id") for op in args[0] if op[0] == "create"]
            if name == "fetch_all":
                return CALS, [], {}, (args[0], args[1]), [], "ok"
            return None
        return fn

    def has_token(self):
        return True

    def has_client_secret(self):
        return True


T = date.today()
CALS = [{"id": "me", "summary": "캘린더", "color": "#4a90d9", "primary": True, "writable": True, "selected": True},
        {"id": "ro", "summary": "읽기 전용", "color": "#999999", "primary": False, "writable": False, "selected": True}]
BASE = [
    Event("g1", "me", "운동", T, T + timedelta(days=1), True, line=True, done=True, text_color="#ff0000"),
    Event("x1", "me", "가", T + timedelta(days=1), T + timedelta(days=2), True),
    Event("x2", "me", "나", T + timedelta(days=1), T + timedelta(days=2), True),
    Event("ro1", "ro", "공유", T, T + timedelta(days=1), True, writable=False),
]
a = A.App()
fake = FakeG()
a.gcal = fake
a.outbox.gcal = fake
a.store.set(CALS, list(BASE), {}, (T - timedelta(days=60), T + timedelta(days=90)))
a._last_sync = datetime.now()

# ---------- B9: 보낼 편지함이 id를 미리 정함 ----------
fake.mode = "offline"
a.outbox.add("create_event", {"title": "새", "calendar_id": "me"}, tmps=["tmp-z"], label="새 일정")
job = a.outbox.jobs[-1]
eid = job["args"][0].get("event_id", "")
check("B9 새 일정 작업에 id를 미리 붙이고 임시 id와 바로 짝지음",
      len(eid) == 32 and all(c in "0123456789abcdef" for c in eid) and a.outbox.real_id("tmp-z") == eid, eid)
a.outbox.retry.stop()
a.outbox.jobs.clear()
a.outbox.busy = False
fake.mode = "gone"
failed = []
a.outbox.failed.disconnect()
a.outbox.failed.connect(lambda label, err: failed.append(label))
a.outbox.add("create_task", "L1", "할 일", "", T, tmps=["tmp-t"], label="새 할 일")
pump(lambda: len(a.outbox) == 0)
check("B9 편지함: 만들기가 404면 버리지 않고 실패로 알림", failed == ["새 할 일"], failed)
fake.mode = None

# ---------- A4: 동기화 도중 생긴 변경을 덮어쓰지 않음 ----------
a.store.events.append(Event("tmp-local", "me", "방금 만든 것", T, T + timedelta(days=1), True))
a.store.reindex()
a.outbox.jobs.append({"op": "noop", "args": [], "tmps": [], "label": "x"})
a._syncing = True
a._on_synced((CALS, [], {}, (T, T), [], "ok"), None)
check("A4 보낼 변경이 남아 있으면 받은 결과로 덮어쓰지 않음", any(e.id == "tmp-local" for e in a.store.events))
a.outbox.jobs.clear()
a._on_synced((CALS, list(BASE), {}, (T - timedelta(days=60), T + timedelta(days=90)), [], "ok"), None)
check("A4 보낼 변경이 없으면 받은 결과를 반영", not any(e.id == "tmp-local" for e in a.store.events))

# ---------- B8: Delete·Ctrl+X 확인 ----------
calls = []
a.delete_event = lambda ev, confirm=False: calls.append(ev.id)
virt = Event("v1", "tasks:L1", "예정", T, T + timedelta(days=1), True, task_list="L1", virtual=True)
for ev in (virt, BASE[3]):
    a.win.selected_event = ev
    a.delete_selected()
    a.cut_selected()
check("B8 '예정'과 읽기 전용 일정은 Delete·Ctrl+X로 지우지 않음", calls == [], calls)
a.win.selected_event = BASE[1]
a.delete_selected()
check("B8 보통 일정은 Delete로 지움", calls == ["x1"], calls)
del a.delete_event

# ---------- A3 / B7: 칸 편집기 ----------
from xetical.editor import CellEditor
ed = CellEditor(a.cfg, a.store, T, QRect(0, 0, 160, 120), "me", [])
ed.show()
ed.edit.setFocus()
c = ed.edit.textCursor()
c.movePosition(QTextCursor.Start)
ed.edit.setTextCursor(c)
QTest.keyClick(ed.edit, Qt.Key_Return)
c = ed.edit.textCursor()
c.movePosition(QTextCursor.Start)
ed.edit.setTextCursor(c)
ed.edit.insertPlainText("치과")
ops, lines, ask = ed.collect()
gym = next((ln for ln in lines if ln["text"] == "운동"), None)
check("A3 줄 맨 앞 Enter: 기존 일정·완료·색은 원래 줄에 남음", gym and gym["ev"] is not None and gym["ev"].id == "g1"
      and gym["done"] and gym["color"] == "#ff0000", lines)
check("A3 줄 맨 앞 Enter: 위에 쓴 줄은 새 일정", any(op[0] == "create" and op[2]["title"] == "치과" for op in ops)
      and not any(op[0] == "patch" and "summary" in op[3] and op[3]["summary"] == "치과" for op in ops), ops)
ed.close()

ed2 = CellEditor(a.cfg, a.store, T + timedelta(days=1), QRect(0, 0, 160, 120), "me", [])
ops2, _l, ask2 = ed2.collect()
check("B7 열었다 닫기만 하면 구글에 쓰지 않음(순서 번호가 같은 두 일정)", ops2 == [] and ask2 == [], ops2)
ed2.close()

# ---------- B10: 반복 종료일(UNTIL) ----------
from xetical.dialogs import RepeatEditor
rep = RepeatEditor()
start = date(2026, 11, 2)
rep.load("", start)
rep.kind.setCurrentIndex(rep.kind.findData("daily"))
rep.end_kind.setCurrentIndex(rep.end_kind.findData("until"))
rep.until.setDate(QDate(2026, 11, 30))
rule = rep.rrule(start, False)
u = rule.split("UNTIL=")[1]
local_end = datetime.strptime(u, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).astimezone()
check("B10 시간 일정 종료일: 이 PC 시각으로 그날 밤 23:59:59", (local_end.date(), local_end.hour) == (date(2026, 11, 30), 23), rule)
rep2 = RepeatEditor()
rep2.load(rule, start)
check("B10 저장한 종료일을 다시 열면 같은 날짜", rep2.until.date() == QDate(2026, 11, 30), rep2.until.date())
check("B10 종일 일정 종료일은 날짜만", rep.rrule(start, True).endswith("UNTIL=20261130"))

# ---------- B12: 떨어진 마우스 훅 다시 걸기 ----------
from xetical.mousehook import ClickThrough
hook = ClickThrough(a.win)
hook.start()
first = hook.hook
hook._calls, hook._cursor = 0, (-99999, -99999)  # 커서는 움직였는데 훅이 한 번도 불리지 않은 상태
hook.check()
check("B12 커서가 움직였는데 훅이 불리지 않았으면 다시 검", hook.hook and hook._cursor != (-99999, -99999))
before = hook.hook
hook._calls = 5
hook.check()
check("B12 훅이 살아 있으면 그대로 둠", hook.hook == before)
hook.stop()

# ---------- B11: Xetical.bat ----------
bat = Path("Xetical.bat").read_bytes()
check("B11 bat: CRLF·ASCII 그대로", b"\r\n" in bat and b"\n" not in bat.replace(b"\r\n", b"") and bat.isascii())
check("B11 bat: 설치가 끝났다는 표시 파일로 다시 설치 여부를 정함", b"requirements.installed" in bat and b"fc /b" in bat)

print(f"\n{sum(ok for _, ok in results)}/{len(results)} 통과")
a.shutdown() if hasattr(a, "shutdown") else None
