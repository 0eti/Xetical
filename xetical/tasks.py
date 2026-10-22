"""할 일(구글 Tasks)과 반복 할 일. 구글은 반복 할 일의 다음 회차를 만들어 주지 않아서 여기서 만든다.
App이 상속해서 쓴다(self는 App)."""
import calendar as pycal
from datetime import date, timedelta

from PySide6.QtWidgets import QMessageBox

from .dialogs import TaskDialog, ask_scope, repeat_dates
from .jobs import new_tmp
from .model import KEEP, Event
from .todos import TODO_RULES, rule_of, todo_rrule, todo_values
from .widgets import confirm as ask_confirm
from .widgets import play_sound


class TaskMixin:
    # ---------- 할 일 ----------
    def task_lists(self):
        return [c for c in self.store.calendars if c.get("tasks")]

    def toggle_task(self, ev):
        """칸에서 ☐를 누르면 바로 완료/미완료(1-1).
        반복 할 일은 구글 API가 다음 회차를 만들어 주지 않으므로 Xetical이 만든다:
        - 지금 회차를 완료하면 다음 회차(할 일)를 새로 만든다.
        - 흐린 '예정'을 누르면 그 날짜의 회차를 '완료된 할 일'로 만든다."""
        if ev.virtual:
            return self._complete_projection(ev)
        if ev.id.startswith("tmp-"):
            return  # 아직 구글에 올라가는 중
        if ev.todo:
            return self._toggle_todo(ev)
        old = ev.done
        ev.done = not old
        jobs = [("set_task_done", [ev, old], [])]
        self.outbox.add("set_task_done", ev, ev.done, label="할 일 완료" if ev.done else "할 일 미완료")
        if ev.done:
            play_sound(self.cfg["done_sound"])
            jobs += self._create_next_occurrence(ev)
        self.push_undo("할 일 완료" if ev.done else "할 일 미완료", jobs)
        self._after_task_change()

    def _toggle_todo(self, ev):
        """반복 할 일의 ☐: 그 회차만 완료/미완료(범위를 묻지 않는다). 구글에는 그 회차의 완료 표시만 고친다."""
        old = ev.done
        ev.done = not old
        label = "할 일 완료" if ev.done else "할 일 미완료"
        self.outbox.add("set_event_done", ev, ev.done, label=label)
        if ev.done:
            play_sound(self.cfg["done_sound"])
        self.push_undo(label, [("set_event_done", [ev, old], [])])
        self._after_task_change()

    def _task_rule(self, ev):
        return self.cfg["task_repeats"].get(self._task_key(ev.task_list, ev.title))

    def _series(self, ev):
        """같은 목록·같은 이름의 할 일들(반복 할 일의 회차들)."""
        return [e for e in self.store.events if e.task_list == ev.task_list and e.title == ev.title and not e.virtual]

    def _anchor_day(self, ev, rule):
        """월·연 반복의 원래 날짜. 31일 회차가 2월 28일로 당겨졌으면 예전 회차에서 31을 찾아 다음 회차를 다시 31일로
        (안 그러면 3/28·4/28…로 계속 밀린다). 달의 마지막 날이 아니면 그 날짜 그대로.
        바로 앞 회차부터 거슬러 보다가 말일이 아닌 회차(당겨지지 않은 진짜 날짜)를 만나면 멈춘다
        (예전에 31일이었다가 28일로 옮긴 할 일이 2월 28일 뒤 다시 31일로 가지 않게)."""
        if rule not in ("monthly", "yearly"):
            return None
        d = ev.first_day
        day = d.day
        if day != pycal.monthrange(d.year, d.month)[1]:
            return day
        before = sorted((e.first_day for e in self._series(ev)
                         if e.first_day < d and (rule == "monthly" or e.first_day.month == d.month)), reverse=True)
        for x in before:
            day = max(day, x.day)
            if x.day != pycal.monthrange(x.year, x.month)[1]:
                break
        return day

    def _create_next_occurrence(self, ev):
        """반복 할 일의 다음 회차를 진짜 할 일로 만든다. 이미 그 날짜(또는 그 뒤) 회차가 있으면 만들지 않는다.
        되돌리기용 반대 작업 목록을 돌려준다."""
        rule = self._task_rule(ev)
        nxt = repeat_dates(ev.start, rule, ev.start + timedelta(days=400), self._anchor_day(ev, rule))[:1] \
            if rule else []
        if not nxt or any(e.first_day >= nxt[0] for e in self._series(ev)):
            return []
        d = nxt[0]
        tmp = new_tmp()
        new = Event(id=tmp, calendar_id=ev.calendar_id, title=ev.title, start=d, end=d + timedelta(days=1),
                    all_day=True, color=ev.color, task_list=ev.task_list, description=ev.description)
        self._add_temp(new)
        self.outbox.add("create_task", ev.task_list, ev.title, ev.description, d, tmps=[tmp], label="반복 할 일 다음 회차")
        self.flash(f"'{ev.title}' 완료 — 다음 회차 {d:%m/%d} 할 일을 만들었어요")
        return [("delete_task", [new], [])]

    def _complete_projection(self, ev):
        """흐린 '예정'을 누름 → 그 날짜 회차를 완료된 할 일로 만든다."""
        src = self._real(ev)
        if src is None:
            return
        tmp = new_tmp()
        done = Event(id=tmp, calendar_id=ev.calendar_id, title=ev.title, start=ev.start, end=ev.end, all_day=True,
                     color=ev.color, task_list=ev.task_list, description=src.description, done=True)
        self._add_temp(done)
        play_sound(self.cfg["done_sound"])
        self.outbox.add("create_task", ev.task_list, ev.title, src.description, ev.start, True, tmps=[tmp],
                        label="반복 할 일 완료")
        self.push_undo("반복 할 일 완료", [("delete_task", [done], [])])
        self.flash(f"'{ev.title}' {ev.first_day:%m/%d} 회차 완료")
        self._after_task_change()

    def _after_task_change(self):
        self._project_task_repeats()
        if self._dayview is not None and self._dayview.isVisible():
            self._dayview.refresh()

    def new_task(self, day):
        if self._need_login():
            return
        lists = self.task_lists()
        if not lists:
            QMessageBox.information(None, "Xetical", "할 일 목록을 불러오지 못했습니다.\n"
                                                     "달력 위쪽 안내 문구를 눌러 다시 로그인하거나 Tasks API를 켜 주세요.")
            return
        dlg = TaskDialog(lists, day=day)

        def done():
            v = dlg.values()
            if v["repeat"] != "none":  # 반복 할 일은 구글 캘린더의 반복 일정으로 만든다(구글 Tasks에는 반복이 없다)
                ev = self._create_todo(todo_values(v["title"], v["notes"], v["due"], v["repeat"],
                                                   self.default_calendar()))
                self.push_undo("새 반복 할 일", [("delete_event", [ev, "one"], [])])
                return
            tmp = new_tmp()
            ev = Event(id=tmp, calendar_id=f"tasks:{v['list_id']}", title=v["title"], start=v["due"],
                       end=v["due"] + timedelta(days=1), all_day=True, task_list=v["list_id"], description=v["notes"])
            self._add_temp(ev)
            self.outbox.add("create_task", v["list_id"], v["title"], v["notes"], v["due"], tmps=[tmp], label="새 할 일")
            self.push_undo("새 할 일", [("delete_task", [ev], [])])
        dlg.accepted.connect(done)
        self._keep(dlg)

    def _create_todo(self, v, op="create_event", *before):
        """반복 할 일(하루 종일 반복 일정, v = todo_values(...))을 만들고 저장하자마자 회차들을 화면에 그린다.
        before: op 앞에 넣을 인자(보통 할 일을 바꿀 때 지울 할 일). 만든 Event를 돌려준다(되돌리기용)."""
        tmp = new_tmp()
        ev = self._event_from_values(tmp, v)
        self._add_temp(ev)
        self._add_temp_repeats(ev, v["recurrence"])
        self.outbox.add(op, *before, v, tmps=[tmp], label="새 반복 할 일")
        return ev

    def _open_todo_dialog(self, ev, rrule):
        """반복 할 일의 '할 일 수정' 창(일정 창 대신). rrule: 구글에서 읽어 온 반복 규칙."""
        dlg = TaskDialog(self.task_lists(), event=ev, repeat=rule_of(rrule, ev.first_day) or "keep", todo=True)
        dlg.accepted.connect(lambda: self._save_todo(ev, rrule, dlg))
        self._keep(dlg)

    def _save_todo(self, ev, rrule, dlg):
        """완료만 바꾸면 그 회차만(묻지 않음), 반복을 '안 함'으로 바꾸면 이 회차부터 끝내고(묻지 않음),
        그 밖에 고치거나 지우면 '이번만 / 이번 및 이후 / 모든 회차'를 묻는다(반복을 바꾸면 '이번만'은 없다)."""
        v = dlg.values()
        old_rule = rule_of(rrule, ev.first_day)  # None = 이 창이 만들지 않은 규칙(휴대폰에서 바꾼 것 등)
        if dlg.action == "delete":
            scope = ask_scope(None, "삭제")
            if not scope:
                return
            if scope == "one":
                self._remove_local(ev.id)
            self.outbox.add("delete_event", ev, scope, label="반복 할 일 삭제")
        elif v["repeat"] == "none":  # 반복을 끝낸다: 이 회차부터 뒤는 지우고, 앞 회차는 기록으로 남긴다
            self.outbox.add("delete_event", ev, "following", label="반복 할 일 끝내기")
        else:
            rule_changed = v["repeat"] not in ("keep", old_rule)
            moved = v["due"] != ev.first_day
            if not rule_changed and not moved and (v["title"], v["notes"]) == (ev.title, ev.description):
                if v["done"] != ev.done:
                    self._toggle_todo(ev)
                return
            scope = ask_scope(None, "수정", allow_one=not rule_changed)
            if not scope:
                return
            if rule_changed:
                rec = todo_rrule(v["repeat"], v["due"])
            elif scope != "one" and moved and old_rule:  # 뒤 회차도 새 날짜(요일·날짜)에 맞춘다
                rec = todo_rrule(old_rule, v["due"])
            else:
                rec = KEEP
            vals = dict(ev.values(), title=v["title"], description=v["notes"], start=v["due"], end=v["due"],
                        done=v["done"], line=False, todo=True, recurrence=rec)
            self.outbox.add("update_event", ev, vals, scope, label="반복 할 일 수정")
        self.sync()

    def _migrate_repeats(self):
        """예전 반복 할 일(규칙은 이 PC 설정 task_repeats에만 있었다)을 구글 캘린더 반복 일정으로 한 번 옮긴다.
        작업은 보낼 편지함에 남으므로 기억은 바로 지운다(껐다 켜도 이어지고, 두 번 옮기지 않는다)."""
        cal = self.default_calendar()
        reps = self.cfg["task_repeats"]
        if not cal or not reps:
            return
        today = date.today()
        moved = 0
        for key, rule in reps.items():
            list_id, _, title = key.partition("|")
            if rule in TODO_RULES and title:
                self.outbox.add("migrate_repeat", list_id, title, rule, cal, today, label="반복 할 일 옮기기")
                moved += 1
        self.cfg["task_repeats"] = {}
        self.cfg.save()
        self._project_task_repeats()
        if moved:
            self.flash(f"반복 할 일 {moved}개를 구글 캘린더 반복 일정으로 옮겨요")

    @staticmethod
    def _task_key(list_id, title):
        return f"{list_id}|{title}"

    def _remember_task_repeat(self, list_id, title, rule, old_title=None):
        reps = self.cfg["task_repeats"]
        if old_title is not None and old_title != title:
            reps.pop(self._task_key(list_id, old_title), None)
        if rule and rule != "none":
            reps[self._task_key(list_id, title)] = rule
        else:
            reps.pop(self._task_key(list_id, title), None)
        self.cfg.save()

    def _project_task_repeats(self):
        """반복 할 일: 가장 최근 회차(완료했든 안 했든) 뒤로 앞으로의 회차를 흐린 '예정'으로 덧붙인다."""
        self.store.events = [e for e in self.store.events if not e.virtual]
        reps = self.cfg["task_repeats"]
        if reps and self.store.range:
            until = self.store.range[1]
            latest = {}
            for ev in self.store.events:
                if ev.task_list and self._task_key(ev.task_list, ev.title) in reps:
                    key = self._task_key(ev.task_list, ev.title)
                    if key not in latest or ev.start > latest[key].start:
                        latest[key] = ev
            for key, ev in latest.items():
                for i, d in enumerate(repeat_dates(ev.start, reps[key], until, self._anchor_day(ev, reps[key]))):
                    self.store.events.append(Event(
                        id=f"vtask:{i}:{ev.id}", calendar_id=ev.calendar_id, title=ev.title, start=d,
                        end=d + timedelta(days=1), all_day=True, color=ev.color, task_list=ev.task_list,
                        virtual=True, source_id=ev.id, writable=False))
        self.store.reindex()
        self.win.update()

    def _open_task_dialog(self, ev):
        dlg = TaskDialog(self.task_lists(), event=ev)

        def done():
            v = dlg.values()
            if dlg.action == "save" and v["repeat"] != "none":
                # 보통 할 일을 반복 할 일로: 그 마감일부터 반복 일정을 만들고 할 일은 지운다(만들어진 뒤에만 지운다)
                new = self._create_todo(todo_values(v["title"], v["notes"], v["due"], v["repeat"],
                                                    self.default_calendar()), "task_to_todo", ev)
                self._remove_local(ev.id)
                self.push_undo("반복 할 일로 바꾸기", [
                    ("delete_event", [new, "one"], []),
                    ("create_task", [ev.task_list, ev.title, ev.description, ev.start, ev.done], [new_tmp()])])
                self._after_task_change()
                self.sync()
            elif dlg.action == "save":
                old = (ev.title, ev.description, ev.start, ev.done)
                self.outbox.add("update_task", ev, v["title"], v["notes"], v["due"], v["done"], label="할 일 수정")
                jobs = [("update_task", [ev, *old], [])]
                became_done = v["done"] and not ev.done
                ev.title, ev.description, ev.done = v["title"], v["notes"], v["done"]
                ev.start, ev.end = v["due"], v["due"] + timedelta(days=1)
                if became_done:  # 창에서 '완료'를 체크해도 반복 할 일의 다음 회차를 만든다
                    jobs += self._create_next_occurrence(ev)
                self.push_undo("할 일 수정", jobs)
                self._project_task_repeats()
                self.sync()
            elif dlg.action == "delete":
                self.delete_task(ev)
        dlg.accepted.connect(done)
        self._keep(dlg)

    def delete_task(self, ev, confirm=False):
        if confirm and not ask_confirm(None, f"'{ev.title}' 할 일을 삭제할까요?"):
            return
        self._remove_local(ev.id)
        self._project_task_repeats()
        self.outbox.add("delete_task", ev, label="할 일 삭제")
        t = new_tmp()
        self.push_undo("할 일 삭제", [("create_task", [ev.task_list, ev.title, ev.description, ev.start, ev.done], [t])])
