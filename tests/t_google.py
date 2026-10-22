"""② 실제 구글 연동 시험. 2027년 1월에 '[Xetical 테스트]' 일정·할 일을 만들어 시험하고 끝나면 모두 지운다."""
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, ".")
SP = Path(sys.argv[1])
from PySide6.QtWidgets import QApplication

qapp = QApplication([])
import xetical.jobs as J
J.OUTBOX_PATH = SP / "outbox_g.json"
J.OUTBOX_PATH.unlink(missing_ok=True)
from googleapiclient.errors import HttpError
from xetical.gcal import GCal, event_props
from xetical.model import Event

P = "[Xetical 테스트]"
D = date(2027, 1, 11)          # 월요일
RANGE = (date(2027, 1, 1), date(2027, 2, 15))
g = GCal()
results = []


def check(name, cond, info=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{info}]" if info and not cond else ""), flush=True)


def mine():
    cals, evs, _h, _r, cds, ts = g.fetch_all(*RANGE, want_holidays=False)
    return cals, [e for e in evs if e.title.startswith(P)], [e for e in cds if e.title.startswith(P)], ts


def val(title, day, end=None, all_day=True, **kw):
    v = {"title": f"{P} {title}", "all_day": all_day, "start": day, "end": end or day, "description": "",
         "location": "", "done": False, "text_color": "", "order": 0, "line": False, "alarm": "", "countdown": "",
         "recurrence": "", "calendar_id": CAL}
    v.update(kw)
    return v


def cleanup():
    """시험용으로 만든 것을 모두 지운다(반복 일정은 원본째)."""
    svc = g._service()
    n = 0
    for cal in [c for c in g.fetch_all(*RANGE, want_holidays=False)[0] if c["writable"] and not c.get("tasks")]:
        items = svc.events().list(calendarId=cal["id"], q="Xetical 테스트", singleEvents=False,
                                  timeMin="2023-12-01T00:00:00Z", timeMax="2029-03-01T00:00:00Z").execute().get("items", [])
        for it in items:
            if (it.get("summary") or "").startswith(P):
                try:
                    svc.events().delete(calendarId=cal["id"], eventId=it["id"]).execute()
                    n += 1
                except HttpError as e:  # 원본을 먼저 지우면 따로 고친 회차는 이미 지워져 있다(410)
                    if e.resp.status not in (404, 410):
                        raise
    if g.has_tasks_scope():
        ts = g._tasks()
        for tl in ts.tasklists().list().execute().get("items", []):
            for t in ts.tasks().list(tasklist=tl["id"], showCompleted=True, showHidden=True, maxResults=100).execute().get("items", []):
                if (t.get("title") or "").startswith(P):
                    ts.tasks().delete(tasklist=tl["id"], task=t["id"]).execute()
                    n += 1
    return n


cals = g.fetch_all(*RANGE, want_holidays=False)[0]
CAL = next(c["id"] for c in cals if c["primary"])
print("시작 전 정리:", cleanup(), "개", flush=True)
if "--cleanup" in sys.argv:  # 정리만 하고 끝낸다
    sys.exit(0)
try:
  try:
      # ---------- 일정 만들기·속성 ----------
      eid = g.create_event(val("종일", D, done=True, text_color="#ff0000", order=2, line=True))
      _, evs, _, _ = mine()
      e = next((x for x in evs if x.id == eid), None)
      check("종일 일정 만들기 + id 받기", e is not None and e.all_day and e.first_day == D)
      check("완료·글자색·순서·줄 속성 저장", e and e.done and e.text_color == "#ff0000" and e.order == 2 and e.line)

      # ---------- 수정(시간 일정으로) ----------
      s = datetime(2027, 1, 11, 14, 0)
      g.update_event(e, val("시간", D, all_day=False, start=s, end=s + timedelta(hours=1)))
      _, evs, _, _ = mine()
      e = next(x for x in evs if x.id == eid)
      check("수정: 종일 → 시간 일정", not e.all_day and e.start == s and e.end == s + timedelta(hours=1) and e.title.endswith("시간"))

      # ---------- 알림 → 구글 팝업 알림 ----------
      g.update_event(e, val("시간", D, all_day=False, start=s, end=s + timedelta(hours=1), alarm="13:30|0"))
      _, evs, _, _ = mine()
      e = next(x for x in evs if x.id == eid)
      check("알림: Xetical 알림 저장 + 구글 팝업 알림 30분 전", e.alarm == "13:30|0" and e.popup_minutes == [30], (e.alarm, e.popup_minutes))

      # ---------- 옮기기 ----------
      g.move_event(e, 2)
      _, evs, _, _ = mine()
      e = next(x for x in evs if x.id == eid)
      check("끌어서 옮기기(+2일, 시각 유지)", e.start == s + timedelta(days=2))

      # ---------- 카운트다운 ----------
      g.update_event(e, val("카운트다운", D + timedelta(days=5), countdown="exam|bar|left"))
      _, _, cds, _ = mine()
      check("카운트다운 따로 받아오기", any(c.id == eid and c.countdown == "exam|bar|left" for c in cds))

      # ---------- 칸 편집 묶음 저장(만들기·고치기·지우기) ----------
      ops = [("create", CAL, val("줄1", D, line=True, order=0)), ("create", CAL, val("줄2", D, line=True, order=1)),
             ("patch", CAL, eid, {"summary": f"{P} 고친 이름", "extendedProperties": {"private": event_props({"done": True})}})]
      ids = g.apply_ops(ops)
      _, evs, _, _ = mine()
      titles = {x.id: x.title for x in evs}
      check("묶음 저장: 새 줄 2개 + 진짜 id 2개", len(ids) == 2 and all(i in titles for i in ids), ids)
      check("묶음 저장: 고치기", titles.get(eid, "").endswith("고친 이름"))
      g.apply_ops([("delete", CAL, ids[1]), ("delete", CAL, "없는id12345")])
      _, evs, _, _ = mine()
      check("묶음 저장: 지우기(없는 일정 지우기는 무시)", ids[1] not in {x.id for x in evs} and ids[0] in {x.id for x in evs})

      # ---------- 반복 일정 ----------
      rid = g.create_event(val("매주", D, recurrence="RRULE:FREQ=WEEKLY;BYDAY=MO;COUNT=4"))
      _, evs, _, _ = mine()
      inst = sorted([x for x in evs if x.recurring_event_id == rid], key=lambda x: x.start)
      check("반복 일정 만들기(매주 월, 4회)", len(inst) == 4, len(inst))
      rule, _start = g.get_rrule(inst[0])
      check("반복 규칙 읽기", "FREQ=WEEKLY" in rule and "COUNT=4" in rule, rule)
      g.update_event(inst[1], dict(inst[1].values(), title=f"{P} 2회차만"), "one")
      _, evs, _, _ = mine()
      inst = sorted([x for x in evs if x.recurring_event_id == rid], key=lambda x: x.start)
      check("반복: 이번만 수정", [x.title.endswith("2회차만") for x in inst] == [False, True, False, False])
      g.update_event(inst[0], dict(inst[0].values(), title=f"{P} 전체 이름"), "all")
      _, evs, _, _ = mine()
      inst = sorted([x for x in evs if x.recurring_event_id == rid], key=lambda x: x.start)
      check("반복: 전체 수정", sum(x.title.endswith("전체 이름") for x in inst) >= 3, [x.title for x in inst])
      g.update_event(inst[2], dict(inst[2].values(), title=f"{P} 3회차부터"), "following")
      _, evs, _, _ = mine()
      old_series = [x for x in evs if x.recurring_event_id == rid]
      new_series = [x for x in evs if x.recurring and x.recurring_event_id != rid and x.title.endswith("3회차부터")]
      check("반복: 이번 및 이후 수정(둘로 나뉨)", len(old_series) == 2 and len(new_series) >= 2, (len(old_series), len(new_series)))
      g.delete_event(old_series[0], "one")
      _, evs, _, _ = mine()
      check("반복: 이번만 삭제", len([x for x in evs if x.recurring_event_id == rid]) == 1)
      g.delete_event(new_series[0], "all")
      _, evs, _, _ = mine()
      check("반복: 전체 삭제", not any(x.title.endswith("3회차부터") for x in evs))

      # ---------- 시간 반복 일정(리뷰 A1·A2) ----------
      ts = datetime(2027, 1, 4, 9, 0)  # 월요일 오전 9시(UTC로는 전날)
      tid0 = g.create_event(val("시간 반복", D, all_day=False, start=ts, end=ts + timedelta(hours=1),
                                recurrence="RRULE:FREQ=WEEKLY;BYDAY=MO;COUNT=4"))
      _, evs, _, _ = mine()
      ti = sorted([x for x in evs if x.recurring_event_id == tid0], key=lambda x: x.start)
      check("A1 시간 반복 일정 만들기(시간대 포함, 4회)", len(ti) == 4 and ti[0].start == ts, len(ti))
      g.update_event(ti[1], dict(ti[1].values(), title=f"{P} 시간 후반"), "following")
      _, evs, _, _ = mine()
      first = [x for x in evs if x.recurring_event_id == tid0]
      later = sorted([x for x in evs if x.title.endswith("시간 후반")], key=lambda x: x.start)
      check("A2 이후 수정: COUNT 4 → 앞 1회 + 뒤 3회(끝없이 늘지 않음)", len(first) == 1 and len(later) == 3
            and all(x.recurring for x in later), (len(first), len(later)))
      g.update_event(later[0], dict(later[0].values(), title=f"{P} 시간 단일", recurrence=""), "all")
      _, evs, _, _ = mine()
      single = [x for x in evs if x.title.endswith("시간 단일")]
      check("A2 전체 + 반복 안 함: 하나만 남음", len(single) == 1 and not single[0].recurring, [(x.start, x.recurring) for x in single])
      g.create_event(val("종료일", D, all_day=False, start=ts, end=ts + timedelta(hours=1),
                         recurrence="RRULE:FREQ=DAILY;UNTIL=" + datetime(2027, 1, 6, 23, 59, 59).astimezone(
                             timezone.utc).strftime("%Y%m%dT%H%M%SZ")))
      _, evs, _, _ = mine()
      days_ = sorted(x.start.date() for x in evs if x.title.endswith("종료일"))
      check("B10 종료일 1/6이면 1/4·1/5·1/6 세 번(1/7 없음)", days_ == [date(2027, 1, 4), date(2027, 1, 5), date(2027, 1, 6)], days_)

      # ---------- 2026-10-22 버그 찾기: '이번 및 이후'(뒤쪽 예외 옮기기·옮긴 회차), 오래된 카운트다운 ----------
      def series(rid):
          return sorted([x for x in mine()[1] if x.recurring_event_id == rid], key=lambda x: x.start)

      ws = datetime(2027, 1, 4, 10, 0)  # 월요일 10시, 6회: 1/4·1/11·1/18·1/25·2/1·2/8
      wid = g.create_event(val("예외 옮기기", D, all_day=False, start=ws, end=ws + timedelta(hours=1),
                               recurrence="RRULE:FREQ=WEEKLY;BYDAY=MO;COUNT=6"))
      wi = series(wid)
      g.delete_event(wi[2], "one")   # 1/18 지움
      g.set_props(wi[3], done=True)  # 1/25 완료
      g.update_event(wi[1], dict(wi[1].values(), title=f"{P} 예외 뒤쪽"), "following")
      _, evs, _, _ = mine()
      later = sorted([x for x in evs if x.title.endswith("예외 뒤쪽")], key=lambda x: x.start)
      check("12 시간 반복 '이후': 앞쪽은 1/4만", [x.start.date() for x in evs if x.recurring_event_id == wid]
            == [date(2027, 1, 4)])
      check("12 시간 반복 '이후': 지운 1/18은 새 반복에도 없음(EXDATE)", [x.start.date() for x in later]
            == [date(2027, 1, 11), date(2027, 1, 25), date(2027, 2, 1), date(2027, 2, 8)], [x.start for x in later])
      check("12 시간 반복 '이후': 완료한 1/25는 새 반복에서도 완료, 나머지는 아님",
            [x.done for x in later] == [False, True, False, False], [(x.start.date(), x.done) for x in later])

      aid = g.create_event(val("종일 예외", date(2027, 1, 4), recurrence="RRULE:FREQ=DAILY;COUNT=5"))  # 1/4~1/8
      ai = series(aid)
      g.delete_event(ai[2], "one")  # 1/6 지움
      g.update_event(ai[1], dict(ai[1].values(), title=f"{P} 종일 뒤쪽"), "following")
      later = sorted(x.first_day for x in mine()[1] if x.title.endswith("종일 뒤쪽"))
      check("12 종일 반복 '이후': 지운 1/6은 새 반복에도 없음(EXDATE;VALUE=DATE)",
            later == [date(2027, 1, 5), date(2027, 1, 7), date(2027, 1, 8)], later)

      ms = datetime(2027, 1, 6, 10, 0)  # 수요일 10시, 4회: 1/6·1/13·1/20·1/27
      mid = g.create_event(val("옮긴 회차", D, all_day=False, start=ms, end=ms + timedelta(hours=1),
                               recurrence="RRULE:FREQ=WEEKLY;BYDAY=WE;COUNT=4"))
      g.move_event(series(mid)[1], 2)  # 1/13 → 1/15
      moved = next((x for x in series(mid) if x.start.date() == date(2027, 1, 15)), None)
      check("9 옮긴 회차에 원래 자리(originalStartTime)가 옴", moved is not None and moved.original == ms + timedelta(days=7),
            moved and (moved.start, moved.original))
      g.delete_event(moved, "following")
      check("9 옮긴 회차부터 지우기: 1/6만 남음(옮긴 1/15도 지워짐)",
            [x.start.date() for x in series(mid)] == [date(2027, 1, 6)], [x.start for x in series(mid)])

      old = date(2025, 1, 11)  # 400일보다 오래전
      pid = g.create_event(val("오래된 경과", old, countdown="memorial|both|passed"))
      lid = g.create_event(val("오래된 남은 날", old, countdown="exam|bar|left"))
      cd_ids = {c.id for c in mine()[2]}
      check("6 400일보다 오래된 'N일 경과' 카운트다운을 받음", pid in cd_ids, cd_ids)
      check("6 오래된 '남은 날' 카운트다운은 받지 않음", lid not in cd_ids, cd_ids)

      # ---------- 트래커 드라이브 동기화(앱 전용 폴더, 시험용 파일 이름) ----------
      if g.has_drive_scope():
          import xetical.gcal as G
          real_name, G.MOODS_FILE = G.MOODS_FILE, "xetical-moods-test.json"  # 실제 트래커 파일은 건드리지 않는다
          try:
              loc = {"trackers": [{"id": "t", "name": "시험", "colors": []}], "deleted": {},
                     "days": {"t": {"2027-01-11": ["#00b050", 1.0]}}}
              g.mood_sync(loc)
              fid, got = g.mood_download()
              check("트래커: 드라이브 앱 전용 폴더에 올리고 받기", got and got["days"] == loc["days"], got)
              loc2 = {"trackers": loc["trackers"], "deleted": {}, "days": {"t": {"2027-01-12": ["#ff0000", 2.0]}}}
              merged = g.mood_sync(loc2)
              check("트래커: 다른 PC 내용과 합쳐짐", set(merged["days"]["t"]) == {"2027-01-11", "2027-01-12"}, merged)
          except HttpError as ex:  # 드라이브 문제(저장 공간 가득 등)로 나머지 시험까지 멈추지 않게
              reason = "구글 저장 공간이 가득 참" if b"storageQuotaExceeded" in (ex.content or b"") else repr(ex)[:120]
              check("트래커: 드라이브 앱 전용 폴더에 올리고 받기", False, reason)
          finally:
              fid, _got = g.mood_download()
              if fid:
                  g._drive().files().delete(fileId=fid).execute()
              G.MOODS_FILE = real_name
      else:
          print("(드라이브 권한이 없어 트래커 동기화 시험은 건너뜀)", flush=True)

      # ---------- 검색 ----------
      found = g.search([c for c in cals if c["id"] == CAL], "Xetical 테스트", *RANGE)
      check("검색", any(x.id == eid for x in found))

      # ---------- 초대 응답(내가 만든 일정이라 응답 대상 없음 → 오류 없이 지나가는지) ----------
      g.respond_event(Event(eid, CAL, "", D, D, True), "accepted")
      check("초대 응답 호출(내 일정에는 영향 없음)", True)

      # ---------- 반복 할 일(하루 종일 반복 일정 + 할 일 표시) ----------
      from xetical.todos import todo_values
      svc = g._service()

      def inst_days(eid, t0, t1):
          items = svc.events().instances(calendarId=CAL, eventId=eid, timeMin=t0, timeMax=t1).execute()["items"]
          return [it["start"].get("date") for it in items], items

      mid = g.create_event(todo_values(f"{P} 반복 할 일 31일", "", date(2027, 1, 31), "monthly", CAL))
      got = svc.events().get(calendarId=CAL, eventId=mid).execute()
      check("반복 할 일: 휴대폰 알림 없음", got.get("reminders", {}).get("useDefault") is False
            and not got["reminders"].get("overrides"), got.get("reminders"))
      check("반복 할 일: 할 일 표시", got.get("extendedProperties", {}).get("private", {}).get("xeticalTodo") == "1")
      days, items = inst_days(mid, "2027-01-30T00:00:00Z", "2027-06-01T00:00:00Z")
      check("반복 할 일: 31일 매월은 짧은 달 말일(BYSETPOS)",
            days[:5] == ["2027-01-31", "2027-02-28", "2027-03-31", "2027-04-30", "2027-05-31"], days)
      yid = g.create_event(todo_values(f"{P} 반복 할 일 2월 29일", "", date(2024, 2, 29), "yearly", CAL))
      ydays, _ = inst_days(yid, "2025-01-01T00:00:00Z", "2029-01-01T00:00:00Z")
      check("반복 할 일: 2월 29일 매년은 평년 2월 28일",
            ydays == ["2025-02-28", "2026-02-28", "2027-02-28", "2028-02-29"], ydays)
      first = next((x for x in mine()[1] if x.recurring_event_id == mid), None)
      check("반복 할 일: 받으면 할 일로(todo)", first is not None and first.todo and first.first_day == date(2027, 1, 31))
      g.set_event_done(first, True)
      done_now = next((x for x in mine()[1] if x.id == first.id), None)
      _, items = inst_days(mid, "2027-02-27T00:00:00Z", "2027-03-01T00:00:00Z")
      feb = items[0].get("extendedProperties", {}).get("private", {}) if items else {}
      check("반복 할 일: ☐는 그 회차만 완료(할 일 표시는 그대로)", done_now is not None and done_now.done and done_now.todo
            and feb.get("xeticalDone") != "1", (done_now, feb))
      for x in (mid, yid):
          svc.events().delete(calendarId=CAL, eventId=x).execute()

      # ---------- 할 일 ----------
      if g.has_tasks_scope():
          tl = g._tasks().tasklists().list().execute()["items"][0]["id"]
          tid = g.create_task(tl, f"{P} 할 일", "메모", D)
          _, ts_evs, _ = g.fetch_tasks(*RANGE)
          t = next((x for x in ts_evs if x.id == tid), None)
          check("할 일 만들기 + id", t is not None and t.first_day == D and not t.done)
          g.set_task_done(t, True)
          t = next(x for x in g.fetch_tasks(*RANGE)[1] if x.id == tid)
          check("할 일 완료(칸에서 ☐ 누르기와 같은 호출)", t.done)
          g.set_task_done(t, False)
          t = next(x for x in g.fetch_tasks(*RANGE)[1] if x.id == tid)
          check("할 일 완료 취소", not t.done)
          g.move_event(t, 3)
          t = next(x for x in g.fetch_tasks(*RANGE)[1] if x.id == tid)
          check("할 일 끌어서 옮기기(마감일 +3)", t.first_day == D + timedelta(days=3))
          g.update_task(t, f"{P} 할 일 수정", "새 메모", D + timedelta(days=4), False)
          t = next(x for x in g.fetch_tasks(*RANGE)[1] if x.id == tid)
          check("할 일 수정", t.title.endswith("할 일 수정") and t.description == "새 메모" and t.first_day == D + timedelta(days=4))
          g.delete_task(t)
          check("할 일 삭제", not any(x.id == tid for x in g.fetch_tasks(*RANGE)[1]))
      else:
          check("할 일 권한", False, "할 일 권한이 없어 건너뜀")

  except Exception as ex:  # noqa: BLE001
    check("시험 도중 오류", False, repr(ex))
  # 보낼 편지함은 Qt 신호를 쓰므로 따로 돌린다
  if True:
    from xetical.app import Worker
    worker = Worker()
    ob = J.Outbox(g, worker)
    ob.add("create_event", val("편지함", D + timedelta(days=1)), tmps=["tmp-test-1"], label="t")
    ob.add("move_event", Event("tmp-test-1", CAL, "", D + timedelta(days=1), D + timedelta(days=2), True), 1, label="t")
    end = time.time() + 30
    while len(ob) and time.time() < end:
        qapp.processEvents()
        time.sleep(0.05)
    real = ob.id_map.get("tmp-test-1")
    _, evs, _, _ = mine()
    e2 = next((x for x in evs if x.id == real), None)
    check("편지함: 임시 id로 만든 뒤 그 임시 id로 옮기기", e2 is not None and e2.first_day == D + timedelta(days=2), real)
    ob.add("delete_event", Event("tmp-test-1", CAL, "", D, D, True), "one", label="t")
    end = time.time() + 30
    while len(ob) and time.time() < end:
        qapp.processEvents()
        time.sleep(0.05)
    _, evs, _, _ = mine()
    check("편지함: 되돌리기(임시 id로 삭제)", not any(x.id == real for x in evs))
finally:
    n = cleanup()
    _, evs, _, _ = mine()
    left = len(evs)
    if g.has_tasks_scope():
        left += sum(1 for x in g.fetch_tasks(*RANGE)[1] if x.title.startswith(P))
    print(f"정리: 시험용 {n}개 지움, 남은 시험용 항목 {left}개", flush=True)
    J.OUTBOX_PATH.unlink(missing_ok=True)
print(f"\n{sum(ok for _, ok in results)}/{len(results)} 통과")
