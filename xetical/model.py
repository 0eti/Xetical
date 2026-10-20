"""일정 데이터 모델과 메모리 저장소(+ 오프라인 캐시)."""
import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta

from .config import CACHE_PATH

COUNTDOWN_TYPES = {"countdown": "카운트다운", "birthday": "생일", "memorial": "기념일", "festival": "제전", "exam": "시험"}


@dataclass
class Event:
    id: str
    calendar_id: str
    title: str
    start: object          # all_day면 date, 아니면 로컬 naive datetime
    end: object            # all_day면 '다음 날'(exclusive) date
    all_day: bool
    color: str = "#4a90d9"           # 캘린더/구글 일정 색
    description: str = ""
    location: str = ""
    recurring: bool = False
    html_link: str = ""
    recurring_event_id: str = ""
    # DesktopCal식 속성(구글 extendedProperties.private 에 저장되어 다른 PC와도 맞춰진다)
    done: bool = False               # 완료(취소선)
    text_color: str = ""             # 글자색("" = 기본)
    order: int = 0                   # 셀 안 줄 순서
    line: bool = False               # 셀 편집기에서 만든 줄
    alarm: str = ""                  # "HH:MM|0,1,7" = 알림 시각|며칠 전 목록
    countdown: str = ""              # "type|show|fmt" (show: bar/card/both, fmt: left/passed)
    popup_minutes: list = field(default_factory=list)  # 구글 팝업 알림(분 전)
    writable: bool = True
    task_list: str = ""              # 구글 할 일이면 그 목록 id
    virtual: bool = False            # 반복 할 일의 앞으로 예정(구글에는 없고 Xetical이 그린 것)
    source_id: str = ""              # virtual이면 원래 할 일의 id
    response: str = ""               # 초대받은 일정이면 내 응답: needsAction | accepted | tentative | declined
    organizer: str = ""              # 초대한 사람

    def days(self):
        if self.all_day:
            first, last = self.start, self.end - timedelta(days=1)
        else:
            first = self.start.date()
            last = (self.end - timedelta(microseconds=1)).date() if self.end > self.start else first
        last = max(first, last)
        return [first + timedelta(days=i) for i in range((last - first).days + 1)]

    @property
    def first_day(self):
        return self.start if self.all_day else self.start.date()

    @property
    def is_line_candidate(self):
        """셀 편집기에서 '한 줄'로 다룰 수 있는 일정: 하루짜리 종일, 쓰기 가능(반복 일정도 포함)."""
        return self.all_day and self.writable and len(self.days()) == 1 and not self.virtual \
            and not self.countdown and not self.alarm and not self.task_list and not self.response

    @property
    def is_multi_day(self):
        return not self.task_list and len(self.days()) > 1

    def values(self):
        """일정 편집 창 값과 같은 형식(되돌리기·복사에 쓴다). 종일 일정의 end는 마지막 날(포함)."""
        return {
            "title": self.title, "all_day": self.all_day, "calendar_id": self.calendar_id,
            "start": self.start, "end": self.end - timedelta(days=1) if self.all_day else self.end,
            "location": self.location, "description": self.description, "done": self.done,
            "text_color": self.text_color, "order": self.order, "line": self.line,
            "alarm": self.alarm, "countdown": self.countdown, "recurrence": None,  # None = 반복 규칙은 그대로
        }

    def sort_key(self):
        if self.all_day:
            return (0, self.order, datetime.combine(self.first_day, datetime.min.time()), self.title)
        return (1, 0, self.start, self.title)

    def alarm_parts(self):
        """(시, 분, [며칠 전…]) 또는 None"""
        if not self.alarm:
            return None
        try:
            hm, adv = (self.alarm.split("|") + [""])[:2]
            h, m = (int(x) for x in hm.split(":"))
            days = [int(x) for x in adv.split(",") if x.strip() != ""] or [0]
            return h, m, days
        except ValueError:
            return None

    def countdown_parts(self):
        if not self.countdown:
            return None
        kind, show, fmt = (self.countdown.split("|") + ["countdown", "bar", "left"])[:3]
        return kind or "countdown", show or "bar", fmt or "left"

    def to_json(self):
        d = asdict(self)
        d["start"] = self.start.isoformat()
        d["end"] = self.end.isoformat()
        return d

    @classmethod
    def from_json(cls, d):
        conv = date.fromisoformat if d["all_day"] else datetime.fromisoformat
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**dict(known, start=conv(d["start"]), end=conv(d["end"])))


class EventStore:
    def __init__(self):
        self.calendars = []   # [{id, summary, color, primary, writable, selected}]
        self.events = []
        self.holidays = {}    # date -> [(이름, 공휴일 여부)]
        self.range = None     # (date, date) 가져온 범위
        self._by_day = {}

    def set(self, calendars, events, holidays, rng):
        self.calendars, self.events, self.holidays, self.range = calendars, events, holidays, rng
        self._index()

    def _index(self):
        by_day = defaultdict(list)
        for ev in self.events:
            for d in ev.days():
                by_day[d].append(ev)
        for lst in by_day.values():
            lst.sort(key=Event.sort_key)
        self._by_day = by_day

    def reindex(self):
        self._index()

    def on_day(self, d, visible):
        return [e for e in self._by_day.get(d, ()) if visible(e.calendar_id)]

    def covers(self, a, b):
        return self.range is not None and self.range[0] <= a and b <= self.range[1]

    def calendar(self, cal_id):
        return next((c for c in self.calendars if c["id"] == cal_id), None)

    def holiday_names(self, d, public=True, observance=False):
        return [n for n, pub in self.holidays.get(d, ()) if (pub and public) or (not pub and observance)]

    def save_cache(self):
        data = {
            "calendars": self.calendars,
            "events": [e.to_json() for e in self.events if not e.virtual],
            "holidays": {d.isoformat(): v for d, v in self.holidays.items()},
        }
        tmp = CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
        tmp.replace(CACHE_PATH)

    def load_cache(self):
        try:
            data = json.loads(CACHE_PATH.read_text("utf-8"))
            holidays = {}
            for d, v in data["holidays"].items():
                holidays[date.fromisoformat(d)] = [(v, True)] if isinstance(v, str) else [tuple(x) for x in v]
            self.set(data["calendars"], [Event.from_json(e) for e in data["events"]], holidays, None)
        except (OSError, ValueError, KeyError, TypeError):
            pass
