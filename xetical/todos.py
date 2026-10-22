"""반복 할 일 = 구글 캘린더의 하루 종일 반복 일정 + '할 일' 표시(xeticalTodo).
구글 Tasks API에는 반복 칸이 없어(만들 수도, 읽을 수도 없다) 반복 할 일은 캘린더 반복 일정으로 둔다.
여기에는 Qt·gcal을 쓰지 않는 계산만 둔다(gcal도 이것을 쓰므로 dialogs를 거치면 순환이 생긴다)."""
import calendar as pycal
from datetime import date, timedelta

TODO_RULES = ("daily", "weekly", "biweekly", "monthly", "yearly")
WD_CODES = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]


def parse_rrule(rule):
    if not rule:
        return {}
    out = {}
    for part in rule.split(":", 1)[-1].split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.upper()] = v
    return out


def repeat_dates(base, rule, until, day=None):
    """반복 할 일의 base 다음 회차들(until 전까지). 월·연 반복은 원래 날짜 기준이라 31일이 30일로 밀리지 않는다.
    day: 월·연 반복의 원래 날짜(base가 2월 28일로 당겨진 31일 회차면 31). 없으면 base의 날짜."""
    days = {"daily": 1, "weekly": 7, "biweekly": 14}.get(rule)
    months = {"monthly": 1, "yearly": 12}.get(rule)
    out, k = [], 1
    while days or months:
        if days:
            d = base + timedelta(days=days * k)
        else:
            y, m = divmod(base.year * 12 + base.month - 1 + months * k, 12)
            d = date(y, m + 1, min(day or base.day, pycal.monthrange(y, m + 1)[1]))
        if d >= until:
            break
        out.append(d)
        k += 1
    return out


def anchor_day(day, earlier, rule):
    """월·연 반복의 원래 날짜. day가 달의 말일이면(31일이 30일·28일로 당겨졌을 수 있다) 바로 앞 회차들(earlier)부터
    거슬러 보며 가장 큰 날짜를 고르고, 말일이 아닌 회차(당겨지지 않은 진짜 날짜)를 만나면 멈춘다."""
    if rule not in ("monthly", "yearly"):
        return None
    d = day.day
    if d != pycal.monthrange(day.year, day.month)[1]:
        return d
    for x in sorted((e for e in earlier if e < day and (rule == "monthly" or e.month == day.month)), reverse=True):
        d = max(d, x.day)
        if x.day != pycal.monthrange(x.year, x.month)[1]:
            break
    return d


def todo_rrule(rule, day, anchor=None):
    """'새 할 일' 창의 반복 종류 → day에 시작하는 RRULE. 29~31일 매월·2월 29일 매년은
    짧은 달(해)에는 그 달 말일에 오게 한다(BYSETPOS=-1: 고른 날짜들 중 그 달에 있는 마지막 날).
    anchor: 원래 날짜(day가 말일로 당겨진 회차일 때, anchor_day)."""
    wd = WD_CODES[day.weekday()]
    n = max(anchor or day.day, day.day)
    if rule == "daily":
        return "RRULE:FREQ=DAILY"
    if rule == "weekly":
        return f"RRULE:FREQ=WEEKLY;BYDAY={wd}"
    if rule == "biweekly":
        return f"RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY={wd}"
    if rule == "monthly":
        if n <= 28:
            return f"RRULE:FREQ=MONTHLY;BYMONTHDAY={n}"
        return f"RRULE:FREQ=MONTHLY;BYMONTHDAY={','.join(str(d) for d in range(28, n + 1))};BYSETPOS=-1"
    if rule == "yearly":
        if (day.month, n) == (2, 29):
            return "RRULE:FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=28,29;BYSETPOS=-1"
        return "RRULE:FREQ=YEARLY"
    raise ValueError(f"모르는 반복: {rule}")


def rule_of(rrule, day):
    """구글에서 읽은 RRULE이 day에 시작하는 TODO_RULES 중 하나면 그 이름, 아니면 None(휴대폰에서 바꾼 규칙 등)."""
    p = parse_rrule(rrule)
    if not p:
        return None
    return next((r for r in TODO_RULES if parse_rrule(todo_rrule(r, day)) == p), None)


def todo_values(title, notes, day, rule, calendar_id, anchor=None):
    """반복 할 일을 만들 일정 편집 값(일정 창의 values()와 같은 모양)."""
    return {"title": title, "all_day": True, "start": day, "end": day, "calendar_id": calendar_id,
            "description": notes, "location": "", "done": False, "text_color": "", "order": 0, "line": False,
            "alarm": "", "countdown": "", "todo": True, "recurrence": todo_rrule(rule, day, anchor)}
