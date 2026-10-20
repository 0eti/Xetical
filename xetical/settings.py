"""설정 창. DesktopCal 설정(캘린더 / 셀 / 글꼴 및 색상 / 고급)의 구성·순서·항목 이름을 따른다.
바꾸는 즉시 적용되고 저장된다(DesktopCal과 같다)."""
import os

from PySide6.QtCore import Qt, QTime, QTimer
from PySide6.QtGui import QFont, QKeySequence
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFontDialog, QFrame, QHBoxLayout,
                               QInputDialog, QKeySequenceEdit, QLabel, QListWidget, QListWidgetItem, QMessageBox,
                               QPushButton, QRadioButton, QScrollArea, QStackedWidget, QTimeEdit, QVBoxLayout,
                               QWidget)

from . import config
from .dialogs import color_icon
from .gcal import HOLIDAY_REGIONS, is_holiday_calendar
from .widgets import SOUNDS, ColorButton, OnOff, is_dark, play_sound

_STYLE = """
QWidget#settings {{ background:{bg}; }}
QListWidget#nav {{ background:{nav}; border:none; border-right:1px solid {line}; font-size:14px; outline:0; }}
QListWidget#nav::item {{ padding:12px 18px; color:{nav_text}; }}
QListWidget#nav::item:hover {{ background:{hover}; }}
QListWidget#nav::item:selected {{ background:{bg}; color:{accent}; font-weight:bold; border-left:3px solid {accent}; }}
QLabel#section {{ font-size:14px; font-weight:bold; color:{title}; padding-top:14px; }}
QLabel#label {{ color:{text}; }}
QLabel#hint {{ color:{hint}; font-size:11px; }}
QFrame#hr {{ background:{line}; max-height:1px; min-height:1px; border:none; }}
QScrollArea {{ border:none; background:{bg}; }}
QScrollArea > QWidget > QWidget {{ background:{bg}; }}
QRadioButton, QCheckBox {{ color:{text}; }}
QRadioButton::indicator {{ width:14px; height:14px; border-radius:8px; border:2px solid {hint}; background:{bg}; }}
QRadioButton::indicator:checked {{ border:2px solid {accent}; background:qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5, stop:0 {accent}, stop:0.45 {accent}, stop:0.55 {bg}, stop:1 {bg}); }}
QRadioButton:checked {{ color:{accent}; font-weight:bold; }}
"""

# Windows 다크 모드면 어두운 색, 아니면 DesktopCal 설정 창 같은 밝은 색
LIGHT = dict(bg="#ffffff", nav="#f4f5f7", nav_text="#444444", hover="#e9ebef", line="#ece4e1", accent="#2f80ed",
             title="#222222", text="#333333", hint="#9a9a9a")
DARK = dict(bg="#202020", nav="#2b2b2b", nav_text="#d6d6d6", hover="#333333", line="#3a3a3a", accent="#4ea1ff",
            title="#ffffff", text="#e8e8e8", hint="#a0a0a0")


def style():
    return _STYLE.format(**(DARK if is_dark() else LIGHT))

WEEKDAY_FULL = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]


class Page(QWidget):
    def __init__(self):
        super().__init__()
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(24, 4, 24, 24)
        self.lay.setSpacing(6)

    def section(self, title):
        lbl = QLabel(title)
        lbl.setObjectName("section")
        self.lay.addWidget(lbl)

    def row(self, label, *widgets, hint=None):
        box = QHBoxLayout()
        text = QVBoxLayout()
        lbl = QLabel(label)
        lbl.setObjectName("label")
        text.addWidget(lbl)
        if hint:
            h = QLabel(hint)
            h.setObjectName("hint")
            h.setWordWrap(True)
            text.addWidget(h)
        box.addLayout(text, 1)
        for w in widgets:
            box.addWidget(w)
        self.lay.addLayout(box)
        hr = QFrame()
        hr.setObjectName("hr")
        self.lay.addWidget(hr)

    def widget_row(self, widget):
        self.lay.addWidget(widget)

    def finish(self):
        self.lay.addStretch()


class SettingsWindow(QWidget):
    def __init__(self, app):
        super().__init__(None, Qt.Window | Qt.WindowStaysOnTopHint)
        self.app, self.cfg = app, app.cfg
        self.setObjectName("settings")
        self.setWindowTitle("캘린더 설정")
        self.setStyleSheet(style())
        self.resize(780, 640)
        self.setMinimumWidth(700)
        self._save_timer = QTimer(self, singleShot=True, interval=400, timeout=self.cfg.save)

        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(150)
        self.stack = QStackedWidget()
        for title, build in (("캘린더", self._page_calendar), ("셀", self._page_cell),
                             ("글꼴 및 색상", self._page_font_color), ("고급", self._page_advanced),
                             ("할 일 · 알림", self._page_tasks), ("계정", self._page_account),
                             ("정보", self._page_about)):
            self.nav.addItem(QListWidgetItem(title))
            page = build()
            page.finish()
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setWidget(page)
            self.stack.addWidget(scroll)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.nav)
        lay.addWidget(self.stack, 1)

    def restyle(self):
        """설정 창 테마를 바꾸면 색을 다시 칠한다."""
        self.setStyleSheet(style())
        for w in self.findChildren(OnOff):
            w._sync()

    def rebuild(self):
        """테마 적용·설정 가져오기처럼 여러 값이 한꺼번에 바뀌면 창을 새로 연다."""
        page = self.nav.currentRow()
        self.app._settings = None
        self.close()
        self.app.open_settings(page)

    # ---------- 공용 ----------
    def set(self, key, value):
        self.cfg[key] = value
        self._save_timer.start()
        self.app.settings_changed(key)

    def onoff(self, key):
        w = OnOff(bool(self.cfg[key]))
        w.toggledValue.connect(lambda v: self.set(key, v))
        return w

    def combo(self, key, items):
        c = QComboBox()
        c.setMinimumWidth(130)
        for value, label in items:
            c.addItem(label, value)
        c.setCurrentIndex(max(0, c.findData(self.cfg[key])))
        c.currentIndexChanged.connect(lambda _: self.set(key, c.currentData()))
        return c

    def color(self, key):
        b = ColorButton(self.cfg[key])
        b.colorChanged.connect(lambda c: self.set(key, c))
        return b

    # ---------- 캘린더 ----------
    def _page_calendar(self):
        p = Page()
        p.section("사용자 지정 스타일")
        modes = QWidget()
        row = QHBoxLayout(modes)
        row.setContentsMargins(0, 4, 0, 4)
        group = QButtonGroup(modes)
        for value, label in (("rolling", "변동 월"), ("month", "전체 월"), ("fixed_row", "고정 단일 행"),
                             ("rolling_row", "변동 단일 행")):
            rb = QRadioButton(label)
            rb.setChecked(self.cfg["display_mode"] == value)
            rb.toggled.connect(lambda on, v=value: on and self.set("display_mode", v))
            group.addButton(rb)
            row.addWidget(rb)
        row.addStretch()
        p.widget_row(modes)
        p.row("최대 행", self.combo("weeks", [(n, f"{n}") for n in range(1, 13)]),
              hint="변동 월에서 한 화면에 보이는 주(행) 수")
        p.row("지나간 주 표시", self.combo("passed_weeks", [(0, "표시 안 함")] + [(n, f"{n}주") for n in range(1, 5)]))
        p.row("최대 열 수", self.combo("max_columns", [(n, f"{n}") for n in range(3, 32)]),
              hint="변동 단일 행에서 보이는 날짜 수")
        p.section("디스플레이 효과")
        p.row("유리 효과", self.onoff("glass"), hint="달력 뒤 바탕화면을 흐리게 비칩니다 (Windows 10 이상)")
        p.row("월 간격", self.onoff("month_interval"), hint="달이 바뀌는 경계에 구분선을 긋습니다")
        p.row("주 번호", self.onoff("week_number"))
        p.row("그림자", self.onoff("shadow"))
        p.row("제목 표시줄 표시", self.onoff("show_title_bar"))
        p.row("여러 날 일정을 막대로 표시", self.onoff("span_bars"), hint="이어지는 일정을 칸을 가로지르는 막대로 그립니다")
        p.section("날짜·제목 형식")
        p.row("1일 칸의 달 표시", self.combo("month_label", [("ko", "11월"), ("en", "Nov"), ("num", "11/1")]))
        p.row("요일 이름", self.combo("weekday_style", [("full", "월요일"), ("short", "월"), ("en", "Mon")]))
        title = QComboBox()
        title.setEditable(True)
        title.setMinimumWidth(230)
        for preset in ("오늘은 {year}년{month}월{day}일 {weekday}", "{year}.{month}.{day} {weekday}",
                       "{month}월 {day}일 {weekday} · {week}주차", "{year}년 {month}월"):
            title.addItem(preset)
        title.setEditText(self.cfg["title_format"])
        title.currentTextChanged.connect(lambda text: self.set("title_format", text))
        p.row("제목 표시줄 문구", title, hint="{year} {month} {day} {weekday} {week}(주차)를 넣을 수 있습니다")
        p.section("휴일")
        p.row("공휴일 표시", self.onoff("show_holidays"))
        p.row("기념일 표시", self.onoff("show_observances"))
        p.row("휴가 지역", self.combo("holiday_region", list(HOLIDAY_REGIONS.items())))
        p.section("음력 절기")
        p.row("음력", self.onoff("show_lunar"))
        p.row("24절기", self.onoff("show_solar_terms"))
        return p

    # ---------- 셀 ----------
    def _page_cell(self):
        p = Page()
        p.section("사용자 지정 스타일")
        bullets = QWidget()
        row = QHBoxLayout(bullets)
        row.setContentsMargins(0, 4, 0, 4)
        group = QButtonGroup(bullets)
        for value, label in (("none", "없음"), ("number", "번호 표시"), ("dot", "점 표시")):
            rb = QRadioButton(label)
            rb.setChecked(self.cfg["bullet"] == value)
            rb.toggled.connect(lambda on, v=value: on and self.set("bullet", v))
            group.addButton(rb)
            row.addWidget(rb)
        row.addStretch()
        p.widget_row(bullets)
        p.row("셀 간격", self.combo("cell_gap", [(n, f"{n}") for n in range(0, 11)]))
        p.row("줄 간격", self.combo("line_gap", [(n, f"{n}") for n in range(0, 11)]))
        p.section("함수")
        p.row("완료 작업 자동으로 맨 아래 줄로 이동", self.onoff("done_to_bottom"))
        p.row("완료된 작업 표시", self.onoff("show_done"))
        p.row("알림 시간 표시", self.onoff("show_alarm_time"))
        p.row("미완료 작업 30일 동안 자동 연기", self.onoff("auto_delay"),
              hint="지난 30일 동안 완료하지 않은 줄 일정을 오늘로 옮깁니다 (셀 편집기로 만든 일정만)")
        p.row("거절한 초대 일정 표시", self.onoff("show_declined"))
        p.row("칸당 최대 일정 수", self.combo("max_items", [(0, "제한 없음")] + [(n, f"{n}개") for n in range(1, 11)]),
              hint="넘치면 '…외 N개'로 줄여 보여 줍니다")
        p.row("일정을 한 줄로 줄여 표시", self.onoff("single_line"), hint="긴 일정은 줄바꿈 대신 '…'로 자릅니다")
        p.row("구글 일정 색을 글자색으로", self.onoff("calendar_text_color"),
              hint="글자색을 따로 정하지 않은 일정은 구글 캘린더 색으로 씁니다")
        p.section("시간 표시")
        p.row("시간 형식", self.combo("time_format", [("24h", "24시간 (14:30)"), ("ampm", "오전·오후 (오후 2:30)")]))
        p.row("끝나는 시간 표시", self.onoff("show_end_time"), hint="[14:30-15:30] 처럼 끝나는 시각도 보여 줍니다")
        p.section("프롬프트 사운드 효과")
        for key, label in (("done_sound", "편집 완료 알람"), ("alarm_sound", "리마인드 알람")):
            c = self.combo(key, list(SOUNDS.items()))
            test = QPushButton("▶")
            test.setFixedWidth(30)
            test.setToolTip("미리 듣기")
            test.clicked.connect(lambda _=False, k=key: play_sound(self.cfg[k]))
            p.row(label, c, test)
        return p

    # ---------- 글꼴 및 색상 ----------
    def _font_button(self, key):
        spec = self.cfg[key]
        b = QPushButton()
        b.setMinimumWidth(170)

        def label():
            s = self.cfg[key]
            b.setText(f"{s['family']}  {s['size']}pt{' 굵게' if s['bold'] else ''}")
            b.setStyleSheet(f"QPushButton{{background:#555;color:{s['color']};padding:4px 8px;}}")
        label()

        def pick():
            s = self.cfg[key]
            f = QFont(s["family"])
            f.setPointSizeF(s["size"])
            f.setBold(s["bold"])
            ok, font = QFontDialog.getFont(f, self, "글꼴")
            if isinstance(ok, QFont):  # PySide 버전에 따라 반환 순서가 다르다
                ok, font = font, ok
            if ok:
                self.set(key, dict(s, family=font.family(), size=round(font.pointSizeF()), bold=font.bold()))
                label()
        b.clicked.connect(pick)
        col = ColorButton(spec["color"])
        col.colorChanged.connect(lambda c: (self.set(key, dict(self.cfg[key], color=c)), label()))
        return b, col

    def _page_font_color(self):
        p = Page()
        p.section("테마")
        themes = QComboBox()
        themes.setMinimumWidth(170)
        themes.addItems(self.app.theme_names())
        apply_btn = QPushButton("적용")
        apply_btn.clicked.connect(lambda: (self.app.apply_theme(themes.currentText()), self.rebuild()))
        p.row("미리 만든 색 조합", themes, apply_btn, hint="색과 투명도만 바뀌고 글꼴 종류·크기는 그대로입니다")
        save_btn = QPushButton("내 테마로 저장…")
        save_btn.clicked.connect(self._save_theme)
        p.row("지금 색 조합 저장", save_btn)
        p.section("글꼴 색상 및 텍스트 크기")
        p.row("캘린더 글꼴", *self._font_button("date_font"), hint="날짜 숫자와 요일")
        p.row("편집 내용의 글꼴 크기 및 색상", *self._font_button("item_font"), hint="칸에 적힌 일정")
        p.section("기타 색상")
        p.row("캘린더 배경", self.color("cell_bg"))
        p.row("휴일 배경색", self.color("holiday_bg"))
        p.row("오늘의 테두리", self.color("today_border"))
        p.row("오늘 테두리 두께", self.combo("today_border_width", [(n, f"{n}px") for n in range(1, 6)]))
        fill = ColorButton(self.cfg["today_fill"], allow_none=True)
        fill.colorChanged.connect(lambda c: self.set("today_fill", c))
        p.row("오늘 칸 채우기", fill, hint="오늘 칸에 덧칠할 색 (없음 = 칠하지 않음)")
        p.row("제목 표시줄 버튼 색상", self.color("title_button_color"))
        p.row("휴일 텍스트 색상", self.color("holiday_text"))
        p.row("24절기 텍스트 색상", self.color("lunar_text"), hint="음력·절기·주 번호")
        p.section("요일별 날짜 색")
        days = QWidget()
        grid = QHBoxLayout(days)
        grid.setContentsMargins(0, 0, 0, 0)
        for i in (6, 0, 1, 2, 3, 4, 5):
            col = QVBoxLayout()
            lbl = QLabel(WEEKDAY_FULL[i][0])
            lbl.setObjectName("label")
            lbl.setAlignment(Qt.AlignCenter)
            btn = ColorButton(self.cfg["weekday_colors"].get(str(i), ""), allow_none=True)
            btn.setFixedWidth(46)
            btn.colorChanged.connect(lambda c, i=i: self._weekday_color(i, c))
            col.addWidget(lbl)
            col.addWidget(btn)
            grid.addLayout(col)
        grid.addStretch()
        p.widget_row(days)
        hint = QLabel("날짜 숫자와 요일 이름의 색. '없음'이면 캘린더 글꼴 색을 씁니다 (공휴일은 휴일 텍스트 색)")
        hint.setObjectName("hint")
        p.widget_row(hint)
        p.section("투명도")
        steps = [(n, "0% (단색)" if n == 0 else "100% (완전 투명)" if n == 100 else f"{n}%") for n in range(0, 101, 10)]
        p.row("투명도", self.combo("transparency", steps))
        p.row("유리 효과", self.onoff("glass"))
        p.row("카운트다운 카드 투명도", self.combo("card_transparency", steps))
        p.row("카운트다운 카드 크기", self.combo("card_size", [("small", "작게"), ("normal", "보통"), ("large", "크게")]))
        return p

    def _weekday_color(self, i, color):
        cols = dict(self.cfg["weekday_colors"])
        if color:
            cols[str(i)] = color
        else:
            cols.pop(str(i), None)
        self.set("weekday_colors", cols)

    def _save_theme(self):
        name, ok = QInputDialog.getText(self, "내 테마로 저장", "테마 이름")
        if ok and name.strip():
            self.app.save_custom_theme(name.strip())
            self.rebuild()

    # ---------- 고급 ----------
    def _hotkey_row(self, p, label, key):
        edit = QKeySequenceEdit(QKeySequence(self.cfg[key]))
        edit.setMaximumSequenceLength(1) if hasattr(edit, "setMaximumSequenceLength") else None
        edit.setMinimumWidth(140)
        edit.editingFinished.connect(lambda: self.set(key, edit.keySequence().toString()))
        p.row(label, edit, self.onoff(key + "_on"), hint="키보드로 직접 입력하고 수정할 수 있습니다.")

    def _page_advanced(self):
        p = Page()
        p.section("고급 설정")
        p.row("시작 요일", self.combo("week_start", [(i, WEEKDAY_FULL[i]) for i in (6, 0, 1, 2, 3, 4, 5)]))
        p.row("첫번째 주", self.combo("first_week", [("jan1", "1월 1일이 포함된 주"), ("iso", "4일 이상 포함된 첫 주"),
                                                   ("full", "첫 번째 온전한 주")]),
              hint="올해 X주차를 첫 번째 주로 설정합니다.")
        p.section("휴무일")
        p.row("쉬는날 표시", self.onoff("show_off_days"))
        days = QWidget()
        row = QHBoxLayout(days)
        row.setContentsMargins(0, 0, 0, 0)
        for i, name in enumerate(WEEKDAY_FULL):
            cb = QCheckBox(name[0])  # 월·화… 한 글자로 줄여 창 폭을 넘지 않게
            cb.setChecked(i in self.cfg["off_days"])
            cb.toggled.connect(lambda on, i=i: self.set(
                "off_days", sorted(set(self.cfg["off_days"]) | {i}) if on else [d for d in self.cfg["off_days"] if d != i]))
            row.addWidget(cb)
        p.row("쉬는날", days)
        p.section("단축키")
        self._hotkey_row(p, "캘린더 숨기기 단축키", "hotkey_toggle")
        self._hotkey_row(p, "예약된 작업 단축키 표시 및 숨기기", "hotkey_content")
        self._hotkey_row(p, "편집 완료 단축키", "hotkey_done")
        p.section("소프트웨어 설정")
        lang = QComboBox()
        lang.addItem("한국어")
        lang.setEnabled(False)
        p.row("언어선택", lang)
        auto = OnOff(self.app.get_autostart())
        auto.toggledValue.connect(self.app.set_autostart)
        p.row("Windows시작시 실행", auto)
        p.row("마우스 휠로 주 이동", self.onoff("wheel_paging"),
              hint="Xetical 추가 기능. 꺼져 있으면 DesktopCal처럼 휠은 넘치는 칸 내용만 스크롤합니다.")
        p.row("바탕화면 아이콘 클릭 통과", self.onoff("click_through"),
              hint="달력 아래에 있는 바탕화면 아이콘도 클릭할 수 있습니다. 빈 곳을 누르면 달력으로 동작합니다.")
        p.row("설정 창 테마", self.combo("ui_theme", [("auto", "Windows 설정 따르기"), ("dark", "어둡게"),
                                                  ("light", "밝게")]))
        p.row("트레이 아이콘에 날짜 표시", self.onoff("tray_show_date"))
        return p

    # ---------- 할 일 · 알림 ----------
    def _alarm_days(self):
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        for days, label in ((0, "당일"), (1, "1일 전"), (2, "2일 전"), (3, "3일 전"), (5, "5일 전"), (7, "1주 전")):
            cb = QCheckBox(label)
            cb.setChecked(days in self.cfg["default_alarm_days"])
            cb.toggled.connect(lambda on, d=days: self.set("default_alarm_days", sorted(
                set(self.cfg["default_alarm_days"]) | {d}) if on else [x for x in self.cfg["default_alarm_days"] if x != d]))
            row.addWidget(cb)
        return box

    def _time_edit(self, key):
        t = QTimeEdit()
        t.setDisplayFormat("HH:mm")
        hh, mm = (int(x) for x in self.cfg[key].split(":"))
        t.setTime(QTime(hh, mm))
        t.timeChanged.connect(lambda v: self.set(key, f"{v.hour():02d}:{v.minute():02d}"))
        return t

    def _page_tasks(self):
        p = Page()
        p.section("할 일")
        p.row("할 일 표시", self.onoff("show_tasks"), hint="구글 할 일(Tasks) 중 마감일이 있는 것")
        p.row("완료한 할 일 표시", self.onoff("show_done_tasks"))
        p.row("반복 할 일 '예정' 표시", self.onoff("show_task_projection"),
              hint="반복 할 일의 앞으로 날짜를 흐리게 보여 줍니다")
        p.section("새 일정 기본 알림")
        p.row("새 일정에 알림 걸기", self.onoff("default_alarm_on"), hint="일정 창에서 새로 만들 때 알림을 미리 켭니다")
        p.row("알림 시각", self._time_edit("default_alarm_time"))
        p.row("알림 날짜", self._alarm_days())
        p.row("칸 편집기로 만든 줄에도 적용", self.onoff("default_alarm_lines"))
        p.section("아침 요약 알림")
        p.row("아침 요약 알림", self.onoff("summary_on"),
              hint="매일 정한 시각에 오늘 일정과 할 일을 알려 줍니다. PC를 늦게 켜도 그날 한 번은 보여 줍니다.")
        p.row("요약 시각", self._time_edit("summary_time"))
        preview = QPushButton("지금 보기")
        preview.clicked.connect(lambda: self.app.reminders.check_summary(force=True))
        p.row("미리 보기", preview)
        return p

    # ---------- 계정(Xetical) ----------
    def _page_account(self):
        p = Page()
        p.section("구글 계정")
        app = self.app
        status = QLabel()
        login = QPushButton()

        def refresh():
            primary = next((c["id"] for c in app.store.calendars if c["primary"]), "")
            logged = app.gcal.has_token()
            status.setText(primary if logged and primary else "로그인됨" if logged else "로그인되어 있지 않음")
            login.setText("로그아웃" if logged else "로그인")
        refresh()
        login.clicked.connect(lambda: ((app.logout if app.gcal.has_token() else app.login)(), refresh()))
        p.row("계정", status, login)
        secret = QPushButton("불러오기…")
        secret.clicked.connect(app.choose_client_secret)
        p.row("OAuth 클라이언트 (client_secret.json)", secret)
        p.section("동기화")
        p.row("자동 동기화", self.onoff("auto_sync"))
        p.row("동기화 간격", self.combo("refresh_minutes", [(n, f"{n}분") for n in (1, 5, 15, 30, 60)]))
        ranges = [(n, f"{n}개월") for n in (1, 3, 6, 12, 24)]
        p.row("동기화 범위 (과거)", self.combo("sync_past_months", ranges), hint="오늘부터 얼마 전까지 받아 둘지")
        p.row("동기화 범위 (미래)", self.combo("sync_future_months", ranges), hint="오늘부터 얼마 뒤까지 받아 둘지")
        p.section("캘린더")
        cal_list = QListWidget()
        cal_list.setMinimumHeight(110)
        default = QComboBox()
        for c in app.store.calendars:
            if is_holiday_calendar(c["id"]):
                continue
            item = QListWidgetItem(color_icon(c["color"]), c["summary"])
            item.setData(Qt.UserRole, c["id"])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if app.win.visible_calendar(c["id"]) else Qt.Unchecked)
            cal_list.addItem(item)
            if c["writable"]:
                default.addItem(color_icon(c["color"]), c["summary"], c["id"])
        if not cal_list.count():
            cal_list.addItem("(로그인 후 동기화하면 캘린더 목록이 나타납니다)")

        def vis_changed(item):
            cid = item.data(Qt.UserRole)
            if cid:
                self.cfg["calendar_visibility"][cid] = item.checkState() == Qt.Checked
                self.set("calendar_visibility", self.cfg["calendar_visibility"])
        cal_list.itemChanged.connect(vis_changed)
        p.lay.addWidget(QLabel("표시할 캘린더"))
        p.lay.addWidget(cal_list)
        primary = next((c["id"] for c in app.store.calendars if c["primary"]), "")
        default.setCurrentIndex(max(0, default.findData(self.cfg["default_calendar"] or primary)))
        default.currentIndexChanged.connect(lambda _: self.set("default_calendar", default.currentData()))
        p.row("새 일정 기본 캘린더", default)
        p.row("일정 왼쪽에 캘린더 색 막대 표시", self.onoff("show_color_bar"))
        return p

    # ---------- 정보 ----------
    def _page_about(self):
        p = Page()
        p.section("Xetical")
        p.row("버전", QLabel(config.APP_VERSION))
        for label, path in (("데이터 폴더", config.DATA_DIR), ("오류 기록", config.LOG_PATH), ("사용 안내", config.README_PATH)):
            b = QPushButton("열기")
            b.clicked.connect(lambda _=False, path=path: os.startfile(path) if os.path.exists(path) else None)
            p.row(label, b)
        p.section("설정 백업")
        exp = QPushButton("내보내기…")
        exp.clicked.connect(self._export)
        imp = QPushButton("가져오기…")
        imp.clicked.connect(self._import)
        p.row("설정 파일", exp, imp, hint="설정과 칸 배경색을 파일로 저장하거나 불러옵니다 (위치·로그인 정보는 빼고)")
        info = QLabel("DesktopCal의 화면과 설정을 본떠 만든 구글 캘린더용 바탕화면 달력입니다.\n"
                      "일정은 구글 캘린더에 저장되고, 설정·셀 배경색은 이 PC의 data 폴더에 저장됩니다.")
        info.setObjectName("hint")
        info.setWordWrap(True)
        p.widget_row(info)
        return p

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(self, "설정 내보내기", os.path.expanduser("~/Documents/Xetical-설정.json"),
                                              "JSON (*.json)")
        if path:
            self.app.export_settings(path)
            QMessageBox.information(self, "Xetical", f"설정을 저장했습니다.\n{path}")

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(self, "설정 가져오기", os.path.expanduser("~/Documents"), "JSON (*.json)")
        if not path:
            return
        try:
            n = self.app.import_settings_file(path)
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Xetical", f"설정 파일을 읽지 못했습니다.\n{e}")
            return
        QMessageBox.information(self, "Xetical", f"설정 {n}개를 적용했습니다.")
        self.rebuild()
