"""Xetical 본체: 창, 트레이, 동기화, 대화상자를 엮는다. 메뉴 구성은 DesktopCal을 따른다."""
import copy
import ctypes
import json
import logging
import os
import shutil
import sys
import uuid
import webbrowser
import winreg
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from logging.handlers import RotatingFileHandler

from PySide6.QtCore import QLockFile, QObject, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu, QMessageBox, QSystemTrayIcon

from . import config, desktop
from .cards import CountdownCard, default_position
from .config import CELLS_PATH, CLIENT_SECRET_PATH, DEFAULTS, LOCAL_ONLY_KEYS, Config, load_json, save_json
from .dialogs import (DatePickDialog, EventDialog, MobileDialog, TaskDialog, ask_scope, bring_to_front,
                      color_icon, repeat_dates)
from .editor import CellEditor
from .gcal import ApiDisabledError, AuthError, GCal, event_props, is_holiday_calendar, validate_client_secret
from .hotkeys import Hotkeys
from .jobs import Outbox, is_offline
from .model import Event, EventStore
from .mousehook import ClickThrough
from .reminders import Reminders
from .themes import PRESETS, capture, theme_values
from .view import CalendarWindow
from .widgets import app_icon, apply_ui_theme, color_menu, play_sound
from .widgets import confirm as ask_confirm

log = logging.getLogger("xetical")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "Xetical"
FETCH_MARGIN = timedelta(days=42)
HK_TOGGLE, HK_CONTENT = 1, 2
UNDO_LIMIT = 50
PAGE_ACCOUNT, PAGE_ABOUT = 5, 6  # 설정 창 페이지 번호

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
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    exe = pyw if os.path.exists(pyw) else sys.executable
    return f'"{exe}" "{config.BASE_DIR / "main.pyw"}"'


def new_tmp():
    return f"tmp-{uuid.uuid4().hex[:12]}"


def months(n):
    return timedelta(days=31 * n)


class App(QObject):
    def __init__(self):
        super().__init__()
        self.cfg = Config.load()
        self.cfg.setdefault("recent_colors", [])
        apply_ui_theme(self.cfg["ui_theme"])
        self.gcal = GCal()
        self.worker = Worker()
        self.outbox = Outbox(self.gcal, self.worker)
        self.store = EventStore()
        self.store.load_cache()
        self.cell_colors = load_json(CELLS_PATH, {})
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
        add(m, "프린트…", self.print_calendar)
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
        data["_xetical"] = config.APP_VERSION
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def import_settings_file(self, path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("Xetical 설정 파일이 아닙니다.")
        colors = data.pop("cell_colors", None)
        values = {k: v for k, v in data.items() if k in DEFAULTS and k not in LOCAL_ONLY_KEYS}
        self.import_settings(values)
        if isinstance(colors, dict):
            self.import_cell_colors(colors)
        return len(values)

    def default_calendar(self):
        writable = [c for c in self.store.calendars if c["writable"] and not is_holiday_calendar(c["id"])]
        want = self.cfg["default_calendar"]
        if any(c["id"] == want for c in writable):
            return want
        return next((c["id"] for c in writable if c["primary"]), writable[0]["id"] if writable else "")

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
            if self._dayview is not None and self._dayview.isVisible():
                self._dayview.refresh()
        self.win.update()
        if self._sync_again:
            self._sync_again = False
            self.sync()

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
            self.worker.submit(self.gcal.get_rrule, ev,
                               callback=lambda r, e: (self.set_status(""), self._open_event_dialog(ev, r[0] if r else "")))
        else:
            self._open_event_dialog(ev, "")

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
            return
        old = ev.done
        ev.done = not old
        jobs = [("set_task_done", [ev, old], [])]
        self.outbox.add("set_task_done", ev, ev.done, label="할 일 완료" if ev.done else "할 일 미완료")
        if ev.done:
            play_sound(self.cfg["done_sound"])
            jobs += self._create_next_occurrence(ev)
        self.push_undo("할 일 완료" if ev.done else "할 일 미완료", jobs)
        self._after_task_change()

    def _task_rule(self, ev):
        return self.cfg["task_repeats"].get(self._task_key(ev.task_list, ev.title))

    def _series(self, ev):
        """같은 목록·같은 이름의 할 일들(반복 할 일의 회차들)."""
        return [e for e in self.store.events if e.task_list == ev.task_list and e.title == ev.title and not e.virtual]

    def _create_next_occurrence(self, ev):
        """반복 할 일의 다음 회차를 진짜 할 일로 만든다. 이미 그 날짜(또는 그 뒤) 회차가 있으면 만들지 않는다.
        되돌리기용 반대 작업 목록을 돌려준다."""
        rule = self._task_rule(ev)
        nxt = repeat_dates(ev.start, rule, ev.start + timedelta(days=400))[:1] if rule else []
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
            self._remember_task_repeat(v["list_id"], v["title"], v["repeat"])
            tmp = new_tmp()
            ev = Event(id=tmp, calendar_id=f"tasks:{v['list_id']}", title=v["title"], start=v["due"],
                       end=v["due"] + timedelta(days=1), all_day=True, task_list=v["list_id"], description=v["notes"])
            self._add_temp(ev)
            self.outbox.add("create_task", v["list_id"], v["title"], v["notes"], v["due"], tmps=[tmp], label="새 할 일")
            self.push_undo("새 할 일", [("delete_task", [ev], [])])
        dlg.accepted.connect(done)
        self._keep(dlg)

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
                for i, d in enumerate(repeat_dates(ev.start, reps[key], until)):
                    self.store.events.append(Event(
                        id=f"vtask:{i}:{ev.id}", calendar_id=ev.calendar_id, title=ev.title, start=d,
                        end=d + timedelta(days=1), all_day=True, color=ev.color, task_list=ev.task_list,
                        virtual=True, source_id=ev.id, writable=False))
        self.store.reindex()
        self.win.update()

    def _open_task_dialog(self, ev):
        rule = self.cfg["task_repeats"].get(self._task_key(ev.task_list, ev.title), "none")
        dlg = TaskDialog(self.task_lists(), event=ev, repeat=rule)

        def done():
            v = dlg.values()
            if dlg.action == "save":
                old = (ev.title, ev.description, ev.start, ev.done)
                self._remember_task_repeat(ev.task_list, v["title"], v["repeat"], old_title=ev.title)
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

    def print_calendar(self):
        from .tools import print_calendar
        print_calendar(self.win)

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
