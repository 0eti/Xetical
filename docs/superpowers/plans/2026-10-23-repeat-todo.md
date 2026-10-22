# 반복 할 일 → 구글 캘린더 반복 일정 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** '새 할 일' 창에서 반복을 고르면 구글 캘린더에 '할 일' 표시가 붙은 하루 종일 반복 일정을 만들고, Xetical은 회차마다 ☐로 보여 주며, 지금 있는 반복 할 일을 자동으로 옮긴다.

**Architecture:** 반복 할 일 = 구글 캘린더 하루 종일 반복 일정 + `extendedProperties.private.xeticalTodo = "1"`(Event.todo). 회차 완료는 기존 `xeticalDone`. 규칙 만들기·되읽기 같은 순수 함수는 새 모듈 `xetical/todos.py`에 두고, 표시는 view/dayview/editor/reminders가 `ev.task_list or ev.todo`로 할 일처럼 다룬다. 고치기·지우기는 기존 `gcal.update_event / delete_event`(scope)를 그대로 쓴다. 예전 '예정'(virtual) 방식은 마지막에 지운다.

**Tech Stack:** Python 3.13, PySide6, google-api-python-client(Calendar v3, Tasks v1). 시험은 pytest가 아니라 `tests/t_*.py` 스크립트(`check(name, cond, info)`가 `PASS/FAIL` 출력, 마지막 줄 `N/M 통과`).

**Spec:** `docs/superpowers/specs/2026-10-23-repeat-todo-design.md` (함께 읽을 것)

## Global Constraints

- 사용자에게 보이는 글자는 모두 한국어. 코드 주석도 주변처럼 한국어.
- 할 일 표시 키: `xeticalTodo`(값 `"1"`/`""`). 회차 완료는 기존 `xeticalDone`.
- 반복 할 일을 만들 때 휴대폰 알림을 끈다: `reminders = {"useDefault": False, "overrides": []}`.
- 반복 할 일의 캘린더 = `App.default_calendar()`. 제목 앞에 아무것도 붙이지 않는다.
- 같은 날 같은 이름이 두 번 생기면 안 된다(할 일 + 반복 일정, 반복 일정 두 벌).
- `xeticalTodo`가 없는 반복 일정의 동작은 바뀌면 안 된다.
- git: `git add -A` 금지, 파일 이름으로 올린다(공개 저장소). 커밋 메시지는 한국어 + `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- 한 시험만 돌리기(Git Bash):
  `W=$(mktemp -d); PYTHONIOENCODING=utf-8 QT_QPA_PLATFORM=offscreen QT_QPA_FONTDIR=C:/Windows/Fonts .venv/Scripts/python.exe tests/t_todo.py "$W" | grep -v "^PASS"`
  전부: `.venv/Scripts/python.exe tests/run_all.py` (마지막 줄 `모두 통과`).
- 시험 스크립트는 사용자 데이터(`data/`)를 건드리면 안 된다: `C.CONFIG_PATH`, `J.OUTBOX_PATH`, `M.CACHE_PATH`, `R.FIRED_PATH`, `MD.MOODS_PATH`, `A.CELLS_PATH`, `C.CACHE_PATH`를 `sys.argv[1]` 폴더로 돌린다(`tests/t_bugs.py` 첫머리와 같게).

## Review Focus

1. 저장 직후 동기화 전(임시 id `tmp-…`, `tmp-…_YYYYMMDD`) 회차의 ☐를 누름 → 아무 일도 하지 않아야 한다(구글에 없는 id로 보내지 않게). → Task 3 시험
2. 휴대폰에서 규칙을 바꾼 반복 할 일(4-2 모양이 아님)을 창에서 열고 제목만 바꿈 → 규칙은 그대로(KEEP), '반복' 칸은 '기존 규칙 유지'. → Task 4 시험
3. 옮기기 작업이 중간에 끊겨 다시 실행됨(반복 일정은 만들었고 할 일은 아직 안 지움) → 반복 일정을 또 만들지 않고 할 일만 지운다. → Task 5 시험
4. `xeticalTodo` 없는 보통 반복 일정 → ☐ 없음, 열면 일정 창(할 일 창 아님), 칸 편집기에서 한 줄로 고칠 수 있음. → Task 3·4 시험
5. '할 일 표시'를 끄면 반복 할 일도 숨고, '완료한 할 일 표시'를 끄면 완료한 회차만 숨는다. → Task 3 시험

---

### Task 1: 할 일 표시와 반복 규칙 (토대)

**Files:**
- Create: `xetical/todos.py`
- Modify: `xetical/model.py`(Event 필드·values·is_line_candidate), `xetical/gcal.py`(P_TODO, event_props, _props_values, _series_values, _body, set_props), `xetical/dialogs.py`(repeat_dates를 todos로 옮기고 다시 내보내기, rule_dates의 BYSETPOS)
- Create test: `tests/t_todo.py` (t_bugs.py 첫머리처럼 경로 돌리기·`check`·`pump`·가짜 구글 `FakeSvc`/`fresh()`를 둔다. FakeSvc는 `events.*`와 `tasks.*`를 모두 받고, `g._service = g._tasks = lambda: svc`)
- Modify: `tests/run_all.py` (SUITES에 `"t_todo"` 추가)

**Interfaces:**
- Produces:
  - `xetical/todos.py`(Qt·gcal을 import하지 않는 순수 모듈):
    - `TODO_RULES = ("daily", "weekly", "biweekly", "monthly", "yearly")`
    - `repeat_dates(base, rule, until, day=None) -> list[date]` — `dialogs.py`에서 그대로 옮김. `dialogs.py`는 `from .todos import repeat_dates`로 이름을 계속 내보낸다(gcal이 dialogs를 import하면 순환이 생기므로).
    - `todo_rrule(rule: str, day: date) -> str`
    - `rule_of(rrule: str, day: date) -> str | None` — `TODO_RULES` 중 `parse_rrule(rrule) == parse_rrule(todo_rrule(r, day))`인 것, 없으면 None. (`parse_rrule`도 dialogs에서 todos로 옮기고 dialogs가 다시 내보낸다.)
    - `todo_values(title: str, notes: str, day: date, rule: str, calendar_id: str) -> dict` — 일정 편집 값: `title, all_day=True, start=day, end=day, calendar_id, description=notes, location="", done=False, text_color="", order=0, line=False, alarm="", countdown="", todo=True, recurrence=todo_rrule(rule, day)`
  - `Event.todo: bool = False`(model, `countdown` 다음). `Event.values()`에 `"todo"`. `is_line_candidate`는 `todo`면 False.
  - `gcal.P_TODO = "xeticalTodo"`; `event_props(v)`가 `P_TODO: "1" if v.get("todo") else ""`; `_props_values`가 `"todo"`; `_series_values`의 `mine`에 `"todo"`; `set_props`의 `cur`에 `"todo": ev.todo`.
  - `GCal._body(v, …)`: `v.get("todo")`면 `body["reminders"] = {"useDefault": False, "overrides": []}`.

- [ ] **Step 1: 실패하는 시험 쓰기** (`tests/t_todo.py`)

```python
FRI = date(2027, 1, 15)
check("1 매일", todo_rrule("daily", FRI) == "RRULE:FREQ=DAILY")
check("1 매주는 그 요일", todo_rrule("weekly", FRI) == "RRULE:FREQ=WEEKLY;BYDAY=FR")
check("1 2주마다", todo_rrule("biweekly", FRI) == "RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=FR")
check("1 매월 15일", todo_rrule("monthly", FRI) == "RRULE:FREQ=MONTHLY;BYMONTHDAY=15")
check("1 매월 31일은 짧은 달 말일", todo_rrule("monthly", date(2027, 1, 31))
      == "RRULE:FREQ=MONTHLY;BYMONTHDAY=28,29,30,31;BYSETPOS=-1")
check("1 매월 30일", todo_rrule("monthly", date(2027, 1, 30)) == "RRULE:FREQ=MONTHLY;BYMONTHDAY=28,29,30;BYSETPOS=-1")
check("1 매년", todo_rrule("yearly", FRI) == "RRULE:FREQ=YEARLY")
check("1 매년 2월 29일은 평년 28일", todo_rrule("yearly", date(2028, 2, 29))
      == "RRULE:FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=28,29;BYSETPOS=-1")
check("1 규칙 되읽기", all(rule_of(todo_rrule(r, d), d) == r for r in TODO_RULES
                       for d in (FRI, date(2027, 1, 31), date(2028, 2, 29))))
check("1 다른 모양 규칙은 None", rule_of("RRULE:FREQ=WEEKLY;BYDAY=MO,WE", FRI) is None)
check("1 rule_dates: 31일 매월은 2월 28일·4월 30일",
      rule_dates(todo_rrule("monthly", date(2027, 1, 31)), date(2027, 1, 31), date(2027, 5, 1))
      == [date(2027, 1, 31), date(2027, 2, 28), date(2027, 3, 31), date(2027, 4, 30)])
check("1 rule_dates: 2월 29일 매년은 평년 2월 28일",
      rule_dates(todo_rrule("yearly", date(2028, 2, 29)), date(2028, 2, 29), date(2030, 3, 1))
      == [date(2028, 2, 29), date(2029, 2, 28), date(2030, 2, 28)])
v = todo_values("손톱 깎기", "메모", FRI, "weekly", "me")
check("1 할 일 표시가 구글 속성에", event_props(v)["xeticalTodo"] == "1" and _props_values(event_props(v))["todo"])
g, svc = fresh()
g.create_event(v)
body = svc.calls("events.insert")[0]["body"]
check("1 만들 때 휴대폰 알림 끔", body["reminders"] == {"useDefault": False, "overrides": []})
check("1 하루 종일 반복", body["start"] == {"date": "2027-01-15"} and body["recurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=FR"])
ev = Event("x", "me", "손톱 깎기", FRI, FRI + timedelta(days=1), True, todo=True)
check("1 반복 할 일은 한 줄 일정이 아님", not ev.is_line_candidate)
check("1 보통 하루 종일 일정은 그대로 한 줄", Event("y", "me", "t", FRI, FRI + timedelta(days=1), True).is_line_candidate)
```

- [ ] **Step 2: 돌려서 실패 확인** — 위 명령, `ImportError: todos` 또는 FAIL.
- [ ] **Step 3: 구현** — 위 Interfaces 그대로. `rule_dates`(dialogs)는 `BYSETPOS=-1`일 때 MONTHLY는 그 달에서 `BYMONTHDAY` 중 말일 이하인 가장 큰 날만, YEARLY는 `BYMONTH`의 달에서 같은 규칙으로 고른다(지금은 YEARLY가 `(start.month, start.day)`만 본다).
- [ ] **Step 4: 통과 확인** — t_todo 전부 PASS, `run_all.py` `모두 통과`.
- [ ] **Step 5: 커밋**

```bash
git add xetical/todos.py xetical/model.py xetical/gcal.py xetical/dialogs.py tests/t_todo.py tests/run_all.py
git commit -m "반복 할 일: 할 일 표시(xeticalTodo)와 반복 규칙 만들기"
```

### Task 2: '새 할 일' 창으로 만들기, 보통 할 일을 반복으로 바꾸기

**Files:**
- Modify: `xetical/tasks.py`(new_task, _open_task_dialog, 새 `_create_todo`), `xetical/editing.py`(`_event_from_values`가 `todo=v.get("todo", False)`), `xetical/dialogs.py`(TaskDialog)
- Test: `tests/t_todo.py`

**Interfaces:**
- Consumes: `todo_values`, `todo_rrule` (Task 1)
- Produces:
  - `TaskMixin._create_todo(v: dict) -> Event` — `v`는 `todo_values` 결과. 임시 id로 화면에 넣고(`_add_temp`, `_add_temp_repeats(ev, v["recurrence"])`), `outbox.add("create_event", v, tmps=[tmp], label="새 반복 할 일")`, 되돌리기 `("delete_event", [ev, "one"], [])`. 만든 Event를 돌려준다.
  - `TaskDialog(task_lists, event=None, day=None, repeat="none", todo=False)` — `repeat`가 `"keep"`이면 '기존 규칙 유지'(data `"keep"`) 항목을 더해 고른다. 반복이 '안 함'이 아니거나 `todo`면 '목록' 줄을 숨긴다(반복을 바꿀 때마다 다시 판단). 예전 안내 문구("구글은 할 일의 반복 정보를…")는 지운다.

- [ ] **Step 1: 실패하는 시험 쓰기** — 앱 수준(가짜 FakeG + `a.outbox` 작업 기록, t_bugs의 앱 수준 준비와 같게, CALS에 `tasks:L1` 목록 포함):

```python
a.new_task(FRI); dlg = a._dialogs[-1]
dlg.title.setText("손톱 깎기"); dlg.repeat.setCurrentIndex(dlg.repeat.findData("weekly")); dlg._save()
job = jobs_named("create_event")[-1]
check("2 반복을 고르면 구글 할 일 대신 반복 일정", job["v"]["todo"] and job["v"]["recurrence"] == "RRULE:FREQ=WEEKLY;BYDAY=FR"
      and job["v"]["calendar_id"] == a.default_calendar() and not jobs_named("create_task"))
check("2 저장하자마자 다음 회차도 보임", any(e.title == "손톱 깎기" and e.first_day == FRI + timedelta(days=7) for e in a.store.events))
check("2 반복을 고르면 '목록' 줄이 숨음", not dlg.list.isVisibleTo(dlg))
a.new_task(FRI); dlg = a._dialogs[-1]; dlg.title.setText("장보기"); dlg._save()
check("2 반복 안 함이면 지금처럼 구글 할 일", jobs_named("create_task")[-1]["args"][1] == "장보기")
plain = Event("task:L1:p1", "tasks:L1", "물 주기", FRI, FRI + timedelta(days=1), True, task_list="L1")
a._open_task_dialog(plain); dlg = a._dialogs[-1]
dlg.repeat.setCurrentIndex(dlg.repeat.findData("monthly")); dlg.action = "save"; dlg.accepted.emit()
check("2 보통 할 일을 반복으로 바꾸면 반복 일정을 만들고 그 할 일은 지움",
      jobs_named("create_event")[-1]["v"]["recurrence"] == "RRULE:FREQ=MONTHLY;BYMONTHDAY=15"
      and jobs_named("delete_task")[-1]["args"][0].id == "task:L1:p1")
```
(`jobs_named(op)`: FakeG가 받은 호출을 `{"args": …, "v": args[0] if dict}`로 모아 두는 시험 도우미. 작업이 끝날 때까지 `pump(lambda: len(a.outbox) == 0)`.)

- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 구현** — `new_task.done()`: `v["repeat"] != "none"`이면 `_create_todo(todo_values(title, notes, due, repeat, self.default_calendar()))`, 아니면 지금 그대로. `_open_task_dialog.done()`: 보통 할 일(`not ev.todo`)에서 반복을 골랐으면 `_create_todo(...)` + `outbox.add("delete_task", ev, label="할 일을 반복 할 일로")`, 되돌리기는 `[("delete_event", [새 Event, "one"], []), ("create_task", [ev.task_list, ev.title, ev.description, ev.start, ev.done], [new_tmp()])]`.
- [ ] **Step 4: 통과 확인** (t_todo + run_all)
- [ ] **Step 5: 커밋** — `xetical/tasks.py xetical/editing.py xetical/dialogs.py tests/t_todo.py`, 메시지 "반복 할 일: '새 할 일' 창에서 반복 일정으로 만들기"

### Task 3: 칸 표시, ☐ 누르기, 하루 상세 보기, 아침 요약

**Files:**
- Modify: `xetical/view.py`(`_shown`, `item_text`, `_paint_items`의 ☐ 자리, `tooltip_text`), `xetical/tasks.py`(toggle_task, 새 `_toggle_todo`), `xetical/gcal.py`(새 `set_event_done`), `xetical/app.py`(show_event_menu 완료 항목), `xetical/dayview.py`, `xetical/editor.py`(`_item_label`), `xetical/reminders.py`(check_summary)
- Test: `tests/t_todo.py`

**Interfaces:**
- Produces:
  - `GCal.set_event_done(ev: Event, done: bool) -> None` — `self.set_props(ev, done=done)`(그 회차만 patch, 다른 속성은 그 회차 값). `set_props` 설명에서 "앱은 쓰지 않고…" 문구를 지운다.
  - `TaskMixin._toggle_todo(ev: Event) -> None` — `ev.id`가 `tmp-`로 시작하면 아무것도 하지 않음. 아니면 `ev.done` 뒤집기, `outbox.add("set_event_done", ev, ev.done, label=…)`, 완료면 `play_sound(cfg["done_sound"])`, 되돌리기 `("set_event_done", [ev, old], [])`, 다시 그리기·하루 상세 새로 고침. `toggle_task(ev)`는 `ev.todo`면 이것을 부른다.
- 규칙: 할 일처럼 다루는 곳은 모두 `ev.task_list or ev.todo`.
  - `_shown`: `show_tasks`·`show_done_tasks`를 따른다. 
  - `item_text`: `☐ 제목`/`☑ 제목`. ☐ 자리(`_check_hits`)도 만든다.
  - `tooltip_text`: `f"{ev.start:%m/%d} 마감 · {'완료한 할 일' if ev.done else '할 일'}"`, 반복 줄은 `"↻ 반복 할 일"`.
  - 오른쪽 클릭 메뉴: '완료로 표시 / 미완료로 표시'. 하루 상세: 누르면 ☐, 오른쪽 클릭이면 `edit_event`. 칸 편집기 위쪽 목록: `☐ 제목`.
  - 아침 요약: 오늘 일정 목록에서 빼고 할 일에 넣는다. 지난 회차는 `today - 30일 ≤ 날짜 < today`이고 미완료면 `☐ 제목  (마감 MM/DD 지남)`, 회차마다 한 줄.

- [ ] **Step 1: 실패하는 시험 쓰기**

```python
todo = Event("rt_20270115", "me", "손톱 깎기", FRI, FRI + timedelta(days=1), True, recurring=True,
             recurring_event_id="rt", todo=True)
plain_rec = Event("rm_20270115", "me", "주간 회의", FRI, FRI + timedelta(days=1), True, recurring=True, recurring_event_id="rm")
check("3 칸 글자", w.item_text(todo, FRI) == "☐ 손톱 깎기" and w.item_text(plain_rec, FRI) == "주간 회의")
# 그린 뒤 _check_hits에 todo는 있고 plain_rec은 없음
a.toggle_task(todo)
check("3 ☐ 누르면 그 회차만 완료", todo.done and jobs_named("set_event_done")[-1]["args"][1] is True)
check("3 되돌리기", a.undo_stack[-1]["jobs"][0][0] == "set_event_done")
g, svc = fresh(); g.set_event_done(todo, True)
p = private(svc.calls("events.patch")[0]["body"]); 
check("3 구글에는 그 회차 완료 + 할 일 표시 유지", svc.calls("events.patch")[0]["eventId"] == "rt_20270115"
      and p["xeticalDone"] == "1" and p["xeticalTodo"] == "1")
tmp_todo = Event("tmp-abc_20270122", "me", "손톱 깎기", FRI, FRI + timedelta(days=1), True, recurring=True, todo=True)
n = len(a.outbox); a.toggle_task(tmp_todo)
check("3 (Review 1) 동기화 전 회차의 ☐는 무시", not tmp_todo.done and len(a.outbox) == n)
a.cfg["show_done_tasks"] = False
check("3 (Review 5) 완료한 할 일 표시 끔 → 완료 회차 숨음", todo not in w.cell_events(FRI))
a.cfg["show_tasks"] = False   # 미완료 회차도 숨음 확인 후 둘 다 True로
check("3 풍선 도움말", "↻ 반복 할 일" in tooltip_text(todo, a.store, a.cfg) and "마감" in tooltip_text(todo, a.store, a.cfg))
# 아침 요약: T 기준 오늘 회차·10일 전 미완료·40일 전 미완료·10일 전 완료 → 줄에 오늘 것과 10일 전 미완료만, 10일 전 것은 "(마감 MM/DD 지남)"
```
(아침 요약은 `R.SummaryPopup`을 가로채 `lines`를 모아 확인한다. `check_summary(force=True)`.)

- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 구현** — 위 규칙대로.
- [ ] **Step 4: 통과 확인** (t_todo + run_all)
- [ ] **Step 5: 커밋** — 위 Files 전부 + `tests/t_todo.py`, "반복 할 일: 칸에 ☐로 보이고 눌러서 그 회차 완료"

### Task 4: '할 일 수정' 창으로 고치기·지우기, 붙여넣기

**Files:**
- Modify: `xetical/editing.py`(edit_event 분기, paste), `xetical/tasks.py`(새 `_open_todo_dialog`, `_save_todo`)
- Test: `tests/t_todo.py`

**Interfaces:**
- Consumes: `rule_of`, `todo_rrule` (Task 1), `TaskDialog(..., repeat="keep", todo=True)` (Task 2), `_toggle_todo` (Task 3)
- Produces:
  - `edit_event(ev)`: `ev.todo`면 `worker.submit(gcal.get_rrule, ev, callback=…)`로 규칙을 읽어 `_open_todo_dialog(ev, rrule)`(읽기 실패 시 반복 일정과 같은 상태 줄 안내). 보통 반복 일정은 지금처럼 일정 창.
  - `TaskMixin._open_todo_dialog(ev: Event, rrule: str) -> None` — `TaskDialog(self.task_lists(), event=ev, repeat=rule_of(rrule, ev.first_day) or "keep", todo=True)`; 저장·삭제 때 `_save_todo(ev, rrule, dlg)`.
  - `TaskMixin._save_todo(ev, rrule, dlg) -> None` — 아래 순서(범위 묻기는 `tasks.py`가 import한 `ask_scope`, 시험에서 가로챈다):

```python
v = dlg.values(); old_rule = rule_of(rrule, ev.first_day)
if dlg.action == "delete": scope = ask_scope(None, "삭제") → outbox delete_event(ev, scope); "one"이면 _remove_local
elif v["repeat"] == "none": outbox delete_event(ev, "following")   # 묻지 않음
else:
    rule_changed = v["repeat"] not in ("keep", old_rule)
    moved = v["due"] != ev.first_day
    if not rule_changed and (v["title"], v["notes"]) == (ev.title, ev.description) and not moved:
        if v["done"] != ev.done: self._toggle_todo(ev)          # 완료만 바꿈: 묻지 않음
        return
    scope = ask_scope(None, "수정", allow_one=not rule_changed)
    rec = (todo_rrule(v["repeat"], v["due"]) if rule_changed
           else todo_rrule(old_rule, v["due"]) if scope != "one" and moved and old_rule else KEEP)
    vals = dict(ev.values(), title=v["title"], description=v["notes"], start=v["due"], end=v["due"],
                done=v["done"], line=False, todo=True, recurrence=rec)
    outbox update_event(ev, vals, scope)
그 뒤 self.sync()
```
  - `paste(day)`: 붙일 것이 `todo`면 첫 번째 할 일 목록(`self.task_lists()[0]`)에 반복 없는 구글 할 일로 붙인다(지금 할 일 붙여넣기 분기를 그대로 탄다). 할 일 목록이 없으면 `flash("할 일 목록이 없어 붙여 넣지 못했어요")`.
  - 끌어서 옮기기·Delete·잘라내기는 바꿀 것 없음(반복 일정으로 이미 회차만 옮기고 범위를 묻는다). 시험으로만 확인.

- [ ] **Step 1: 실패하는 시험 쓰기**

```python
RULE = "RRULE:FREQ=WEEKLY;BYDAY=FR"
a._open_todo_dialog(todo, RULE); dlg = a._dialogs[-1]
check("4 창의 반복 칸에 지금 규칙", dlg.repeat.currentData() == "weekly")
dlg.done_box.setChecked(True); dlg.action = "save"; dlg.accepted.emit()
check("4 완료만 바꾸면 묻지 않고 그 회차만", asked == [] and jobs_named("set_event_done") and not jobs_named("update_event"))
# 제목+날짜(토요일) 바꾸고 '이번 및 이후'
T_ask("following"); open → title "발톱 깎기", due FRI+1 → save
u = jobs_named("update_event")[-1]
check("4 이후 범위로 날짜 바꾸면 규칙도 새 요일", u["args"][2] == "following" and u["args"][1]["recurrence"] == "RRULE:FREQ=WEEKLY;BYDAY=SA"
      and u["args"][1]["todo"] and u["args"][1]["title"] == "발톱 깎기")
# '이번만'으로 날짜만 바꿈 → recurrence KEEP(None)
# 반복을 '매월'로 바꿈 → ask_scope가 allow_one=False로 불림, recurrence는 매월 규칙
# 반복을 '안 함'으로 → 묻지 않고 delete_event(ev, "following")
# 삭제 → ask_scope("삭제") 결과 범위로 delete_event
# (Review 2) RRULE "RRULE:FREQ=WEEKLY;BYDAY=MO,WE"로 열면 반복 칸 "keep", 제목만 바꿔 '모든 회차' → recurrence None
# (Review 4) 보통 반복 일정 plain_rec를 edit_event → get_rrule 뒤 EventDialog가 열림(TaskDialog 아님)
# 붙여넣기: a.clip = todo; a.paste(FRI+3) → create_task 작업, 목록 "L1", 제목 "손톱 깎기", 날짜 FRI+3
# Delete: a.win.selected_event = todo; a.delete_selected() → editing.ask_scope("삭제")가 불리고 delete_event(todo, 그 범위)
# 끌어서 옮기기: a.move_event(todo, 2) → move_event 작업(그 회차 id), 범위를 묻지 않음
```
(`ask_scope`는 `import xetical.tasks as TK; TK.ask_scope = lambda *a, allow_one=True: (asked.append(allow_one), answer)[1]`로 가로챈다. FakeG의 `get_rrule`은 정해 둔 규칙을 돌려준다.)

- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** (t_todo + run_all)
- [ ] **Step 5: 커밋** — `xetical/editing.py xetical/tasks.py tests/t_todo.py`, "반복 할 일: '할 일 수정' 창으로 고치고 지우기(범위 묻기)"

### Task 5: 지금 있는 반복 할 일 자동으로 옮기기

**Files:**
- Modify: `xetical/gcal.py`(새 `find_todo_series`, `todo_tasks`, `migrate_repeat`), `xetical/sync.py`(`_on_synced`에서 부르기), `xetical/tasks.py`(새 `_migrate_repeats`), `xetical/app.py`(설정 가져오기에서 `task_repeats` 무시)
- Test: `tests/t_todo.py`

**Interfaces:**
- Consumes: `todo_values`, `repeat_dates` (Task 1)
- Produces:
  - `GCal.find_todo_series(cal_id: str, title: str) -> bool` — `_list_events(cal_id, None, None, private=f"{P_TODO}=1", single=False)` 중 `"recurrence"`가 있고 제목이 같은 것이 있으면 True.
  - `GCal.todo_tasks(list_id: str, title: str) -> list[Event]` — 그 목록의 할 일을 **dueMin/dueMax 없이**(showCompleted·showHidden, 쪽 넘김) 받아 제목이 같고 마감일이 있는 것만 `_parse_task`로.
  - `GCal.migrate_repeat(list_id, title, rule, cal_id, today) -> str` — `"none" | "exists" | "created"`:
    - 할 일이 없으면 `"none"`. 마감일이 가장 늦은 것 = `latest`.
    - `find_todo_series(cal_id, title)`면: `latest`가 미완료일 때만 `delete_task(latest)`, `"exists"`.
    - 아니면 시작 날짜 = 미완료면 `latest.start`, 완료면 `repeat_dates(latest.start, rule, max(today, latest.start) + 800일)` 중 `today` 이상인 첫 날짜. `create_event(todo_values(title, latest.description, start, rule, cal_id))`, 미완료면 `delete_task(latest)`, `"created"`.
  - `TaskMixin._migrate_repeats() -> None` — `default_calendar()`가 없으면 아무것도 안 함. `task_repeats`의 `"목록id|제목": 규칙`마다 `outbox.add("migrate_repeat", list_id, title, rule, cal, date.today(), label="반복 할 일 옮기기")`, 그다음 `cfg["task_repeats"] = {}`·`cfg.save()`·`flash(f"반복 할 일 {n}개를 구글 캘린더 반복 일정으로 옮겨요")`.
  - `_on_synced`: 성공 갈래 끝에서 `cfg["task_repeats"]`가 있고 `tasks_status == "ok"`면 `_migrate_repeats()`.
  - `import_settings_file`: `task_repeats`를 합치던 부분을 `values.pop("task_repeats", None)`으로(예전 백업이 이미 옮긴 반복을 다시 옮기지 않게).

- [ ] **Step 1: 실패하는 시험 쓰기** (가짜 구글: `tasks.list`가 정해 둔 할 일, `events.list`가 `privateExtendedProperty` 있을 때 정해 둔 반복 일정)

```python
TODAY = date(2027, 1, 20)
# 미완료 최신(1/15) + 완료 지난 것 → 1/15부터 매주, 1/15 할 일 지움, 지난 것은 둠
check("5 미완료 최신 회차부터 시작", r == "created" and ins["start"] == {"date": "2027-01-15"}
      and ins["recurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=FR"] and private(ins)["xeticalTodo"] == "1"
      and deleted == ["new_task_id"])
check("5 날짜 제한 없이 할 일을 찾음", all("dueMin" not in kw and "dueMax" not in kw for kw in svc.calls("tasks.list")))
# 최신이 완료(2026-08-07, 매주 금) → 오늘(1/20) 이후 첫 금요일 1/22부터, 할 일 안 지움
# 할 일 없음 → "none", 아무것도 안 만듦
# (Review 3) 이미 '할 일' 반복 일정이 있음 → "exists", 만들지 않고 미완료 할 일만 지움 (두 번 실행해도 반복 일정 하나)
# 앱: cfg task_repeats 두 개 + _on_synced(…, "ok") → migrate_repeat 작업 2개, task_repeats == {}
# 앱: tasks_status "scope"면 옮기지 않음(task_repeats 그대로)
# 앱: task_repeats가 든 예전 백업을 가져와도 cfg task_repeats는 {} 그대로
```

- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** (t_todo + run_all)
- [ ] **Step 5: 커밋** — `xetical/gcal.py xetical/sync.py xetical/tasks.py xetical/app.py tests/t_todo.py`, "반복 할 일: 예전 반복 할 일을 자동으로 옮기기"

### Task 6: 예전 '예정' 방식 지우기, 시험·문서 정리

**Files:**
- Modify: `xetical/model.py`(`virtual`, `source_id` 필드와 쓰는 곳), `xetical/tasks.py`(`_project_task_repeats`, `_anchor_day`, `_create_next_occurrence`, `_complete_projection`, `_remember_task_repeat`, `_task_rule`, `_series`, `_task_key` — 옮기기가 `task_repeats`를 읽는 것만 남김), `xetical/editing.py`(`_real` 지우고 부르던 곳은 ev 그대로), `xetical/app.py`(settings_changed의 `show_task_projection`·`task_repeats`, show_event_menu의 `_real`·virtual), `xetical/view.py`, `xetical/sync.py`, `xetical/reminders.py`, `xetical/dayview.py`, `xetical/editor.py`, `xetical/config.py`(`show_task_projection` 지움, `task_repeats`는 옮기기용으로 남기고 주석 고침), `xetical/settings.py`('반복 할 일 예정 표시' 줄)
- Delete: `tests/t_repeat.py` (예전 방식 시험) — `tests/run_all.py` SUITES에서도 뺀다
- Modify tests: `tests/t_bugs.py`(섹션 `3`·`R3`과 `repeat_dates`·`_anchor_day` 쓰는 곳), `tests/t_all.py`(228–241의 예정 그리기, 284–292의 task_repeats 합치기 확인), `tests/t_more.py`(283–287), `tests/t_fixes.py`(B8의 virt 일정 → 읽기 전용 일정만으로)
- Modify docs: `README.md`(반복 할 일 설명: 이제 구글 캘린더 반복 일정, 휴대폰에 보임, 완료 표시는 Xetical에만), `docs/HANDOFF.md`(시험 표: t_repeat 빼고 t_todo 추가, 함정과 주의에 '반복 할 일 = xeticalTodo 반복 일정' 한 단락)

- [ ] **Step 1: 지우기 전 확인용 검색** — `grep -rn "virtual\|source_id\|_project_task_repeats\|show_task_projection\|_anchor_day\|_create_next_occurrence\|_complete_projection\|_remember_task_repeat\|_real(" xetical tests` 결과를 적어 둔다.
- [ ] **Step 2: 지우고 고치기** — 위 목록. `toggle_task`는 `todo`면 `_toggle_todo`, 아니면 완료만 바꾸고 다음 회차를 만들지 않는다.
- [ ] **Step 3: 같은 검색이 비었는지 확인** — `task_repeats`는 config·tasks(`_migrate_repeats`)·sync·app(가져오기에서 버림)에만 남는다.
- [ ] **Step 4: 전부 통과 확인** — `run_all.py`가 `모두 통과`(t_repeat 없음, t_todo 포함).
- [ ] **Step 5: 커밋** — 고친 파일을 이름으로 모두, `git rm tests/t_repeat.py`, "반복 할 일: 예전 '예정' 방식 지우기"

### Task 7: 실제 구글로 확인 (사용자 허락을 받고 돌림)

**Files:**
- Modify: `tests/t_google.py`(시험용 항목 정리 규칙 "[Xetical 테스트]" 그대로 따름)

- [ ] **Step 1: 시험 더하기** — 2027년 1월 31일 시작 매월 반복 할 일(`todo_values`)을 만들고 2027-02~05 회차를 `events.instances`로 받아 날짜가 `2/28, 3/31, 4/30, 5/31`인지, 2028-02-29 시작 매년 반복 할 일의 2029 회차가 `2/28`인지, 만든 일정의 `reminders`가 `useDefault: false`·overrides 없음인지, `set_event_done`으로 한 회차를 완료하면 그 회차만 `xeticalDone == "1"`인지, `find_todo_series`가 찾는지, 끝나면 지운다.
- [ ] **Step 2: 사용자에게 실제 구글 시험 허락을 받고 돌리기** — `PYTHONIOENCODING=utf-8 QT_QPA_PLATFORM=offscreen QT_QPA_FONTDIR=C:/Windows/Fonts .venv/Scripts/python.exe -u tests/t_google.py "$(mktemp -d)"`, 기대: 마지막 줄 `N/N 통과`, "남은 시험용 항목 0개".
- [ ] **Step 3: BYSETPOS가 거절되면** — 스펙 4-2의 대안대로 `todo_rrule`이 29~31일은 `BYMONTHDAY=<날짜>`, 2월 29일은 `FREQ=YEARLY`만 쓰게 바꾸고 Task 1 시험의 기대값도 그에 맞춘다. 사용자에게 "짧은 달은 건너뜀"을 알린다.
- [ ] **Step 4: 커밋** — `tests/t_google.py`(바꿨으면 `xetical/todos.py tests/t_todo.py`도), "반복 할 일: 실제 구글 시험"
