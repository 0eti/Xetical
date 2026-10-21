"""동기화: 구글에서 받아 와 화면에 반영하고, 보낼 편지함의 결과(다 보냄·막힘·실패)를 처리한다.
App이 상속해서 쓴다(self는 App)."""
import copy
import logging
from datetime import date, datetime, timedelta

from PySide6.QtWidgets import QMessageBox

from .gcal import ApiDisabledError, AuthError

log = logging.getLogger("xetical")
FETCH_MARGIN = timedelta(days=42)


def months(n):
    return timedelta(days=31 * n)


def nearest_countdowns(events):
    """반복 카운트다운은 가장 가까운 회차 하나만 남긴다(N일 경과 형식은 가장 최근 지난 회차)."""
    today = date.today()
    groups = {}
    for ev in events:
        groups.setdefault(ev.recurring_event_id or ev.id, []).append(ev)
    out = []
    for evs in groups.values():
        fmt = evs[0].countdown_parts()[2] if evs[0].countdown_parts() else "left"
        future = sorted((e for e in evs if e.first_day >= today), key=lambda e: e.first_day)
        past = sorted((e for e in evs if e.first_day < today), key=lambda e: e.first_day)
        if fmt == "passed" and past:
            out.append(past[-1])
        else:
            out.append(future[0] if future else past[-1])
    return sorted(out, key=lambda e: e.first_day)


class SyncMixin:
    # ---------- 동기화 ----------
    def sync(self):
        if not self.gcal.has_token():
            return
        if len(self.outbox):  # 보낼 변경이 남아 있으면 먼저 보내고 받는다(다 보내면 drained에서 다시 부른다)
            self.outbox.process()
            return
        if self._syncing:
            self._sync_again = True
            return
        a, b = self.win.view_range()
        today = date.today()
        lo = min(a - FETCH_MARGIN, today - months(self.cfg["sync_past_months"]))
        hi = max(b + FETCH_MARGIN, today + months(self.cfg["sync_future_months"]))
        self._syncing = True
        self.win.cloud_state = "syncing"
        self.win.update()
        self.worker.submit(self.gcal.fetch_all, lo, hi, self.cfg["show_holidays"], self.cfg["holiday_region"],
                           callback=self._on_synced)

    def _on_synced(self, res, err):
        self._syncing = False
        if err:
            self.win.cloud_state = "login" if isinstance(err, AuthError) else "error"
            if isinstance(err, AuthError):
                self.set_status(f"{err} — 여기를 클릭", True)
            elif isinstance(err, ApiDisabledError):
                self._fix_url = err.url
                self.set_status("Google Calendar API가 꺼져 있음 — 여기를 클릭해 '사용' 누르기", True)
            else:
                self.set_status("동기화 실패 (인터넷 연결 확인)")
        elif len(self.outbox):
            # 받는 동안 새 변경이 줄을 섰다. 이 결과에는 그 변경이 없어서 화면에 반영해 둔 것을 덮어 버리므로 버린다.
            # (다 보내면 drained에서 다시 받는다)
            self.win.cloud_state = "ok"
        else:
            calendars, events, holidays, rng, countdowns, tasks_status = res
            self.store.set(calendars, events, holidays, rng)
            self._project_task_repeats()
            self.win.countdowns = nearest_countdowns(countdowns)
            self._last_sync = datetime.now()
            self.win.cloud_state = "ok"
            self.set_status("")
            # 할 일은 캘린더와 따로 받는다. 문제가 있으면 알려 주되 캘린더 표시는 그대로 둔다.
            if tasks_status == "scope":
                self.set_status("할 일을 보려면 다시 로그인 — 여기를 클릭", True)
            elif isinstance(tasks_status, tuple):
                self._fix_url = tasks_status[1]
                self.set_status("Google Tasks API가 꺼져 있음 — 여기를 클릭해 '사용' 누르기", True)
            try:
                self.store.save_cache()
            except OSError:
                log.exception("캐시 저장 실패")
            self._refresh_cards()
            self._auto_delay()
            self._reselect()
            self.sync_moods()
            if self._dayview is not None and self._dayview.isVisible():
                self._dayview.refresh()
        self.win.update()
        if self._sync_again:
            self._sync_again = False
            self.sync()

    # ---------- 트래커 드라이브 동기화 ----------
    def sync_moods(self):
        """트래커(칠한 날·트래커 목록)를 구글 드라이브 앱 전용 폴더와 맞춘다. 켜져 있을 때만."""
        if not self.cfg["mood_sync"] or not self.gcal.has_token():
            return
        if not self.gcal.has_drive_scope():
            self.set_status("트래커를 동기화하려면 다시 로그인 — 여기를 클릭", True)
            return
        if self._mood_syncing:
            self._mood_again = True
            return
        self._mood_syncing = True
        local = {"trackers": copy.deepcopy(self.cfg["mood_trackers"]), "trackers_t": self.cfg["mood_trackers_t"],
                 "days": copy.deepcopy(self.moods.days)}
        self.worker.submit(self.gcal.mood_sync, local, callback=self._on_moods_synced)

    def _on_moods_synced(self, res, err):
        self._mood_syncing = False
        if err is not None:
            if isinstance(err, ApiDisabledError):
                self._fix_url = err.url
                self.set_status(f"{err.api}가 꺼져 있음 — 여기를 클릭해 '사용' 누르기", True)
            elif isinstance(err, AuthError):
                self.set_status(f"{err} — 여기를 클릭", True)
            else:  # 인터넷 끊김 등: 다음 동기화 때 합쳐서 다시 올린다(잃는 것 없음)
                log.warning("트래커 동기화 실패: %s", err)
        else:
            if res["trackers_t"] > self.cfg["mood_trackers_t"]:  # 다른 PC에서 트래커 목록을 바꿨다
                self.cfg["mood_trackers"], self.cfg["mood_trackers_t"] = res["trackers"], res["trackers_t"]
                ids = [t["id"] for t in res["trackers"]]
                if self.cfg["mood_active"] not in ids and ids:
                    self.cfg["mood_active"] = ids[0]
                self.cfg.save()
                for tracker_id in [t for t in self.moods.days if t not in ids]:
                    self.moods.drop(tracker_id)
            self.moods.merge(res["days"])  # 동기화하는 사이 이 PC에서 칠한 것은 시각이 더 나중이라 남는다
            self.win.update()
        if self._mood_again:
            self._mood_again = False
            self.sync_moods()

    def _reselect(self):
        """동기화로 일정 객체가 새로 바뀌어도 선택은 유지한다."""
        sel = self.win.selected_event
        if sel is not None:
            self.win.selected_event = next((e for e in self.store.events if e.id == self.outbox.real_id(sel.id)), None)

    def _on_navigated(self):
        if not self.store.covers(*self.win.view_range()):
            self.sync()

    def _on_outbox_drained(self):
        if self.win.status.startswith("오프라인"):
            self.set_status("")
        self.sync()

    def _on_outbox_blocked(self, err):
        self.win.cloud_state = "error"
        if isinstance(err, AuthError):
            self.set_status(f"{err} — 여기를 클릭", True)
        elif isinstance(err, ApiDisabledError):
            self._fix_url = err.url
            self.set_status(f"{err.api}가 꺼져 있음 — 여기를 클릭해 '사용' 누르기", True)
        else:
            self.set_status(f"오프라인 — 변경 {len(self.outbox)}개 저장 대기 중 (연결되면 자동으로 보내요)")
        self.win.update()

    def _on_outbox_failed(self, label, err):
        QMessageBox.warning(None, "Xetical", f"구글에 저장하지 못한 변경이 있어 취소했습니다: {label}\n\n"
                                             f"{type(err).__name__}: {err}")

    def _auto_delay(self):
        """미완료 작업 자동 연기: 지난 30일 안의 완료하지 않은 줄 일정을 오늘로 옮긴다."""
        if not self.cfg["auto_delay"]:
            return
        today = date.today()
        ops = []
        for ev in self.store.events:
            if ev.line and not ev.done and ev.writable and ev.all_day and not ev.recurring \
                    and today - timedelta(days=30) <= ev.start < today and ev.id not in self._delayed_today:
                self._delayed_today.add(ev.id)
                ops.append(("patch", ev.calendar_id, ev.id, {"start": {"date": today.isoformat()},
                                                             "end": {"date": (today + timedelta(days=1)).isoformat()}}))
        if ops:
            self.outbox.add("apply_ops", ops, label="미완료 작업 자동 연기")
