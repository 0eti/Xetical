"""할 일(구글 Tasks)과 반복 할 일. 구글 Tasks에는 반복이 없어서(API로 만들 수도 읽을 수도 없다)
반복 할 일은 구글 캘린더의 하루 종일 반복 일정 + '할 일' 표시(Event.todo)로 둔다(todos.py).
App이 상속해서 쓴다(self는 App)."""
from datetime import date, timedelta

from PySide6.QtWidgets import QMessageBox

from .dialogs import TaskDialog, ask_scope
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
        """칸에서 ☐를 누르면 바로 완료/미완료(1-1). 반복 할 일은 그 회차만."""
        if ev.id.startswith("tmp-"):
            return  # 아직 구글에 올라가는 중
        if ev.todo:
            return self._toggle_todo(ev)
        old = ev.done
        ev.done = not old
        self.outbox.add("set_task_done", ev, ev.done, label="할 일 완료" if ev.done else "할 일 미완료")
        if ev.done:
            play_sound(self.cfg["done_sound"])
        self.push_undo("할 일 완료" if ev.done else "할 일 미완료", [("set_task_done", [ev, old], [])])
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

    def _after_task_change(self):
        self.store.reindex()
        self.win.update()
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
        if moved:
            self.flash(f"반복 할 일 {moved}개를 구글 캘린더 반복 일정으로 옮겨요")

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
                ev.title, ev.description, ev.done = v["title"], v["notes"], v["done"]
                ev.start, ev.end = v["due"], v["due"] + timedelta(days=1)
                self.push_undo("할 일 수정", jobs)
                self._after_task_change()
                self.sync()
            elif dlg.action == "delete":
                self.delete_task(ev)
        dlg.accepted.connect(done)
        self._keep(dlg)

    def delete_task(self, ev, confirm=False):
        if confirm and not ask_confirm(None, f"'{ev.title}' 할 일을 삭제할까요?"):
            return
        self._remove_local(ev.id)
        self.outbox.add("delete_task", ev, label="할 일 삭제")
        t = new_tmp()
        self.push_undo("할 일 삭제", [("create_task", [ev.task_list, ev.title, ev.description, ev.start, ev.done], [t])])
