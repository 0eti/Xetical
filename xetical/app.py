"""Xetical 본체: 창, 트레이, 메뉴, 설정, 로그인을 엮는다. 메뉴 구성은 DesktopCal을 따른다.
동기화는 sync.py, 일정 편집·되돌리기·복사는 editing.py, 할 일은 tasks.py에 나눠 두고 App이 상속한다."""
import ctypes
import json
import logging
import os
import shutil
import sys
import webbrowser
import winreg
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from logging.handlers import RotatingFileHandler

from PySide6.QtCore import QLockFile, QObject, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu, QMessageBox, QSystemTrayIcon

from . import config, desktop
from .cards import CountdownCard, default_position
from .config import CELLS_PATH, CLIENT_SECRET_PATH, DEFAULTS, LOCAL_ONLY_KEYS, Config, load_json, save_json
from .dialogs import DatePickDialog, MobileDialog, bring_to_front, color_icon
from .editing import EditMixin
from .gcal import ApiDisabledError, AuthError, GCal, is_holiday_calendar, validate_client_secret
from .hotkeys import Hotkeys
from .jobs import Outbox, is_offline
from .model import EventStore
from .mood import MoodData, active_tracker
from .mousehook import ClickThrough
from .reminders import Reminders
from .sync import SyncMixin
from .tasks import TaskMixin
from .themes import PRESETS, capture, theme_values
from .view import CalendarWindow
from .widgets import app_icon, apply_ui_theme, color_menu, swatch_icon

log = logging.getLogger("xetical")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "Xetical"
HK_TOGGLE, HK_CONTENT = 1, 2
PAGE_TRACKER, PAGE_ACCOUNT, PAGE_ABOUT = 5, 6, 7  # 설정 창 페이지 번호

# 이 설정이 바뀌면 표시 범위가 달라지므로 오늘 기준으로 다시 맞춘다
RESET_VIEW_KEYS = {"display_mode", "weeks", "passed_weeks", "max_columns", "week_start"}
RESYNC_KEYS = {"show_holidays", "holiday_region", "sync_past_months", "sync_future_months"}
CARD_KEYS = {"card_transparency", "card_size", "cell_bg", "date_font", "item_font", "lunar_text", "calendar_visibility"}


class Worker(QObject):
    """구글 API 호출을 한 줄로 세워 백그라운드에서 실행하고, 결과는 UI 스레드로 돌려준다."""
    _done = Signal(object, object, object)

    def __init__(self):
        super().__init__()
        self._pool = ThreadPoolExecutor(1, thread_name_prefix="gcal")
        self._done.connect(self._deliver)

    def submit(self, fn, *args, callback=None):
        def finished(fut):
            try:
                res, err = fut.result(), None
            except Exception as e:  # noqa: BLE001 - UI에 그대로 전달
                res, err = None, e
            self._done.emit(callback, res, err)
        self._pool.submit(fn, *args).add_done_callback(finished)

    def _deliver(self, callback, res, err):
        if err and not isinstance(err, (AuthError, ApiDisabledError)) and not is_offline(err):
            log.error("작업 실패", exc_info=err)
        if callback:
            callback(res, err)


def autostart_command():
    if config.FROZEN:  # exe로 실행 중이면 exe를 그대로 등록한다
        return f'"{sys.executable}"'
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    exe = pyw if os.path.exists(pyw) else sys.executable
    return f'"{exe}" "{config.APP_DIR / "main.pyw"}"'


class App(SyncMixin, EditMixin, TaskMixin, QObject):
    def __init__(self):
        super().__init__()
        self.cfg = Config.load()
        self.cfg.setdefault("recent_colors", [])
        apply_ui_theme(self.cfg["ui_theme"])
        self.gcal = GCal()
        self.gcal.want_drive = self.cfg["mood_sync"]
        self.worker = Worker()
        self.outbox = Outbox(self.gcal, self.worker)
        self.store = EventStore()
        self.store.load_cache()
        self.cell_colors = load_json(CELLS_PATH, {})
        self.moods = MoodData()
        self._mood_syncing = self._mood_again = False
        self.mood_timer = QTimer(self, singleShot=True, interval=3000, timeout=self.sync_moods)  # 칠한 뒤 잠시 모아서
        self._syncing = self._sync_again = False
        self._logging_in = False
        self._fix_url = None  # 상태 줄을 클릭하면 열 해결 페이지
        self._last_sync = None
        self._delayed_today = set()
        self._dialogs = []  # 열린 창이 GC되지 않게 보관
        self._editor = None
        self._settings = None
        self._dayview = None
        self._today = date.today()
        self.undo_stack = []
        self.clip = None   # 복사한 일정(2-4)
        self.cards = {}

        self.win = CalendarWindow(self.cfg, self.store, self.cell_colors)
        w = self.win
        w.sync_info = self.sync_info
        w.cellEditRequested.connect(self.open_cell_editor)
        w.eventEditRequested.connect(self.edit_event)
        w.dayMenuRequested.connect(self.show_day_menu)
        w.eventMenuRequested.connect(self.show_event_menu)
        w.dayViewRequested.connect(self.open_day_view)
        w.taskToggleRequested.connect(self.toggle_task)
        w.eventMoveRequested.connect(self.move_event)
        w.undoRequested.connect(self.undo)
        w.copyRequested.connect(self.copy_selected)
        w.cutRequested.connect(self.cut_selected)
        w.pasteRequested.connect(self.paste)
        w.deleteRequested.connect(self.delete_selected)
        w.menuRequested.connect(lambda pos: self.menu.popup(pos))
        w.pluginsRequested.connect(self.show_calendars_menu)
        w.accountRequested.connect(self.show_account_menu)
        w.mobileRequested.connect(lambda: self._keep(MobileDialog()))
        w.cloudClicked.connect(self.cloud_action)
        w.statusClicked.connect(self.status_action)
        w.navigated.connect(self._on_navigated)
        w.adjustFinished.connect(lambda: self.set_adjusting(False))
        w.moods = self.moods
        w.moodTrackerChosen.connect(self.choose_tracker)
        w.moodMenuRequested.connect(self.show_mood_menu)

        self.outbox.drained.connect(self._on_outbox_drained)
        self.outbox.blocked.connect(self._on_outbox_blocked)
        self.outbox.failed.connect(self._on_outbox_failed)

        self.menu = self._build_menu()
        self.tray = QSystemTrayIcon(app_icon(self._today.day, self.cfg["tray_show_date"]), self)
        self.tray.setToolTip("Xetical")
        self.tray.setContextMenu(self._build_tray_menu())
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

        self.hotkeys = Hotkeys()
        QApplication.instance().installNativeEventFilter(self.hotkeys)
        self.reminders = Reminders(self)
        self.click_hook = ClickThrough(self.win)

        self.sync_timer = QTimer(self, timeout=self.sync)
        self.minute_timer = QTimer(self, interval=60_000, timeout=self._tick)
        self.attach_timer = QTimer(self, interval=3_000, timeout=self._ensure_attached)
        self.screen_timer = QTimer(self, singleShot=True, interval=800, timeout=self._fit_windows)
        self.status_timer = QTimer(self, singleShot=True, timeout=self._clear_flash)
        self._flash = None
        qapp = QGuiApplication.instance()
        qapp.screenAdded.connect(self._watch_screen)
        qapp.screenRemoved.connect(lambda _s: self.screen_timer.start())
        for s in QGuiApplication.screens():
            self._watch_screen(s, initial=True)

    # ---------- 시작 ----------
    def start(self):
        self._restore_geometry()
        if self.cfg["show_calendar"]:
            self.win.show_on_desktop()
        self._apply_click_through()
        self.minute_timer.start()
        self.attach_timer.start()
        self._apply_auto_sync()
        self._apply_hotkeys(quiet=True)
        if not self.gcal.has_client_secret():
            self.set_status("구글 연동 설정 필요 — 여기를 클릭", True)
            QTimer.singleShot(500, self.first_run_help)
        elif not self.gcal.has_token():
            self.set_status("구글 로그인 필요 — 여기를 클릭", True)
        else:
            self.sync()

    def _restore_geometry(self):
        """저장한 위치를 쓰되, 지금 연결된 화면 안으로 들어오게 맞춘다(1-9)."""
        g = self.cfg.get("geometry")
        rect = QRect(*g) if g else None
        if rect is None or not any(s.availableGeometry().intersects(rect) for s in QGuiApplication.screens()):
            a = QGuiApplication.primaryScreen().availableGeometry()
            w, h = round(a.width() * 0.4), round(a.height() * 0.58)
            rect = QRect(a.right() - w + 1, a.top(), w, h)
        self.win.setGeometry(fit_rect(rect))

    def _watch_screen(self, screen, initial=False):
        screen.availableGeometryChanged.connect(lambda _r: self.screen_timer.start())
        screen.logicalDotsPerInchChanged.connect(lambda _d: self.screen_timer.start())
        if not initial:
            self.screen_timer.start()

    def _fit_windows(self):
        """모니터를 빼거나 해상도·배율이 바뀌면 달력과 카드를 화면 안으로 다시 맞춘다."""
        if self.win.adjusting:
            return
        self._restore_geometry()
        for card in self.cards.values():
            r = fit_rect(QRect(card.pos(), card.size()))
            card.move(r.topLeft())
        self.win.update()

    def _ensure_attached(self):
        self.win.ensure_attached()
        self.click_hook.check()

    # ---------- 메뉴 ----------
    @staticmethod
    def _add(menu, text, fn, checked=None, enabled=True):
        act = QAction(text, menu)
        if checked is not None:
            act.setCheckable(True)
            act.setChecked(checked)
        act.setEnabled(enabled)
        act.triggered.connect(fn)
        menu.addAction(act)
        return act

    def _build_menu(self):
        """☰ '추가 옵션' 메뉴. 제목줄 버튼·설정 창과 겹치는 항목은 뺐다."""
        m, add = QMenu(), self._add
        add(m, "날짜로 이동…", self.pick_date)
        m.addSeparator()
        self.act_undo = add(m, "실행 취소", self.undo)
        add(m, "내용 검색…", self.open_search)
        m.addSeparator()
        self.act_content = add(m, "예약된 작업 숨기기", self.toggle_content)
        add(m, "캘린더 숨기기", lambda: self.set_calendar_visible(False))
        add(m, "위치 및 크기", lambda: self.set_adjusting(True))
        m.addSeparator()
        add(m, "내보내기…", self.open_export)
        m.addSeparator()
        add(m, "설정…", self.open_settings)
        add(m, "종료", QApplication.quit)
        m.aboutToShow.connect(self._update_menu)
        return m

    def _build_tray_menu(self):
        """트레이 메뉴(DesktopCal 트레이 메뉴 구성)."""
        m, add = QMenu(), self._add
        self.act_tray_show = add(m, "캘린더 숨기기", lambda: self.set_calendar_visible(not self.cfg["show_calendar"]))
        add(m, "관리 도구 열기", self.open_settings)
        add(m, "동기화", self.cloud_action)
        m.addSeparator()
        add(m, "사용 안내", lambda: os.startfile(config.README_PATH))
        add(m, "제품정보", lambda: self.open_settings(PAGE_ABOUT))
        m.addSeparator()
        add(m, "닫기", QApplication.quit)
        m.aboutToShow.connect(lambda: self.act_tray_show.setText(
            "캘린더 숨기기" if self.cfg["show_calendar"] else "캘린더 목록 열기"))
        return m

    def _update_menu(self):
        self.act_undo.setEnabled(bool(self.undo_stack))
        self.act_undo.setText(f"실행 취소: {self.undo_stack[-1]['label']}" if self.undo_stack else "실행 취소")
        self.act_content.setText("예약된 작업 숨기기" if self.cfg["show_content"] else "예약된 작업 표시")

    def sync_info(self):
        """☁ 버튼 풍선 도움말에 덧붙일 줄: 마지막 동기화 시각과 보내지 못한 변경 수."""
        lines = []
        if self._last_sync:
            mins = int((datetime.now() - self._last_sync).total_seconds() // 60)
            ago = "방금" if mins < 1 else f"{mins}분 전" if mins < 60 else f"{mins // 60}시간 전"
            lines.append(f"마지막 동기화: {ago}")
        if len(self.outbox):
            lines.append(f"보내기 대기: {len(self.outbox)}개")
        return "\n".join(lines)

    def _day_items(self, m, d):
        self._add(m, "편집", lambda: self.open_cell_editor(d, self.win.cell_global_rect(d)))
        self._add(m, "하루 상세 보기", lambda: self.open_day_view(d))
        self._add(m, "새 일정 (시간·반복·알림)…", lambda: self.new_event(d))
        self._add(m, "새 할 일…", lambda: self.new_task(d))
        clip = f"붙여넣기: {self.clip.title}" if self.clip else "붙여넣기"
        self._add(m, clip, lambda: self.paste(d), enabled=self.clip is not None)
        cm = color_menu(lambda c: self.set_cell_color(d, c), self.cfg["recent_colors"], parent=m)
        cm.setTitle("셀 배경색")
        m.addMenu(cm)

    def show_day_menu(self, d, pos):
        """달력 우클릭: 그날 항목 + 실행 취소·설정. 나머지는 ☰ 메뉴에 있다."""
        if d is None:
            self.menu.popup(pos)
            return
        m = QMenu()
        self._day_items(m, d)
        m.addSeparator()
        self._update_menu()
        m.addAction(self.act_undo)
        m.addSeparator()
        self._add(m, "설정…", self.open_settings)
        m.exec(pos)

    def show_event_menu(self, ev, d, pos):
        """일정 위 우클릭: 편집·복사·잘라내기·삭제(+ 초대 응답) + '이 날짜' 하위 메뉴."""
        m = QMenu()
        real = self._real(ev)
        self._add(m, "편집…", lambda: self.edit_event(ev))
        if ev.task_list and real:
            self._add(m, "미완료로 표시" if real.done else "완료로 표시", lambda: self.toggle_task(real))
        self._add(m, "복사", self.copy_selected, enabled=not ev.virtual)
        self._add(m, "잘라내기", self.cut_selected, enabled=not ev.virtual and (ev.writable or bool(ev.task_list)))
        self._add(m, "삭제", self.delete_selected, enabled=not ev.virtual and (ev.writable or bool(ev.task_list)))
        if ev.response:
            rm = m.addMenu("초대 응답")
            for status, label in (("accepted", "참석"), ("tentative", "미정"), ("declined", "거절")):
                self._add(rm, label, lambda _=False, s=status: self.respond(ev, s), checked=ev.response == status)
        if d:
            m.addSeparator()
            self._day_items(m.addMenu("이 날짜"), d)
        m.exec(pos)

    def show_calendars_menu(self, pos):
        """🧩 버튼: 달력에 표시할 캘린더 선택."""
        m = QMenu()
        cals = [c for c in self.store.calendars if not is_holiday_calendar(c["id"])]
        if not cals:
            m.addAction("(로그인 후 동기화하면 캘린더 목록이 나타납니다)").setEnabled(False)
        for c in cals:
            act = self._add(m, c["summary"], lambda on, cid=c["id"]: self._set_calendar_visible(cid, on),
                            checked=self.win.visible_calendar(c["id"]))
            act.setIcon(color_icon(c["color"]))
        m.addSeparator()
        self._add(m, "공휴일 표시", lambda on: self._set_cfg("show_holidays", on), checked=self.cfg["show_holidays"])
        self._add(m, "음력", lambda on: self._set_cfg("show_lunar", on), checked=self.cfg["show_lunar"])
        self._add(m, "24절기", lambda on: self._set_cfg("show_solar_terms", on), checked=self.cfg["show_solar_terms"])
        self._add(m, "할 일 표시", lambda on: self._set_cfg("show_tasks", on), checked=self.cfg["show_tasks"])
        m.exec(pos)

    def show_account_menu(self, pos):
        """👤 버튼: 계정 센터."""
        m = QMenu()
        primary = next((c["id"] for c in self.store.calendars if c["primary"]), None)
        if self.gcal.has_token():
            m.addAction(primary or "구글 계정에 로그인됨").setEnabled(False)
            m.addSeparator()
            self._add(m, "계정 설정", lambda: self.open_settings(PAGE_ACCOUNT))
            self._add(m, "로그아웃", self.logout)
        else:
            m.addAction("로그인되어 있지 않음").setEnabled(False)
            m.addSeparator()
            self._add(m, "로그인", self.login)
        self._add(m, "client_secret.json 불러오기…", self.choose_client_secret)
        self._add(m, "구글 계정 권한 관리", lambda: webbrowser.open("https://myaccount.google.com/permissions"))
        m.exec(pos)

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self.set_calendar_visible(True)
            self.win.go_today()

    def _tick(self):
        today = date.today()
        if today != self._today:  # 날짜가 바뀌면 오늘 페이지로 따라간다
            was_current = self.win.anchor == self.win.today_anchor(self._today)
            self._today = today
            self._delayed_today.clear()
            self._update_icons()
            if was_current:
                self.win.go_today()
            self._refresh_cards()
        self.win.update()

    def _update_icons(self):
        self.tray.setIcon(app_icon(date.today().day, self.cfg["tray_show_date"]))
        QApplication.setWindowIcon(app_icon(date.today().day))

    # ---------- 상태 / 표시 ----------
    def set_status(self, text, clickable=False):
        self._flash = None
        self.win.status, self.win.status_clickable = text, clickable
        self.win.update()

    def flash(self, text, ms=3000):
        """잠깐 보이고 사라지는 안내(실행 취소 등)."""
        if self.win.status_clickable:
            return  # 해야 할 일을 알리는 문구는 덮지 않는다
        self.set_status(text)
        self._flash = text
        self.status_timer.start(ms)

    def _clear_flash(self):
        if self._flash is not None and self.win.status == self._flash:
            self.set_status("")

    def status_action(self):
        if self._fix_url:
            webbrowser.open(self._fix_url)
            self._fix_url = None
            self.set_status("사용 설정 후 1~2분 뒤 ☁ 버튼을 눌러 주세요")
        elif not self.gcal.has_client_secret():
            self.first_run_help()
        else:
            self.login()

    def cloud_action(self):
        """☁ 버튼: 로그인 전이면 로그인, 아니면 지금 동기화."""
        if self.gcal.has_token() and not self._fix_url:
            self.outbox.process()
            self.sync()
        else:
            self.status_action()

    def set_calendar_visible(self, on):
        self.cfg["show_calendar"] = on
        self.cfg.save()
        if on:
            self.win.show_on_desktop()
        else:
            self.win.hide()
        self._apply_click_through()
        self._refresh_cards()

    def toggle_content(self):
        self._set_cfg("show_content", not self.cfg["show_content"])

    def _set_cfg(self, key, value):
        self.cfg[key] = value
        self.cfg.save()
        self.settings_changed(key)

    def _set_calendar_visible(self, cal_id, on):
        self.cfg["calendar_visibility"][cal_id] = on
        self._set_cfg("calendar_visibility", self.cfg["calendar_visibility"])

    def settings_changed(self, key):
        """설정 창·메뉴에서 값이 바뀌면 바로 적용한다."""
        if key in RESET_VIEW_KEYS:
            self.win.anchor = self.win.today_anchor()
            self._on_navigated()
        if key == "glass":
            desktop.set_blur(int(self.win.winId()), self.cfg["glass"])
        if key in ("auto_sync", "refresh_minutes"):
            self._apply_auto_sync()
        if key.startswith("hotkey_toggle") or key.startswith("hotkey_content"):
            self._apply_hotkeys()
        if key in RESYNC_KEYS:
            self.sync()
        if key in CARD_KEYS:
            for card in self.cards.values():
                card.apply_size()
            self._refresh_cards()
        if key == "click_through":
            self._apply_click_through()
        if key == "tray_show_date":
            self._update_icons()
        if key == "ui_theme":
            apply_ui_theme(self.cfg["ui_theme"])
            if self._settings is not None:
                self._settings.restyle()
        if key in ("mood_sync", "mood_trackers"):
            self.gcal.want_drive = self.cfg["mood_sync"]
            if self.cfg["mood_sync"]:
                self.mood_timer.start()
        if key in ("show_task_projection", "task_repeats"):
            self._project_task_repeats()
        if self._dayview is not None and self._dayview.isVisible():
            self._dayview.refresh()
        self.win.update()

    def _apply_auto_sync(self):
        if self.cfg["auto_sync"]:
            self.sync_timer.start(self.cfg["refresh_minutes"] * 60_000)
        else:
            self.sync_timer.stop()

    def _apply_click_through(self):
        on = self.cfg["click_through"] and self.cfg["show_calendar"]
        self.win.set_click_through(on)
        if on:
            self.click_hook.start()
        else:
            self.click_hook.stop()

    def _apply_hotkeys(self, quiet=False):
        failed = []
        if not self.hotkeys.set(HK_TOGGLE, self.cfg["hotkey_toggle"], self.cfg["hotkey_toggle_on"],
                                lambda: self.set_calendar_visible(not self.cfg["show_calendar"])):
            failed.append(self.cfg["hotkey_toggle"])
        if not self.hotkeys.set(HK_CONTENT, self.cfg["hotkey_content"], self.cfg["hotkey_content_on"],
                                self.toggle_content):
            failed.append(self.cfg["hotkey_content"])
        if failed and not quiet:
            self.tray.showMessage("Xetical", f"단축키를 등록하지 못했습니다(다른 프로그램이 사용 중): {', '.join(failed)}",
                                  QSystemTrayIcon.Warning)
        elif failed:
            log.warning("단축키 등록 실패: %s", failed)

    # ---------- 트래커 ----------
    def choose_tracker(self, tracker_id):
        self.cfg["mood_active"] = tracker_id
        self.cfg.save()
        self.win.update()

    def show_mood_menu(self, d, pos):
        """트래커 칸을 누름: 지금 트래커의 색 중에서 고른다."""
        tracker = active_tracker(self.cfg)
        if tracker is None:
            return
        current = self.moods.get(tracker["id"], d)
        m = QMenu()
        m.addAction(f"{tracker['name']} · {d.month}월 {d.day}일").setEnabled(False)
        for c in tracker["colors"]:
            act = self._add(m, c["label"], lambda _=False, col=c["color"]: self.set_mood(d, col),  # 이름이 없으면 색 견본만
                            checked=current.lower() == c["color"].lower())
            act.setIcon(swatch_icon(c["color"]))
        m.addSeparator()
        self._add(m, "지우기", lambda: self.set_mood(d, ""), enabled=bool(current))
        self._add(m, "트래커 설정…", lambda: self.open_settings(PAGE_TRACKER))
        m.exec(pos)

    def set_mood(self, d, color, tracker_id=None, record=True):
        tracker_id = tracker_id or active_tracker(self.cfg)["id"]
        old = self.moods.get(tracker_id, d)
        self.moods.set(tracker_id, d, color)
        if self.cfg["mood_sync"]:
            self.mood_timer.start()
        if record and old != color:
            self.push_undo("트래커", [], local=lambda: self.set_mood(d, old, tracker_id, record=False))
        self.win.update()

    def set_cell_color(self, d, color, record=True):
        old = self.cell_colors.get(d.isoformat(), "")
        if color:
            self.cell_colors[d.isoformat()] = color
            if color in self.cfg["recent_colors"]:
                self.cfg["recent_colors"].remove(color)
            self.cfg["recent_colors"].insert(0, color)
            del self.cfg["recent_colors"][8:]
            self.cfg.save()
        else:
            self.cell_colors.pop(d.isoformat(), None)
        save_json(CELLS_PATH, self.cell_colors)
        if record and old != color:
            self.push_undo("셀 배경색", [], local=lambda: self.set_cell_color(d, old, record=False))
        self.win.update()

    def import_cell_colors(self, colors):
        self.cell_colors.update(colors)
        save_json(CELLS_PATH, self.cell_colors)
        self.win.update()

    def import_settings(self, values):
        self.cfg.update(values)
        self.cfg.save()
        if "geometry" in values:
            self._restore_geometry()
        for key in values:
            self.settings_changed(key)

    # ---------- 테마(2-6) / 설정 백업(4-1) ----------
    def theme_names(self):
        return list(PRESETS) + list(self.cfg["custom_themes"])

    def apply_theme(self, name):
        theme = PRESETS.get(name) or self.cfg["custom_themes"].get(name)
        if theme:
            self.import_settings(theme_values(theme, self.cfg))

    def save_custom_theme(self, name):
        self.cfg["custom_themes"][name] = capture(self.cfg)
        self.cfg.save()

    def export_settings(self, path):
        data = {k: v for k, v in self.cfg.items() if k not in LOCAL_ONLY_KEYS}
        data["cell_colors"] = self.cell_colors
        data["moods"] = self.moods.days
        data["_xetical"] = config.APP_VERSION
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def import_settings_file(self, path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("Xetical 설정 파일이 아닙니다.")
        colors = data.pop("cell_colors", None)
        moods = data.pop("moods", None)
        values = {k: v for k, v in data.items() if k in DEFAULTS and k not in LOCAL_ONLY_KEYS}
        self.import_settings(values)
        if isinstance(colors, dict):
            self.import_cell_colors(colors)
        if isinstance(moods, dict):
            self.moods.restore(moods)
            self.win.update()
        return len(values)

    def default_calendar(self):
        writable = [c for c in self.store.calendars if c["writable"] and not is_holiday_calendar(c["id"])]
        want = self.cfg["default_calendar"]
        if any(c["id"] == want for c in writable):
            return want
        return next((c["id"] for c in writable if c["primary"]), writable[0]["id"] if writable else "")

    # ---------- 카운트다운 카드 ----------
    def _refresh_cards(self):
        want = {}
        if self.cfg["show_calendar"]:
            for ev in self.win.countdowns:
                parts = ev.countdown_parts()
                if parts and parts[1] in ("card", "both") and self.win.visible_calendar(ev.calendar_id):
                    want[ev.recurring_event_id or ev.id] = ev
        for key in list(self.cards):
            if key not in want:
                self.cards.pop(key).close()
        for i, (key, ev) in enumerate(want.items()):
            card = self.cards.get(key)
            if card is None:
                card = CountdownCard(self.cfg, ev)
                card.moved.connect(self._card_moved)
                card.openRequested.connect(self.edit_event)
                pos = self.cfg["card_positions"].get(key)
                start = QPoint(*pos) if pos else default_position(i, self.win.geometry())
                card.move(fit_rect(QRect(start, card.size())).topLeft())
                card.show_on_desktop()
                self.cards[key] = card
            card.ev = ev
            card.update()

    def _card_moved(self, key, pos):
        self.cfg["card_positions"][key] = [pos.x(), pos.y()]
        self.cfg.save()

    # ---------- 로그인 ----------
    def first_run_help(self):
        box = QMessageBox(QMessageBox.Information, "Xetical 처음 설정",
                          "구글 캘린더와 연동하려면 Google Cloud에서 만든\n"
                          "'데스크톱 앱' OAuth 클라이언트 파일(client_secret.json)이 필요합니다.\n\n"
                          "만드는 방법은 설명서(README.md)에 단계별로 적어 두었습니다.")
        box.setWindowFlag(Qt.WindowStaysOnTopHint)
        open_btn = box.addButton("파일 선택…", QMessageBox.AcceptRole)
        guide_btn = box.addButton("설명서 열기", QMessageBox.HelpRole)
        box.addButton("나중에", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is open_btn:
            self.choose_client_secret()
        elif box.clickedButton() is guide_btn:
            os.startfile(config.README_PATH)

    def choose_client_secret(self):
        path, _ = QFileDialog.getOpenFileName(None, "client_secret.json 선택",
                                              os.path.expanduser("~/Downloads"), "JSON (*.json)")
        if not path:
            return
        try:
            validate_client_secret(path)
        except (OSError, ValueError) as e:
            QMessageBox.warning(None, "Xetical", f"올바른 파일이 아닙니다.\n{e}")
            return
        shutil.copyfile(path, CLIENT_SECRET_PATH)
        self.gcal.logout()
        self.login()

    def login(self):
        if not self.gcal.has_client_secret():
            self.first_run_help()
            return
        if self._logging_in:
            return
        self._logging_in = True
        self.set_status("브라우저에서 구글 로그인을 완료해 주세요…")
        self.worker.submit(self.gcal.login, callback=self._on_login)

    def _on_login(self, _, err):
        self._logging_in = False
        if err:
            self.set_status("로그인 실패 — 여기를 클릭해 다시 시도", True)
            self.tray.showMessage("Xetical", f"로그인 실패: {err}", QSystemTrayIcon.Warning)
        else:
            self.set_status("")
            self.outbox.process()
            self.sync()

    def logout(self):
        self.gcal.logout()
        self.store.set([], [], {}, None)
        self.win.countdowns = []
        self._refresh_cards()
        config.CACHE_PATH.unlink(missing_ok=True)
        self._last_sync = None
        self.win.cloud_state = "login"
        self.set_status("구글 로그인 필요 — 여기를 클릭", True)

    # ---------- 공용 도우미 ----------
    def _keep(self, dlg):
        self._dialogs.append(dlg)
        dlg.destroyed.connect(lambda *_: self._dialogs.remove(dlg) if dlg in self._dialogs else None)
        if hasattr(dlg, "finished"):
            dlg.finished.connect(lambda *_: self._dialogs.remove(dlg) if dlg in self._dialogs else None)
        bring_to_front(dlg)

    def _need_login(self):
        if self.gcal.has_token():
            return False
        self.status_action()
        return True

    # ---------- 도구 ----------
    def open_day_view(self, d):
        from .dayview import DayView
        if self._dayview is not None and self._dayview.isVisible():
            self._dayview.set_day(d)
        else:
            self._dayview = DayView(self, d)
        bring_to_front(self._dayview)

    def pick_date(self):
        self._keep(DatePickDialog(self.win.anchor, self.win.go_to))

    def open_search(self):
        from .tools import SearchDialog
        self._keep(SearchDialog(self))

    def open_export(self):
        if self._need_login():
            return
        from .tools import ExportDialog
        self._keep(ExportDialog(self))

    def open_settings(self, page=0):
        from .settings import SettingsWindow
        if self._settings is None or not self._settings.isVisible():
            self._settings = SettingsWindow(self)
        self._settings.nav.setCurrentRow(page)
        bring_to_front(self._settings)

    def set_adjusting(self, on):
        self.win.set_adjusting(on)
        if not on:
            g = self.win.geometry()
            self.cfg["geometry"] = [g.x(), g.y(), g.width(), g.height()]
            self.cfg.save()

    def shutdown(self):
        self.click_hook.stop()
        self.hotkeys.clear()

    # ---------- 자동 실행 ----------
    @staticmethod
    def get_autostart():
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                return bool(winreg.QueryValueEx(k, RUN_NAME)[0])
        except OSError:
            return False

    @staticmethod
    def set_autostart(on):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
                if on:
                    winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, autostart_command())
                else:
                    try:
                        winreg.DeleteValue(k, RUN_NAME)
                    except FileNotFoundError:
                        pass
        except OSError as e:
            QMessageBox.warning(None, "Xetical", f"자동 실행 설정 실패: {e}")


def fit_rect(rect):
    """rect가 가장 많이 걸친 화면의 작업 영역 안으로 들어오게 옮기고, 너무 크면 줄인다(1-9)."""
    screens = QGuiApplication.screens()
    best = max(screens, key=lambda s: s.availableGeometry().intersected(rect).width()
               * s.availableGeometry().intersected(rect).height(), default=None)
    if best is None or best.availableGeometry().intersected(rect).isEmpty():
        best = QGuiApplication.primaryScreen()
    a = best.availableGeometry()
    w, h = min(rect.width(), a.width()), min(rect.height(), a.height())
    x = min(max(rect.x(), a.left()), a.right() - w + 1)
    y = min(max(rect.y(), a.top()), a.bottom() - h + 1)
    return QRect(x, y, w, h)


def main():
    # 오류 기록은 1MB씩 최대 3개 파일까지만 남긴다(1-12)
    handler = RotatingFileHandler(config.LOG_PATH, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler],
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sys.excepthook = lambda *exc: log.error("처리되지 않은 예외", exc_info=exc)

    # 작업 표시줄이 python 아이콘 대신 Xetical 아이콘으로 묶이도록 앱 ID를 지정한다
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Xetical.DesktopCalendar")
    except (AttributeError, OSError):
        pass
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Xetical")
    app.setWindowIcon(app_icon())  # 설정 창·대화상자 제목줄 아이콘

    lock = QLockFile(str(config.LOCK_PATH))
    if not lock.tryLock(100):
        QMessageBox.information(None, "Xetical", "Xetical이 이미 실행 중입니다. (작업 표시줄 트레이 아이콘 확인)")
        return 0

    calendar_app = App()
    calendar_app.start()
    code = app.exec()
    calendar_app.shutdown()
    lock.unlock()
    return code
