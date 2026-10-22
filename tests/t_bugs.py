"""버그 찾기(2026-10-22)에서 고친 12가지 시험. 구글 대신 가짜를 쓴다."""
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, ".")
SP = Path(sys.argv[1])
from PySide6.QtCore import QCoreApplication, QEvent, QPointF, QRect
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication

qapp = QApplication([])
import xetical.config as C
C.Config.save = lambda self: None
C.CONFIG_PATH = SP / "config_none.json"  # 사용자 설정(data/config.json)이 아니라 기본값으로 시험한다
import xetical.jobs as J
J.OUTBOX_PATH = SP / "outbox_b.json"
J.OUTBOX_PATH.unlink(missing_ok=True)
import xetical.model as M
M.CACHE_PATH = SP / "cache_b.json"
import xetical.reminders as R
R.FIRED_PATH = SP / "fired_b.json"
import xetical.mood as MD
MD.MOODS_PATH = SP / "moods_b.json"
import xetical.app as A
A.CELLS_PATH = SP / "cells_b.json"
A.MoodData = lambda: MD.MoodData(SP / "moods_b.json")
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
    """events.get은 원본(master), events.instances는 instances(kw)가 정한 회차들을 돌려준다."""

    def __init__(self):
        self.log = []
        self.master = {"id": "m1", "start": {"dateTime": "2026-11-02T10:00:00+09:00"},
                       "end": {"dateTime": "2026-11-02T11:00:00+09:00"},
                       "recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=MO"]}
        self.instances = lambda kw: []
        self.lists = lambda kw: []

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
        if kind == "colors.get":
            return {"event": {}}
        if kind == "calendarList.list":
            return {"items": [{"id": "me", "summary": "나", "accessRole": "owner", "primary": True}]}
        if kind == "events.insert":
            return {"id": kw["body"].get("id") or "new1"}
        return {}

    def calls(self, kind):
        return [kw for k, kw in self.log if k == kind]


def fresh():
    g, svc = GCal(), FakeSvc()
    g._service = lambda: svc
    return g, svc


def iso(dt):
    return dt.astimezone().isoformat()


S0 = datetime(2026, 11, 16, 10, 0)  # 매주 월요일 10시 반복의 셋째 회차
WEEK = timedelta(days=7)


def inst(**kw):
    return Event("m1_x", "me", "운동", kw.pop("start", S0), kw.pop("end", S0 + timedelta(hours=1)), False,
                 recurring=True, recurring_event_id="m1", **kw)


def vals(**kw):
    v = {"title": "운동", "all_day": False, "calendar_id": "me", "start": S0, "end": S0 + timedelta(hours=1),
         "description": "", "location": "", "alarm": "", "countdown": "", "recurrence": None,
         "done": False, "text_color": ""}
    v.update(kw)
    return v


def private(body):
    return (body or {}).get("extendedProperties", {}).get("private", {})


# ---------- 4: 이 회차의 완료·글자색이 반복 전체로 퍼지지 않음 ----------
g, svc = fresh()
g.update_event(inst(done=True, text_color="#ff0000"), vals(title="수영", done=True, text_color="#ff0000"), "all")
p = private(svc.calls("events.patch")[0]["body"])
check("4 '모든 일정' 이름 바꾸기: 이 회차만의 완료·글자색은 원본에 쓰지 않음",
      p.get("xeticalDone") == "" and p.get("xeticalColor") == "", p)
g, svc = fresh()
svc.master["extendedProperties"] = {"private": {"xeticalColor": "#0000ff"}}
g.update_event(inst(text_color="#0000ff"), vals(text_color="#00ff00"), "all")
p = private(svc.calls("events.patch")[0]["body"])
check("4 '모든 일정'에서 직접 바꾼 글자색은 전체에 씀", p.get("xeticalColor") == "#00ff00", p)
g, svc = fresh()
g.update_event(inst(done=True), vals(title="수영", done=True), "following")
p = private(svc.calls("events.insert")[0]["body"])
check("4 '이번 및 이후': 새 반복은 이 회차의 완료 표시를 물려받지 않음", p.get("xeticalDone") == "", p)

# ---------- 9: 다른 날로 옮긴 회차의 '이번 및 이후' ----------
moved = inst(start=S0 + timedelta(days=2), end=S0 + timedelta(days=2, hours=1), original=S0)
g, svc = fresh()
g.delete_event(moved, "following")
cut = svc.calls("events.patch")[0]["body"]["recurrence"][-1]
until = (S0.astimezone(timezone.utc) - timedelta(seconds=1)).strftime("%Y%m%dT%H%M%SZ")
check("9 옮긴 회차부터 지우기: 원래 자리(월요일) 앞에서 끊어 그 회차도 지움", f"UNTIL={until}" in cut, cut)
g, svc = fresh()
first = datetime.fromisoformat(svc.master["start"]["dateTime"]).astimezone().replace(tzinfo=None)
g.update_event(inst(start=first + timedelta(days=3), end=first + timedelta(days=3, hours=1), original=first),
               vals(title="수영", start=first + timedelta(days=3), end=first + timedelta(days=3, hours=1)), "following")
check("9 뒤로 옮긴 첫 회차의 '이번 및 이후' = 전체 고치기(두 벌로 나누지 않음)",
      not svc.calls("events.insert") and svc.calls("events.patch")[0]["eventId"] == "m1", svc.log)
item = {"id": "m1_x", "status": "confirmed", "summary": "운동", "recurringEventId": "m1",
        "start": {"dateTime": iso(S0 + timedelta(days=2))}, "end": {"dateTime": iso(S0 + timedelta(days=2, hours=1))},
        "originalStartTime": {"dateTime": iso(S0)}}
ev = GCal()._parse(item, {"id": "me", "color": "#000000", "writable": True})
check("9 받은 회차에 원래 자리를 기억", ev.original == S0 and ev.slot == S0 and ev.start != S0, ev)
check("9 원래 자리는 보낼 편지함·캐시에 저장해도 남음",
      Event.from_json(json.loads(json.dumps(ev.to_json()))).original == S0
      and J.decode(json.loads(json.dumps(J.encode([ev]))))[0].original == S0)
check("9 예전 캐시(원래 자리 없음)도 읽힘", Event.from_json({k: v for k, v in Event(
    "a", "me", "t", S0.date(), S0.date() + timedelta(days=1), True).to_json().items() if k != "original"}).original is None)

# ---------- 12: '이번 및 이후'에서 뒤쪽 예외(지운 회차·완료 표시)를 새 반복에 옮김 ----------
g, svc = fresh()
svc.master["extendedProperties"] = {"private": {}}


def old_instances(kw):
    if kw["eventId"] == "m1":
        return [{"id": "c", "status": "cancelled", "originalStartTime": {"dateTime": iso(S0 + WEEK)}},
                {"id": "d", "status": "confirmed", "originalStartTime": {"dateTime": iso(S0 + 2 * WEEK)},
                 "extendedProperties": {"private": {"xeticalDone": "1"}}},
                {"id": "e", "status": "confirmed", "originalStartTime": {"dateTime": iso(S0 + 3 * WEEK)}}]
    return [{"id": f"{kw['eventId']}_n{i}", "originalStartTime": {"dateTime": iso(S0 + i * WEEK)}} for i in range(4)]


svc.instances = old_instances
g.update_event(inst(), vals(title="수영"), "following")
rec = svc.calls("events.insert")[0]["body"]["recurrence"]
ex = (S0 + WEEK).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
check("12 지운 회차는 새 반복에서도 빠짐(EXDATE)", rec[0].startswith("RRULE:") and rec[1:] == [f"EXDATE:{ex}"], rec)
marks = [kw for kw in svc.calls("events.patch") if kw["eventId"].endswith("_n2")]
check("12 완료한 회차는 새 반복의 같은 회차에도 완료로", len(marks) == 1
      and private(marks[0]["body"]).get("xeticalDone") == "1"
      and not any(kw["eventId"].endswith(("_n1", "_n3")) for kw in svc.calls("events.patch")), svc.calls("events.patch"))
g, svc = fresh()
svc.instances = old_instances
g.update_event(inst(), vals(recurrence="RRULE:FREQ=DAILY;COUNT=3"), "following")
check("12 규칙을 바꾸면 예전 예외는 옮기지 않음", svc.calls("events.insert")[0]["body"]["recurrence"]
      == ["RRULE:FREQ=DAILY;COUNT=3"] and not svc.calls("events.instances"))

# ---------- 6: 아주 오래된 'N일 경과' 카운트다운 ----------
g, svc = fresh()
GCal.has_tasks_scope = staticmethod(lambda: False)
OLD = (date.today() - timedelta(days=1000)).isoformat()
OLD2 = (date.today() - timedelta(days=999)).isoformat()


def old_countdowns(kw):
    if kw.get("singleEvents") is False:
        return [{"id": "love", "summary": "사귄 날", "start": {"date": OLD}, "end": {"date": OLD2},
                 "extendedProperties": {"private": {"xeticalCountdown": "memorial|both|passed", "xeticalCD": "1"}}},
                {"id": "exam", "summary": "옛 시험", "start": {"date": OLD}, "end": {"date": OLD2},
                 "extendedProperties": {"private": {"xeticalCountdown": "exam|bar|left", "xeticalCD": "1"}}}]
    return []


svc.lists = old_countdowns
cds = g.fetch_all(date.today(), date.today() + timedelta(days=30), False)[4]
check("6 400일보다 오래된 'N일 경과' 카운트다운도 받음(지난 '남은 날' 카운트다운은 다시 안 나옴)",
      [e.id for e in cds] == ["love"], [e.id for e in cds])
check("6 오래된 것은 반복을 펼치지 않고 받음", all("orderBy" not in kw for kw in svc.calls("events.list")
                                         if kw.get("singleEvents") is False))

# ---------------------------------------------------------------- 앱 수준
class FakeG:
    def __getattr__(self, name):
        def fn(*args):
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

    def has_drive_scope(self):
        return False


T = date.today()
CALS = [{"id": "me", "summary": "캘린더", "color": "#4a90d9", "primary": True, "writable": True, "selected": True},
        {"id": "tasks:L1", "summary": "할 일: 목록", "color": "#4285f4", "primary": False, "writable": False,
         "selected": True, "tasks": True}]
a = A.App()
fake = FakeG()
a.gcal = fake
a.outbox.gcal = fake
a.store.set(CALS, [], {}, (T - timedelta(days=60), T + timedelta(days=90)))
a._last_sync = datetime.now()
w = a.win

# ---------- 1·5: 알림 ----------
hm = f"{(datetime.now() - timedelta(minutes=1)):%H:%M}"
a.store.events.append(Event("al1", "me", "약 먹기", T, T + timedelta(days=1), True, alarm=f"{hm}|0"))
a.reminders.check()
check("1 알림 창이 뜸", len(a.reminders.popups) == 1)
for p_ in list(a.reminders.popups):
    p_.close()
QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
check("1 닫은 알림 창은 목록에서 빠짐", a.reminders.popups == [], a.reminders.popups)
a.store.events.append(Event("al2", "me", "물 마시기", T, T + timedelta(days=1), True, alarm=f"{hm}|0"))
try:
    a.reminders.check()
    err = None
except RuntimeError as e:
    err = e
check("1 알림 창을 닫은 뒤에도 다음 알림이 뜸", err is None and len(a.reminders.popups) == 1
      and a.reminders.popups[0].isVisible(), err)

a.store.events.append(Event("tmp-r1", "me", "새 일정", T, T + timedelta(days=1), True, alarm=f"{hm}|0"))
a.store.events.append(Event("tmp-r2_" + f"{T:%Y%m%d}", "me", "새 반복", T, T + timedelta(days=1), True,
                            alarm=f"{hm}|0", recurring=True))
a.outbox.id_map.update({"tmp-r1": "real1", "tmp-r2": "real2"})  # 편지함이 새 일정 id를 미리 정해 바로 짝짓는다
a.reminders.check()
n = len(a.reminders.popups)
a.store.events = [e for e in a.store.events if not e.id.startswith("tmp-")]
a.store.events += [Event("real1", "me", "새 일정", T, T + timedelta(days=1), True, alarm=f"{hm}|0"),
                   Event(f"real2_{T:%Y%m%d}", "me", "새 반복", T, T + timedelta(days=1), True, alarm=f"{hm}|0",
                         recurring=True, recurring_event_id="real2")]
a.reminders.check()
check("5 임시 id → 진짜 id로 바뀌어도 같은 알림을 또 띄우지 않음(반복 회차 포함)",
      n == 3 and len(a.reminders.popups) == 3, (n, len(a.reminders.popups)))
for p_ in list(a.reminders.popups):
    p_.close()
QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
a.store.events = []
a.store.reindex()

# ---------- 2: 칸 편집기를 열었다 닫기만 하면 고쳐 쓰지 않음 ----------
from xetical.editor import CellEditor
D2 = T + timedelta(days=5)
a.store.events += [Event("sp", "me", "헬스 ", D2, D2 + timedelta(days=1), True),
                   Event("nl", "me", "장보기\n우유", D2, D2 + timedelta(days=1), True, order=1),
                   Event("rc", "me", "요가 ", D2, D2 + timedelta(days=1), True, order=2, recurring=True,
                         recurring_event_id="rcm")]
a.store.reindex()
ed = CellEditor(a.cfg, a.store, D2, QRect(0, 0, 160, 120), "me", [])
ops, lines, ask = ed.collect()
check("2 제목 끝 공백·줄바꿈이 있어도 열었다 닫기만 하면 구글에 쓰지 않음", ops == [] and ask == [], (ops, ask))
check("2 줄바꿈 제목은 한 줄로 보임", ed.edit.document().blockCount() == 3
      and ed.edit.document().findBlockByNumber(1).text() == "장보기 우유")
c = ed.edit.textCursor()
c.movePosition(QTextCursor.Start)
c.movePosition(QTextCursor.EndOfBlock)
c.insertText("장")
ops, lines, ask = ed.collect()
sp = [op for op in ops if op[0] == "patch" and op[2] == "sp"]
check("2 이름을 바꾸면 그 일정만 이름을 고침", sp and sp[0][3].get("summary") == "헬스장"
      and not any("summary" in op[3] for op in ops if op[0] == "patch" and op[2] != "sp"), ops)
ed.close()
a.store.events = []
a.store.reindex()

# ---------- 6: 'N일 경과' 형식의 미래 날짜 ----------
cd = Event("cd", "me", "결혼기념일", T + timedelta(days=64), T + timedelta(days=65), True, countdown="memorial|both|passed")
check("6 'N일 경과' 형식이어도 아직 안 온 날은 남은 날로(막대)", w.countdown_text(cd) == "[기념일] 결혼기념일 64일 남음",
      w.countdown_text(cd))
past = Event("cd2", "me", "사귄 날", T - timedelta(days=5), T - timedelta(days=4), True, countdown="memorial|bar|passed")
check("6 지난 날은 그대로 'N일 경과'", w.countdown_text(past) == "[기념일] 사귄 날 5일 경과", w.countdown_text(past))

# ---------- 7: 전체 월 모드에서 휠 넘기기 ----------
a.cfg["display_mode"], a.cfg["wheel_paging"] = "month", True
w.go_to(date(2026, 10, 15))
for _ in range(4):
    w.on_wheel(QPointF(-500, -500), -120)
check("7 전체 월에서 휠은 한 달씩(1일 기준 유지)", w.anchor == date(2027, 2, 1), w.anchor)
a.cfg["display_mode"], a.cfg["wheel_paging"] = "rolling", False

# ---------- 8: 지나간 주가 최대 행보다 많아도 오늘이 보임 ----------
a.cfg["weeks"], a.cfg["passed_weeks"] = 2, 3
w.go_today()
lo, hi = w.view_range()
check("8 변동 월: 지나간 주 3 / 최대 행 2여도 오늘이 화면 안", lo <= T < hi, (lo, hi))
a.cfg["weeks"], a.cfg["passed_weeks"] = 6, 2
w.go_today()
lo, hi = w.view_range()
check("8 보통 설정은 그대로(지나간 2주 + 오늘 주…)", lo == w.week_first(T) - timedelta(days=14), (lo, hi))
a.cfg["passed_weeks"] = 0

# ---------- 10: 설정 가져오기가 백업 뒤에 만든 트래커를 지우지 않음 ----------
a.cfg["mood_trackers"] = [{"id": "mood", "name": "기분", "colors": []},
                          {"id": "water", "name": "물", "colors": [{"color": "#00b0f0", "label": "마심"}]}]
path = SP / "backup_b.json"
path.write_text(json.dumps({"mood_trackers": [{"id": "mood", "name": "기분(백업)", "colors": []}]}, ensure_ascii=False),
                "utf-8")
a.import_settings_file(str(path))
names = {t["id"]: t["name"] for t in a.cfg["mood_trackers"]}
check("10 백업에 없는 트래커는 남고, 같은 트래커는 백업 쪽", names == {"mood": "기분(백업)", "water": "물"}, names)

# ---------- 11: 잘못된 제목 형식 ----------
from xetical.view import title_text
for bad in ("{year.x}", "{year[0]}", "{year:%Y}", "{"):
    a.cfg["title_format"] = bad
    try:
        txt = title_text(a.cfg)
    except Exception as e:  # noqa: BLE001
        txt = repr(e)
    check(f"11 제목 형식 {bad!r}: 기본 문구로", txt.startswith("오늘은"), txt)
a.cfg["title_format"] = C.DEFAULTS["title_format"]
w.grab()

# ================================================================ 리뷰(2026-10-22)에서 고친 것
# ---------- R1: 매일 반복 + '하루 전·당일' 알림은 같은 시각이어도 두 회차 다 띄움 ----------
a.store.events = [Event(f"rm_{d:%Y%m%d}", "me", "약 먹기", d, d + timedelta(days=1), True, alarm=f"{hm}|1,0",
                        recurring=True, recurring_event_id="rm") for d in (T, T + timedelta(days=1))]
a.reminders.fired = set()
a.reminders.check()
check("R1 매일 반복의 당일 알림과 다음 회차의 하루 전 알림이 둘 다 뜸", len(a.reminders.popups) == 2,
      len(a.reminders.popups))

# ---------- R4: 아래쪽 알림 창을 닫으면 새 창은 그 빈자리에 ----------
bottom = next(p_ for p_ in a.reminders.popups if p_.stack_index == 0)
bottom.close()
QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
a.store.events.append(Event("al3", "me", "새 알림", T, T + timedelta(days=1), True, alarm=f"{hm}|0"))
a.reminders.check()
check("R4 새 알림 창은 남은 창과 겹치지 않음", sorted(p_.stack_index for p_ in a.reminders.popups) == [0, 1],
      [p_.stack_index for p_ in a.reminders.popups])
for p_ in list(a.reminders.popups):
    p_.close()
QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
a.store.events = []
a.store.reindex()

# ---------- R2: '이번 및 이후'에서 뒤쪽의 옮긴 회차는 옮긴 날짜 그대로 ----------
g, svc = fresh()
svc.master["extendedProperties"] = {"private": {}}
FRI = S0 + 2 * WEEK + timedelta(days=4)
EARLY = S0 - timedelta(days=1)  # 넷째 주 회차를 이 회차 앞으로 옮김


def moved_instances(kw):
    if kw["eventId"] == "m1":
        return [{"id": "f", "status": "confirmed", "originalStartTime": {"dateTime": iso(S0 + 2 * WEEK)},
                 "start": {"dateTime": iso(FRI), "timeZone": "Asia/Seoul"},
                 "end": {"dateTime": iso(FRI + timedelta(hours=1)), "timeZone": "Asia/Seoul"}},
                {"id": "e", "status": "confirmed", "originalStartTime": {"dateTime": iso(S0 + 3 * WEEK)},
                 "start": {"dateTime": iso(EARLY)}, "end": {"dateTime": iso(EARLY + timedelta(hours=1))},
                 "extendedProperties": {"private": {"xeticalDone": "1"}}},
                {"id": "u", "status": "confirmed", "originalStartTime": {"dateTime": iso(S0 + WEEK)},
                 "start": {"dateTime": iso(S0 + WEEK)}, "end": {"dateTime": iso(S0 + WEEK + timedelta(hours=1))}}]
    return [{"id": f"{kw['eventId']}_n{i}", "originalStartTime": {"dateTime": iso(S0 + i * WEEK)}} for i in range(4)]


svc.instances = moved_instances
g.update_event(inst(), vals(title="수영"), "following")
patches = {kw["eventId"].rsplit("_", 1)[1]: kw["body"] for kw in svc.calls("events.patch") if "_n" in kw["eventId"]}
n2, n3 = patches.get("n2"), patches.get("n3")
check("R2 금요일로 옮긴 뒤쪽 회차는 새 반복에서도 금요일", n2 and n2["start"] == {
    "dateTime": iso(FRI), "timeZone": "Asia/Seoul"} and n2["end"]["dateTime"] == iso(FRI + timedelta(hours=1)), patches)
check("R2 이 회차 앞으로 옮긴 뒤쪽 회차도 찾아 옮긴 날짜·완료 표시를 그대로",
      n3 and n3["start"] == {"dateTime": iso(EARLY)} and private(n3).get("xeticalDone") == "1", patches)
check("R2 안 옮기고 안 고친 회차는 건드리지 않음", len(patches) == 2, list(patches))
first_q = svc.calls("events.instances")[0]
check("R2 예전 반복은 이 회차 앞쪽도 함께 봄", first_q["timeMin"] < iso(S0), first_q["timeMin"])

# ---------- R5: 회차 표시가 거절돼도 넘어가되 기록은 남김 ----------
from googleapiclient.errors import HttpError
import logging


class Resp(dict):
    status = 403
    reason = "Forbidden"


g, svc = fresh()
svc.master["extendedProperties"] = {"private": {}}
svc.instances = moved_instances
orig_handle = svc.handle


def refuse_patch(kind, kw):
    if kind == "events.patch" and "_n" in kw["eventId"]:
        svc.log.append((kind, kw))
        raise HttpError(Resp(), b"{}")
    return orig_handle(kind, kw)


svc.handle = refuse_patch
records = []
handler = logging.Handler()
handler.emit = records.append
logging.getLogger("xetical").addHandler(handler)
try:
    g.update_event(inst(), vals(title="수영"), "following")
    err = None
except Exception as e:  # noqa: BLE001
    err = e
logging.getLogger("xetical").removeHandler(handler)
check("R5 회차 표시가 거절돼도 '이번 및 이후'는 끝까지(앞쪽 자르기)", err is None and any(
    kw["eventId"] == "m1" for kw in svc.calls("events.patch")), err)
check("R5 거절된 것은 로그에 남김", any("옮기지 못함" in r_.getMessage() for r_ in records), records)

# ---------- R7: 오래된 카운트다운은 동기화마다 다시 받지 않음(고치면 다시 받음) ----------
g, svc = fresh()
svc.lists = old_countdowns


def old_queries():
    return len([kw for kw in svc.calls("events.list") if kw.get("singleEvents") is False])


g.fetch_all(T, T + timedelta(days=30), False)
cds = g.fetch_all(T, T + timedelta(days=30), False)[4]
check("R7 두 번째 동기화는 오래된 카운트다운을 다시 받지 않음(목록은 그대로)",
      old_queries() == 1 and [e.id for e in cds] == ["love"], (old_queries(), [e.id for e in cds]))
g._call(svc.events().patch(calendarId="me", eventId="love", body={}))
g.fetch_all(T, T + timedelta(days=30), False)
check("R7 무언가 고친 뒤에는 다시 받음", old_queries() == 2, old_queries())

# ---------- R6: 칸 편집기에서 공백만 고쳐도 이름 바꾸기로 저장 ----------
a.store.events += [Event("ws", "me", "회의 준비", D2, D2 + timedelta(days=1), True)]
a.store.reindex()
ed = CellEditor(a.cfg, a.store, D2, QRect(0, 0, 160, 120), "me", [])
c = ed.edit.textCursor()
c.movePosition(QTextCursor.Start)
c.movePosition(QTextCursor.Right, n=2)
c.insertText(" ")
ops, lines, ask = ed.collect()
check("R6 겹친 공백을 넣은 것도 저장", [op[3].get("summary") for op in ops if op[0] == "patch"] == ["회의  준비"], ops)
ed.close()
a.store.events = []
a.store.reindex()

# ---------- R9: 설정 가져오기는 시각과 상관없이 같은 트래커를 백업 쪽으로 ----------
a.cfg["mood_trackers"] = [{"id": "mood", "name": "기분", "colors": [], "t": time.time() + 86400}]
a.import_settings_file(str(path))
names = {t["id"]: t["name"] for t in a.cfg["mood_trackers"]}
check("R9 이 PC 트래커의 바꾼 시각이 더 나중이어도 백업 쪽", names == {"mood": "기분(백업)"}, names)

# ================================================================ 전체 코드 리뷰(2026-10-22)에서 고친 것
from xetical.dialogs import EventDialog
from xetical.gcal import google_reminders
from xetical.model import NO_REPEAT

# ---------- F1: '새 일정 기본 알림'을 켜도 ⏳(카운트다운 추가)는 카운트다운을 켠 채로 연다 ----------
a.cfg["default_alarm_on"] = True
dlg = EventDialog(CALS, day=T, default_cal="me", focus="countdown", cfg=a.cfg)
check("F1 기본 알림이 켜져 있어도 카운트다운 칸이 켜짐(기본 알림도 그대로)",
      dlg.countdown.isChecked() and dlg.alarm.isChecked())
dlg.deleteLater()
a.cfg["default_alarm_on"] = False

# ---------- F2: 한 번짜리를 반복으로 바꾼 것을 되돌리면 반복도 없앤다 ----------
g, svc = fresh()
single = Event("s1", "me", "운동", S0, S0 + timedelta(hours=1), False)
g.update_event(single, vals(recurrence=NO_REPEAT), "one")
check("F2 한 번짜리 일정에 '반복 안 함'을 보내면 반복 규칙을 지움",
      svc.calls("events.patch")[0]["body"].get("recurrence") == [], svc.calls("events.patch"))
g, svc = fresh()
g.update_event(single, vals(), "one")
check("F2 반복을 건드리지 않은 수정은 반복 규칙을 보내지 않음", "recurrence" not in svc.calls("events.patch")[0]["body"])
a.store.set(CALS, [Event("one1", "me", "회의", T, T + timedelta(days=1), True)], {},
            (T - timedelta(days=60), T + timedelta(days=90)))
a._open_event_dialog(a.store.events[0], "")
dlg = a._dialogs[-1]
dlg.repeat.kind.setCurrentIndex(dlg.repeat.kind.findData("weekly"))
dlg.action = "save"
dlg.accepted.emit()
undo_v = a.undo_stack[-1]["jobs"][0][1][1]
check("F2 반복으로 바꾼 수정의 되돌리기는 '반복 안 함'을 보냄", undo_v["recurrence"] == NO_REPEAT, undo_v)
dlg.close()
pump(lambda: len(a.outbox) == 0)

# ---------- F4: 칸 편집기에서 뺀 새 줄은 창을 취소하면 한 줄 일정으로 되살아남 ----------
a.store.set(CALS, [], {}, (T - timedelta(days=60), T + timedelta(days=90)))
a.new_event(D2, "alarm", title="치과 예약", restore_line=True)
a._dialogs[-1].reject()
lines_ = [e for e in a.store.events if e.title == "치과 예약"]
check("F4 반복·알림 창을 취소하면 그 줄이 한 줄 일정으로 남음",
      len(lines_) == 1 and lines_[0].line and lines_[0].all_day and lines_[0].first_day == D2, lines_)
a.new_event(D2, "alarm", title="미용실", restore_line=True)
a._dialogs[-1]._save()
made = [e for e in a.store.events if e.title == "미용실"]
check("F4 창을 저장하면 줄을 따로 만들지 않음(일정 하나만)", len(made) == 1 and not made[0].line, made)
pump(lambda: len(a.outbox) == 0)

# ---------- F5: 브라우저 로그인을 기다리는 동안에도 동기화·보내기 줄은 돈다 ----------
import threading
gate, got = threading.Event(), []
a.worker.submit(gate.wait, 10, callback=lambda r, e: got.append("login"), side=True)
a.worker.submit(lambda: "sync", callback=lambda r, e: got.append(r))
pump(lambda: "sync" in got, 3)
check("F5 로그인이 끝나지 않아도 다른 작업이 먼저 끝남", got == ["sync"], got)
gate.set()
pump(lambda: "login" in got, 3)

# ---------- F6: 범위를 벗어난 알림 값 하나가 알림 검사 전체를 멈추지 않음 ----------
check("F6 '24:00'·'9:60'·'09:00|-1'은 알림이 없는 것으로", all(
    Event("x", "me", "t", T, T, True, alarm=al).alarm_parts() is None for al in ("24:00|0", "9:60|0", "09:00|-1")))
check("F6 구글 팝업 알림도 같은 해석(이상한 값이면 걸지 않음)",
      google_reminders({"alarm": "25:00|1", "all_day": True}) is None
      and google_reminders({"alarm": "09:00|1", "all_day": True}) == {"useDefault": False,
                                                                     "overrides": [{"method": "popup", "minutes": 900}]})
a.store.events = [Event("bad", "me", "이상한 알림", T, T + timedelta(days=1), True, alarm="25:00|0"),
                  Event("ok", "me", "약 먹기", T, T + timedelta(days=1), True, alarm=f"{hm}|0")]
a.reminders.fired = set()
try:
    a.reminders.check()
    err = None
except ValueError as e:
    err = e
check("F6 이상한 알림이 있어도 다른 일정 알림은 뜸", err is None and len(a.reminders.popups) == 1, err)
for p_ in list(a.reminders.popups):
    p_.close()
QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
a.store.events = []
a.store.reindex()

# ---------- F9: id 짝을 줄여도 대기 작업·되돌리기가 가리키는 임시 id는 남김 ----------
ob = a.outbox
saved_jobs, saved_map, saved_keep = ob.jobs, ob.id_map, ob.keep_ids
ob.id_map = {f"tmp-old{i}": f"real{i}" for i in range(600)}
ob.jobs = [{"op": "delete_event", "args": J.encode([Event("tmp-old3", "me", "t", T, T, True), "one"]), "tmps": [],
            "label": "", "v": 2}]
ob.keep_ids = lambda: [[("delete_event", [Event("tmp-old7", "me", "t", T, T, True), "one"], [])]]
ob._save()
check("F9 오래된 짝을 버려도 대기 작업·되돌리기가 쓰는 짝은 남음",
      ob.id_map.get("tmp-old3") == "real3" and ob.id_map.get("tmp-old7") == "real7"
      and "tmp-old8" not in ob.id_map and len(ob.id_map) == 302, len(ob.id_map))
ob.jobs, ob.id_map, ob.keep_ids = saved_jobs, saved_map, saved_keep
ob._save()

# ---------- F7·F8: 좁은 칸의 여러 날 막대, 칸 스크롤 끝 ----------
a.cfg["display_mode"], a.cfg["weeks"], a.cfg["span_bars"], a.cfg["passed_weeks"] = "rolling", 6, True, 0
w.go_today()
W0 = w.week_first(T)
a.store.set(CALS, [Event(f"mb{k}", "me", f"여행{k}", W0, W0 + timedelta(days=3), True) for k in range(4)]
            + [Event(f"it{k}", "me", f"할 일 {k}", W0, W0 + timedelta(days=1), True, order=k) for k in range(12)],
            {}, (T - timedelta(days=60), T + timedelta(days=90)))
seen_hidden = {}
orig_items = w._paint_items


def spy_items(p_, L_, d_, cell_, area_, hidden_bars=0):
    seen_hidden[d_] = hidden_bars
    return orig_items(p_, L_, d_, cell_, area_, hidden_bars)


w._paint_items = spy_items
w.resize(560, 330)
w.grab()
check("F7 좁은 칸: 그리지 못한 여러 날 막대는 그 칸의 '…외 N개'에 더함",
      seen_hidden.get(W0, 0) > 0 and seen_hidden.get(W0 + timedelta(days=2), 0) > 0, seen_hidden.get(W0))
w.resize(1400, 1400)
seen_hidden.clear()
w.grab()
check("F7 넉넉한 칸: 막대를 모두 그리고 '…외'에 더하지 않음", seen_hidden.get(W0, 0) == 0, seen_hidden.get(W0))
w._paint_items = orig_items
w.resize(560, 330)
w.grab()
center = next(r for r, dd in w._cells if dd == W0).center()
for _ in range(60):
    w.on_wheel(center, -120)
check("F8 칸 스크롤은 끝까지 내리면 더 내려가지 않음",
      0 < w._scroll.get(W0, 0) == w._scroll_max.get(W0), (w._scroll.get(W0), w._scroll_max.get(W0)))
w._scroll.clear()
a.store.set(CALS, [], {}, (T - timedelta(days=60), T + timedelta(days=90)))

# ---------- F3: 로그아웃하면 보내지 못한 변경을 (물어본 뒤) 버림 — 마지막에(화면 상태를 지운다) ----------
C.CACHE_PATH = SP / "cache_logout.json"  # 진짜 data/cache.json을 지우지 않게
A.config.CACHE_PATH = C.CACHE_PATH
a.outbox.jobs.append({"op": "delete_event", "args": [], "tmps": [], "label": "x", "v": 2})
a.push_undo("시험", [("delete_event", [], [])])
A.ask_confirm = lambda *args, **kw: False
check("F3 대기 변경이 있는데 '취소'하면 로그아웃하지 않음", a.logout() is False and len(a.outbox) == 1)
A.ask_confirm = lambda *args, **kw: True
check("F3 로그아웃하면 대기 변경·되돌리기를 버림(다음 계정으로 보내지 않게)",
      a.logout() is True and len(a.outbox) == 0 and a.outbox.id_map == {} and a.undo_stack == [])

print(f"\n{sum(ok for _, ok in results)}/{len(results)} 통과")
