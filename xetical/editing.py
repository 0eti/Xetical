"""일정 편집: 칸 편집기, 일정 창, 끌어서 옮기기, 초대 응답, 복사·붙여넣기, 되돌리기.
App이 상속해서 쓴다(self는 App)."""
import copy
from datetime import timedelta

from PySide6.QtGui import QGuiApplication

from .dialogs import EventDialog, ask_scope, bring_to_front
from .editor import CellEditor
from .gcal import event_props
from .jobs import new_tmp
from .model import Event
from .widgets import confirm as ask_confirm

UNDO_LIMIT = 50


class EditMixin:
    # ---------- 되돌리기(1-3) ----------
    def push_undo(self, label, jobs, local=None):
        """jobs = [(작업 이름, [인자…], [임시 id…])] — 되돌릴 때 보낼 반대 작업."""
        if not jobs and local is None:
            return
        self.undo_stack.append({"label": label, "jobs": jobs, "local": local})
        del self.undo_stack[:-UNDO_LIMIT]

    def undo(self):
        if not self.undo_stack:
            self.flash("되돌릴 작업이 없어요")
            return
        item = self.undo_stack.pop()
        for op, args, tmps in item["jobs"]:
            self.outbox.add(op, *args, tmps=tmps, label=f"실행 취소: {item['label']}")
        if item["local"]:
            item["local"]()
        self.flash(f"실행 취소: {item['label']}")
        if item["jobs"]:
            self.sync()

    # ---------- 일정 편집 ----------
    def _real(self, ev):
        """예정(가상) 할 일이면 진짜 할 일, 아니면 그대로."""
        if ev is not None and ev.virtual:
            return next((e for e in self.store.events if e.id == ev.source_id), None)
        return ev

    def _add_temp(self, ev):
        self.store.events.append(ev)
        self.store.reindex()
        self.win.update()

    def _remove_local(self, ev_id):
        self.store.events = [e for e in self.store.events if e.id != ev_id]
        self.store.reindex()
        self.win.update()

    @staticmethod
    def _event_from_values(ev_id, v):
        if v["all_day"]:
            start, end = v["start"], v["end"] + timedelta(days=1)
        else:
            start, end = v["start"], v["end"]
        return Event(id=ev_id, calendar_id=v["calendar_id"], title=v["title"], start=start, end=end,
                     all_day=v["all_day"], done=v.get("done", False), text_color=v.get("text_color", ""),
                     order=v.get("order", 0), line=v.get("line", False), alarm=v.get("alarm", ""),
                     countdown=v.get("countdown", ""), description=v.get("description", ""),
                     location=v.get("location", ""), recurring=bool(v.get("recurrence")))

    def open_cell_editor(self, day, rect):
        if self._need_login():
            return
        if self._editor is not None:
            self._editor.save_and_close()
        ed = CellEditor(self.cfg, self.store, day, rect, self.default_calendar(), self.cfg["recent_colors"])
        ed.committed.connect(self.apply_cell)
        ed.openEvent.connect(self.edit_event)
        ed.newStructured.connect(lambda d, kind: self.new_event(d, kind))
        ed.cellColorPicked.connect(self.set_cell_color)
        ed.destroyed.connect(lambda *_: setattr(self, "_editor", None))
        self._editor = ed
        bring_to_front(ed)
        ed.edit.setFocus()

    def apply_cell(self, day, ops, lines, series):
        """셀 편집 결과를 화면에 먼저 반영하고, 구글에는 보낼 편지함으로 보낸다. 되돌리기용 반대 작업도 만든다."""
        self.cfg.save()  # 편집창 크기·최근 색
        by_id = {e.id: copy.copy(e) for e in self.store.events}
        tmps = [new_tmp() for op in ops if op[0] == "create"]
        inverse, inv_tmps = [], []
        for op in ops:
            if op[0] == "patch" and op[2] in by_id:
                old = by_id[op[2]]
                body = {"extendedProperties": {"private": event_props(
                    {"done": old.done, "text_color": old.text_color, "order": old.order, "line": old.line,
                     "alarm": old.alarm, "countdown": old.countdown})}}
                if "summary" in op[3]:
                    body["summary"] = old.title
                inverse.append(("patch", op[1], op[2], body))
            elif op[0] == "delete" and op[2] in by_id and not by_id[op[2]].recurring:
                t = new_tmp()
                inv_tmps.append(t)
                inverse.append(("create", op[1], by_id[op[2]].values()))
        inverse += [("delete", op[1], t) for op, t in zip([o for o in ops if o[0] == "create"], tmps)]

        removed = {op[2] for op in ops if op[0] == "delete"}
        self.store.events = [e for e in self.store.events if e.id not in removed]
        live = {e.id: e for e in self.store.events}
        for i, ln in enumerate(lines):
            ev = ln["ev"]
            if ev and ev.id in live:
                e = live[ev.id]
                e.title, e.done, e.text_color, e.order = ln["text"], ln["done"], ln["color"], i
        it = iter(tmps)
        for op in ops:
            if op[0] == "create":
                v = op[2]
                self.store.events.append(self._event_from_values(next(it), v))
        self.store.reindex()
        self.win.update()
        if ops:
            self.outbox.add("apply_ops", ops, tmps=tmps, label="칸 편집")
        for job in series:
            self.outbox.add(job[0], *job[1:], label="반복 일정 편집")
        if inverse:
            self.push_undo("칸 편집", [("apply_ops", [inverse], inv_tmps)])

    def new_event(self, day, focus=None, start_time=None):
        if self._need_login():
            return
        dlg = EventDialog(self.store.calendars, day=day, default_cal=self.default_calendar(), focus=focus,
                          cfg=self.cfg, start_time=start_time)

        def done():
            v = dlg.values()
            tmp = new_tmp()
            ev = self._event_from_values(tmp, v)
            self._add_temp(ev)
            self.outbox.add("create_event", v, tmps=[tmp], label="새 일정")
            self.push_undo("새 일정", [("delete_event", [ev, "one"], [])])
        dlg.accepted.connect(done)
        self._keep(dlg)

    def edit_event(self, ev):
        if ev.id.startswith("tmp-"):
            return  # 아직 구글에 올라가는 중
        ev = self._real(ev)  # 예정 표시를 누르면 실제 할 일(지금 차례)을 연다
        if ev is None:
            return
        if ev.task_list:
            self._open_task_dialog(ev)
        elif ev.recurring:  # 반복 규칙은 원본에서 읽어 와야 한다
            self.set_status("반복 일정 불러오는 중…")
            self.worker.submit(self.gcal.get_rrule, ev, callback=lambda r, e: self._rrule_loaded(ev, r, e))
        else:
            self._open_event_dialog(ev, "")

    def _rrule_loaded(self, ev, res, err):
        if err is not None:  # 규칙을 모르는 채로 열면 '반복 안 함'으로 보여 저장할 때 반복이 지워질 수 있다
            self.set_status("반복 일정을 불러오지 못했습니다 (인터넷 연결 확인)")
            return
        self.set_status("")
        self._open_event_dialog(ev, res[0])

    def _open_event_dialog(self, ev, rrule):
        dlg = EventDialog(self.store.calendars, event=ev, rrule=rrule, cfg=self.cfg)

        def done():
            if dlg.action == "save":
                scope = "one"
                if ev.recurring:
                    scope = ask_scope(None, "수정", allow_one=not dlg.recurrence_changed())
                    if not scope:
                        return
                v = dlg.values()
                self.outbox.add("update_event", ev, v, scope, label="일정 수정")
                if scope == "one" and v["calendar_id"] == ev.calendar_id:
                    after = self._event_from_values(ev.id, v)
                    after.recurring, after.recurring_event_id = ev.recurring, ev.recurring_event_id
                    self.push_undo("일정 수정", [("update_event", [after, ev.values(), "one"], [])])
                self.sync()
            elif dlg.action == "delete":
                self.delete_event(ev)
            elif dlg.action and dlg.action.startswith("respond:"):
                self.respond(ev, dlg.action.split(":", 1)[1])
        dlg.accepted.connect(done)
        self._keep(dlg)

    def delete_event(self, ev, confirm=False):
        if ev.task_list:
            return self.delete_task(ev, confirm)
        if confirm and not ask_confirm(None, f"'{ev.title}' 일정을 삭제할까요?"):
            return
        scope = ask_scope(None, "삭제") if ev.recurring else "one"
        if not scope:
            return
        if scope == "one":
            self._remove_local(ev.id)
        self.outbox.add("delete_event", ev, scope, label="일정 삭제")
        if not ev.recurring:
            t = new_tmp()
            self.push_undo("일정 삭제", [("create_event", [ev.values()], [t])])
        self.sync()

    def respond(self, ev, status):
        """초대 응답(1-11)."""
        old = ev.response
        ev.response = status
        self.win.update()
        self.outbox.add("respond_event", ev, status, label="초대 응답")
        self.push_undo("초대 응답", [("respond_event", [ev, old], [])])

    def move_event(self, ev, delta):
        """끌어서 다른 날로 옮기기(1-7)."""
        if not delta or ev.virtual or ev.id.startswith("tmp-"):
            return
        before = copy.copy(ev)
        d = timedelta(days=delta)
        ev.start, ev.end = ev.start + d, ev.end + d
        self.store.reindex()
        self.win.update()
        self.outbox.add("move_event", before, delta, label="일정 옮기기")
        self.push_undo("일정 옮기기", [("move_event", [copy.copy(ev), -delta], [])])
        self.flash(f"'{ev.title}' → {ev.first_day:%m/%d}")

    # ---------- 복사·붙여넣기(2-4) ----------
    def copy_selected(self):
        ev = self._real(self.win.selected_event)
        if ev is None:
            self.flash("복사할 일정을 먼저 눌러 선택하세요")
            return
        self.clip = copy.copy(ev)
        QGuiApplication.clipboard().setText(ev.title)
        self.flash(f"복사: {ev.title}")

    def _removable_selection(self):
        """Delete·Ctrl+X 대상. 우클릭 메뉴처럼 '예정'과 읽기 전용 일정은 지울 수 없다."""
        sel = self.win.selected_event
        if sel is None:
            return None
        if sel.virtual or not (sel.writable or sel.task_list):
            self.flash("예정 표시나 읽기 전용 일정은 지울 수 없습니다" if sel.virtual else "읽기 전용 일정은 지울 수 없습니다")
            return None
        return self._real(sel)

    def cut_selected(self):
        ev = self._removable_selection()
        if ev is None:
            return
        self.copy_selected()
        self.delete_event(ev)

    def delete_selected(self):
        ev = self._removable_selection()
        if ev is not None:
            self.delete_event(ev, confirm=True)

    def paste(self, day):
        if self.clip is None or day is None:
            return
        src = self.clip
        if src.task_list:
            tmp = new_tmp()
            self._add_temp(Event(id=tmp, calendar_id=src.calendar_id, title=src.title, start=day,
                                 end=day + timedelta(days=1), all_day=True, task_list=src.task_list,
                                 description=src.description))
            self.outbox.add("create_task", src.task_list, src.title, src.description, day, tmps=[tmp], label="붙여넣기")
            self.push_undo("붙여넣기", [("delete_task", [Event(id=tmp, calendar_id=src.calendar_id, title=src.title,
                                                               start=day, end=day, all_day=True,
                                                               task_list=src.task_list)], [])])
            return
        v = src.values()
        delta = day - src.first_day
        v["start"], v["end"] = v["start"] + delta, v["end"] + delta
        writable = {c["id"] for c in self.store.calendars if c["writable"]}
        if v["calendar_id"] not in writable:
            v["calendar_id"] = self.default_calendar()
        tmp = new_tmp()
        ev = self._event_from_values(tmp, v)
        self._add_temp(ev)
        self.outbox.add("create_event", v, tmps=[tmp], label="붙여넣기")
        self.push_undo("붙여넣기", [("delete_event", [ev, "one"], [])])
        self.flash(f"붙여넣기: {src.title} → {day:%m/%d}")
