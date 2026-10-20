"""음력, 24절기, 주 번호 계산."""
import math
from datetime import date, datetime, time, timedelta
from functools import lru_cache

try:
    from korean_lunar_calendar import KoreanLunarCalendar
except ImportError:  # 음력은 선택 기능
    KoreanLunarCalendar = None

# 태양 황경 0°(춘분)부터 15°마다
SOLAR_TERMS = ["춘분", "청명", "곡우", "입하", "소만", "망종", "하지", "소서", "대서", "입추", "처서", "백로",
               "추분", "한로", "상강", "입동", "소설", "대설", "동지", "소한", "대한", "입춘", "우수", "경칩"]


@lru_cache(maxsize=4096)
def lunar(d):
    """'9.10' 같은 음력 월.일 (윤달이면 '윤' 접두)."""
    if KoreanLunarCalendar is None:
        return ""
    k = KoreanLunarCalendar()
    if not k.setSolarDate(d.year, d.month, d.day):
        return ""
    return f"{'윤' if k.isIntercalation else ''}{k.lunarMonth}.{k.lunarDay}"


def _sun_longitude(dt_local):
    """현지 시각의 태양 겉보기 황경(도). Meeus 저정밀 공식(약 0.01° 오차)."""
    jd = dt_local.astimezone().timestamp() / 86400 + 2440587.5
    t = (jd - 2451545.0) / 36525
    l0 = 280.46646 + 36000.76983 * t
    m = math.radians(357.52911 + 35999.05029 * t)
    c = (1.914602 - 0.004817 * t) * math.sin(m) + (0.019993 - 0.000101 * t) * math.sin(2 * m) \
        + 0.000289 * math.sin(3 * m)
    omega = math.radians(125.04 - 1934.136 * t)
    return (l0 + c - 0.00569 - 0.00478 * math.sin(omega)) % 360


@lru_cache(maxsize=4096)
def solar_term(d):
    """그날 들어오는 절기 이름(없으면 '')."""
    a = _sun_longitude(datetime.combine(d, time.min))
    b = _sun_longitude(datetime.combine(d + timedelta(days=1), time.min))
    ia, ib = int(a // 15), int(b // 15)
    return SOLAR_TERMS[ib] if ia != ib else ""


def week_number(d, week_start, rule):
    """주 번호. rule: jan1=1월 1일이 있는 주가 1주, iso=첫 4일 이상인 주, full=첫 온전한 주."""
    def first_of_week(x):
        return x - timedelta(days=(x.weekday() - week_start) % 7)

    def week1_start(year):
        jan1 = date(year, 1, 1)
        w = first_of_week(jan1)
        if rule == "iso":
            return w if (jan1 - w).days <= 3 else w + timedelta(days=7)
        if rule == "full":
            return w if w == jan1 else w + timedelta(days=7)
        return w

    ws = first_of_week(d)
    start = week1_start(ws.year + 1)
    if ws >= start:
        return 1
    start = week1_start(ws.year)
    if ws < start:
        start = week1_start(ws.year - 1)
    return (ws - start).days // 7 + 1
