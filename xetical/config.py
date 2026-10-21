"""설정 파일과 데이터 경로.

데이터 폴더(data):
- 소스로 실행(Xetical.bat): 프로그램 폴더의 data/
- exe: exe 옆에 portable.txt가 있으면 exe 폴더의 data/(포터블), 없으면 %APPDATA%\\Xetical
  (설치 프로그램으로 Program Files에 두면 그 폴더에는 쓸 수 없기 때문)

설정 항목은 DesktopCal 설정 창(캘린더 / 셀 / 글꼴 및 색상 / 고급)의 구성을 그대로 따른다.
기본값은 사용자가 쓰던 DesktopCal 설정값이다.
"""
import copy
import json
import os
import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)  # PyInstaller로 묶은 exe로 실행 중
APP_DIR = Path(sys.executable).parent if FROZEN else Path(__file__).resolve().parent.parent  # exe·main.pyw 폴더
BASE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))  # 함께 묶은 파일(README.md 등)이 있는 곳
if FROZEN and not (APP_DIR / "portable.txt").exists():
    DATA_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "Xetical"
else:
    DATA_DIR = APP_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = DATA_DIR / "config.json"
CLIENT_SECRET_PATH = DATA_DIR / "client_secret.json"
TOKEN_PATH = DATA_DIR / "token.json"
CACHE_PATH = DATA_DIR / "cache.json"
CELLS_PATH = DATA_DIR / "cells.json"      # 날짜별 셀 배경색
MOODS_PATH = DATA_DIR / "moods.json"      # 트래커: 트래커 id -> {날짜: 색}
FIRED_PATH = DATA_DIR / "fired.json"      # 이미 띄운 알림
OUTBOX_PATH = DATA_DIR / "outbox.json"    # 아직 구글에 못 보낸 변경(오프라인 대기)
LOCK_PATH = DATA_DIR / "xetical.lock"
LOG_PATH = DATA_DIR / "xetical.log"
README_PATH = BASE_DIR / "README.md"

APP_VERSION = "1.2.1"

# 설정 백업에서 빼는 항목(이 PC에만 의미가 있는 값)
LOCAL_ONLY_KEYS = {"geometry", "card_positions", "summary_last", "editor_size"}

DEFAULTS = {
    # ── 캘린더 ──
    "display_mode": "rolling",     # rolling=변동 월, month=전체 월, fixed_row=고정 단일 행, rolling_row=변동 단일 행
    "weeks": 6,                    # 최대 행
    "passed_weeks": 0,             # 지나간 주 표시
    "max_columns": 7,              # 최대 열 수(단일 행 모드)
    "glass": False,                # 유리 효과
    "month_interval": False,       # 월 간격
    "week_number": False,          # 주 번호
    "shadow": False,               # 그림자
    "show_title_bar": True,        # 제목 표시줄 표시
    "show_holidays": True,         # 공휴일 표시
    "show_observances": False,     # 기념일 표시
    "holiday_region": "south_korea",
    "show_lunar": False,           # 음력
    "show_solar_terms": False,     # 24절기
    # ── 셀 ──
    "bullet": "none",              # none=아니, number=번호, dot=점
    "cell_gap": 0,                 # 셀 간격(px)
    "line_gap": 0,                 # 줄 간격(px)
    "done_to_bottom": False,       # 완료 작업 자동으로 맨 아래 줄로 이동
    "show_done": True,             # 완료된 작업 표시
    "show_alarm_time": True,       # 알림 시간 표시
    "auto_delay": False,           # 미완료 작업 30일 동안 자동 연기
    "done_sound": "none",          # 편집 완료 알람
    "alarm_sound": "default",      # 리마인드 알람
    # ── 글꼴 및 색상 ──
    "date_font": {"family": "Malgun Gothic", "size": 9, "bold": True, "color": "#ffffc0"},
    "item_font": {"family": "Malgun Gothic", "size": 12, "bold": True, "color": "#ffffff"},
    "cell_bg": "#808080",          # 캘린더 배경
    "holiday_bg": "#505050",       # 휴일 배경색
    "today_border": "#282828",     # 오늘의 테두리
    "title_button_color": "#ffffff",
    "holiday_text": "#ff9d1c",     # 휴일 텍스트 색상
    "lunar_text": "#bdbdbd",       # 음력·24절기 텍스트 색상
    "transparency": 20,            # 투명도(%)
    "card_transparency": 20,       # 카운트다운 카드 투명도(%)
    # ── 고급 ──
    "week_start": 6,               # 6=일요일, 0=월요일 … (datetime.weekday)
    "first_week": "jan1",          # jan1=1월 1일이 있는 주, iso=첫 4일 주, full=첫 번째 온전한 주
    "show_off_days": True,         # 쉬는날 표시
    "off_days": [5, 6],            # 쉬는날(토, 일)
    "hotkey_toggle": "Ctrl+Alt+H", "hotkey_toggle_on": True,     # 캘린더 숨기기 단축키
    "hotkey_content": "Ctrl+Alt+J", "hotkey_content_on": False,  # 예약된 작업 표시 및 숨기기
    "hotkey_done": "Ctrl+D", "hotkey_done_on": True,             # 편집 완료 단축키
    # ── 표시 상태 ──
    "show_calendar": True,         # 캘린더 열기/숨기기
    "show_content": True,          # 예약된 작업 표시/숨기기
    "geometry": None,              # [x, y, w, h]
    "editor_size": [260, 230],
    "card_positions": {},          # 카운트다운 카드 위치 {event_id: [x, y]}
    # ── 구글 연동(Xetical 전용) ──
    "calendar_visibility": {},
    "default_calendar": "",
    "refresh_minutes": 5,
    "auto_sync": True,
    "show_color_bar": False,
    "wheel_paging": False,         # 마우스 휠로 주 이동(DesktopCal에는 없음)
    # 구글 할 일의 반복 주기. 구글 API가 반복 정보를 주지 않아서 Xetical이 따로 기억한다.
    # {"목록id|할 일 제목": "daily" | "weekly" | "biweekly" | "monthly" | "yearly"}
    "task_repeats": {},
    # ── 기능 추가분 ──
    "click_through": False,        # 바탕화면 아이콘 클릭 통과(아이콘은 아이콘으로, 빈 곳은 달력으로)
    "span_bars": True,             # 여러 날 일정을 막대로 표시
    "show_declined": False,        # 거절한 초대 일정 표시
    "show_tasks": True,            # 할 일 표시
    "show_done_tasks": True,       # 완료한 할 일 표시
    "show_task_projection": True,  # 반복 할 일 '예정' 표시
    "sync_past_months": 1,         # 동기화 범위(과거)
    "sync_future_months": 3,       # 동기화 범위(미래)
    "time_format": "24h",          # 24h | ampm
    "show_end_time": True,         # 끝나는 시간 표시
    "default_alarm_on": False,     # 새 일정 기본 알림
    "default_alarm_time": "09:00",
    "default_alarm_days": [0],     # 며칠 전(0 = 당일)
    "default_alarm_lines": False,  # 칸 편집기로 만든 줄에도 적용
    "ui_theme": "auto",            # 설정 창·대화상자 테마: auto | dark | light
    "weekday_colors": {},          # 요일별 날짜 색 {"0"(월)~"6"(일): "#rrggbb"}
    "today_border_width": 2,       # 오늘 테두리 두께(px)
    "today_fill": "",              # 오늘 칸 채우기 색("" = 없음)
    "calendar_text_color": False,  # 구글 일정 색을 글자색으로
    "max_items": 0,                # 칸당 최대 일정 수(0 = 제한 없음)
    "single_line": False,          # 일정을 한 줄로 줄여 표시
    "card_size": "normal",         # 카운트다운 카드 크기: small | normal | large
    "month_label": "ko",           # 1일 칸의 달 표시: ko(11월) | en(Nov) | num(11/1)
    "weekday_style": "full",       # 요일 이름: full(월요일) | short(월) | en(Mon)
    "title_format": "오늘은 {year}년{month}월{day}일 {weekday}",
    "tray_show_date": True,        # 트레이 아이콘에 날짜 표시
    "summary_on": False,           # 아침 요약 알림
    "summary_time": "08:30",
    "summary_last": "",            # 마지막으로 요약을 띄운 날짜
    "custom_themes": {},           # 내 테마 {이름: {설정…}}
    # 트래커(무드 트래커 등): 달력 아래 올해 365칸에 날마다 색을 칠한다. 구글과 동기화하지 않는다.
    "mood_on": False,
    "mood_layout": "week",         # week = 7줄(요일)×53주, month = 12줄(월)×31칸
    "mood_trackers": [
        {"id": "mood", "name": "기분", "colors": [
            {"color": "#00b050", "label": "좋음"}, {"color": "#ffc000", "label": "보통"},
            {"color": "#ff0000", "label": "나쁨"}]},
        {"id": "workout", "name": "운동", "colors": [{"color": "#0070c0", "label": "운동함"}]},
    ],
    "mood_active": "mood",         # 지금 보이는 트래커 id
}

# 예전 버전 설정 이름 → 새 이름
_LEGACY = {"font_family": None, "font_size": None, "opacity": None}


class Config(dict):
    @classmethod
    def load(cls):
        cfg = cls(copy.deepcopy(DEFAULTS))
        try:
            saved = json.loads(CONFIG_PATH.read_text("utf-8"))
        except (OSError, ValueError):
            saved = {}
        for k, v in saved.items():
            if k in _LEGACY:
                continue
            if isinstance(DEFAULTS.get(k), dict) and isinstance(v, dict) and k in ("date_font", "item_font"):
                cfg[k].update(v)
            else:
                cfg[k] = v
        return cfg

    @property
    def opacity(self):
        return max(0.0, min(1.0, 1 - self["transparency"] / 100))

    def save(self):
        tmp = CONFIG_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(self, ensure_ascii=False, indent=2), "utf-8")
        tmp.replace(CONFIG_PATH)


def load_json(path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return default


def save_json(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    tmp.replace(path)
