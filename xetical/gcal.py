"""Google Calendar API 연동. 모든 메서드는 블로킹이므로 작업 스레드에서만 호출한다."""
import json
import logging
import re
from datetime import date, datetime, time, timedelta, timezone

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaInMemoryUpload

from .config import CLIENT_SECRET_PATH, TOKEN_PATH
from .model import KEEP, NO_REPEAT, Event, parse_alarm
from .mood import merge_sync

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/tasks",
]
TASKS_SCOPE = SCOPES[-1]
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.appdata"  # 트래커 동기화를 켰을 때만 받는다(앱 전용 숨은 폴더)
MOODS_FILE = "xetical-moods.json"
TASK_COLOR = "#4285f4"
log = logging.getLogger("xetical")

# DesktopCal '휴가 지역'에 대응하는 구글 공휴일 캘린더(이름은 한국어로 받는다)
HOLIDAY_REGIONS = {
    "south_korea": "대한민국", "usa": "미국", "japanese": "일본", "china": "중국", "taiwan": "대만",
    "hong_kong": "홍콩", "uk": "영국", "german": "독일", "french": "프랑스", "canadian": "캐나다",
    "australian": "호주", "vietnamese": "베트남", "indonesian": "인도네시아", "singapore": "싱가포르",
}

# DesktopCal식 속성을 담는 extendedProperties.private 키
P_DONE, P_COLOR, P_ORDER, P_LINE, P_ALARM, P_COUNTDOWN, P_CD = (
    "xeticalDone", "xeticalColor", "xeticalOrder", "xeticalLine", "xeticalAlarm", "xeticalCountdown", "xeticalCD")
P_TODO = "xeticalTodo"  # 반복 할 일(이 표시가 있는 반복 일정만 ☐·할 일 창으로 다룬다)


class AuthError(Exception):
    """로그인이 필요하거나 토큰이 만료/취소됨."""


class ApiDisabledError(Exception):
    """구글 클라우드 프로젝트에서 Google Calendar API가 사용 설정되지 않음."""

    def __init__(self, url):
        super().__init__("Google API가 꺼져 있습니다.")
        self.url = url
        self.api = ("Google Tasks API" if "tasks.googleapis.com" in url else
                    "Google Drive API" if "drive.googleapis.com" in url else "Google Calendar API")


def is_transient(e):
    """다시 보내면 될 구글 오류인가(서버 일시 장애·요청 한도 초과)."""
    if not isinstance(e, HttpError):
        return False
    return e.resp.status in (429, 500, 502, 503, 504) or (
        e.resp.status == 403 and b"ateLimitExceeded" in (e.content or b""))


def is_holiday_calendar(cal_id):
    return cal_id.endswith("#holiday@group.v.calendar.google.com")


def holiday_calendar_id(region):
    return f"ko.{region}#holiday@group.v.calendar.google.com"


def validate_client_secret(path):
    data = json.loads(open(path, encoding="utf-8").read())
    if "installed" not in data:
        raise ValueError("데스크톱 앱 유형의 OAuth 클라이언트 JSON이 아닙니다.\n"
                         "(애플리케이션 유형을 '데스크톱 앱'으로 만들어야 합니다)")


def _local(s):
    return datetime.fromisoformat(s).astimezone().replace(tzinfo=None)


def _rfc3339(d):
    return datetime.combine(d, time.min).astimezone().isoformat()


def _when(d):
    """date(종일)·로컬 datetime → 구글 timeMin/timeMax 값."""
    return d.astimezone().isoformat() if isinstance(d, datetime) else _rfc3339(d)


def _rrule_line(recurrence):
    return next((r for r in recurrence or [] if r.upper().startswith("RRULE:")), "")


def _when_value(o):
    """구글 start·originalStartTime 값 → 종일이면 date, 아니면 로컬 datetime, 없으면 None."""
    if "date" in o:
        return date.fromisoformat(o["date"])
    if "dateTime" in o:
        return _local(o["dateTime"])
    return None


def _original_start(item):
    """구글 반복 회차의 원래 자리(originalStartTime)."""
    return _when_value(item.get("originalStartTime") or {})


def _is_first(ev, master):
    """반복 일정의 첫 회차인가(원래 자리가 원본의 시작과 같거나 앞). 첫 회차를 뒤로 옮겼어도 첫 회차다."""
    ms, slot = master.get("start", {}), ev.slot
    if "date" in ms:
        return not isinstance(slot, datetime) and slot <= date.fromisoformat(ms["date"])
    if "dateTime" in ms:
        return isinstance(slot, datetime) and slot <= _local(ms["dateTime"])
    return False


def rrule_with_until(rule, until):
    """RRULE에서 COUNT/UNTIL을 지우고 새 UNTIL을 붙인다(until=None이면 무기한)."""
    parts = [p for p in rule[6:].split(";") if p and not p.upper().startswith(("COUNT=", "UNTIL="))]
    if until:
        parts.append(f"UNTIL={until}")
    return "RRULE:" + ";".join(parts)


def event_props(v):
    """편집 값(dict) → extendedProperties.private (빈 문자열 = 해제)."""
    return {
        P_DONE: "1" if v.get("done") else "",
        P_COLOR: v.get("text_color") or "",
        P_ORDER: str(v.get("order", 0) or 0),
        P_LINE: "1" if v.get("line") else "",
        P_ALARM: v.get("alarm") or "",
        P_COUNTDOWN: v.get("countdown") or "",
        P_CD: "1" if v.get("countdown") else "",  # 카운트다운만 골라 받기 위한 표시
        P_TODO: "1" if v.get("todo") else "",
    }


def _props_values(p):
    """extendedProperties.private → 편집 값의 속성들(event_props의 반대)."""
    try:
        order = int(p.get(P_ORDER) or 0)
    except ValueError:
        order = 0
    return {"done": p.get(P_DONE) == "1", "text_color": p.get(P_COLOR, ""), "order": order,
            "line": p.get(P_LINE) == "1", "alarm": p.get(P_ALARM, ""), "countdown": p.get(P_COUNTDOWN, ""),
            "todo": p.get(P_TODO) == "1"}


def _series_values(ev, v, master):
    """반복 전체·뒤쪽에 쓸 편집 값: 이 회차에서 바꾼 속성만 원본의 속성에 얹는다.
    (이 회차만 완료했거나 색을 바꾼 것이 '모든 일정'·'이번 및 이후'로 고칠 때 전체로 퍼지지 않게)"""
    base = _props_values(master.get("extendedProperties", {}).get("private", {}))
    mine = {"done": ev.done, "text_color": ev.text_color, "order": ev.order, "line": ev.line,
            "alarm": ev.alarm, "countdown": ev.countdown, "todo": ev.todo}
    out = dict(v)
    for k, old in mine.items():
        if k not in v or (v[k] or "") == (old or ""):
            out[k] = base[k]
    return out


def google_reminders(v):
    """알림 설정을 구글 팝업 알림으로도 걸어 휴대폰에서도 울리게 한다(표현 가능한 것만)."""
    a = parse_alarm(v.get("alarm"))
    if not a:
        return None
    h, m, days = a
    mins = []
    for d in days:
        if v["all_day"]:
            minutes = d * 1440 - (h * 60 + m)  # 구글 종일 일정 알림은 '당일 0시 기준 몇 분 전'
        else:
            start = v["start"]
            fire = datetime.combine(start.date() - timedelta(days=d), time(h, m))
            minutes = int((start - fire).total_seconds() // 60)
        if 0 <= minutes <= 40320:
            mins.append(minutes)
    overrides = [{"method": "popup", "minutes": x} for x in sorted(set(mins))[:5]]
    return {"useDefault": False, "overrides": overrides} if overrides else None


class GCal:
    def __init__(self):
        self._creds = None
        self._svc = None
        self._event_colors = None
        self._tz = None  # 기본 캘린더의 시간대 이름(예: Asia/Seoul)
        self._writes = 0      # 구글에 무언가 고친 횟수(이 사이에 고쳤으면 아래 캐시를 버린다)
        self._old_cds = None  # 오래된 'N일 경과' 카운트다운 캐시: (받은 시각, 그때의 _writes, {캘린더 id: [항목]})
        self.want_drive = False  # 트래커 드라이브 동기화를 켰으면 로그인할 때 드라이브 권한도 받는다(app이 정한다)

    # ---------- 인증 ----------
    @staticmethod
    def has_client_secret():
        return CLIENT_SECRET_PATH.exists()

    @staticmethod
    def has_token():
        return TOKEN_PATH.exists()

    def login(self):
        scopes = SCOPES + ([DRIVE_SCOPE] if self.want_drive else [])
        flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_PATH), scopes)
        creds = flow.run_local_server(
            port=0, open_browser=True, timeout_seconds=300,
            authorization_prompt_message="",
            success_message="Xetical 로그인 완료! 이 탭은 닫아도 됩니다.",
        )
        if not creds or not creds.valid:
            raise AuthError("로그인이 완료되지 않았습니다.")
        TOKEN_PATH.write_text(creds.to_json(), "utf-8")
        self._creds, self._svc = creds, None
        return True

    def logout(self):
        TOKEN_PATH.unlink(missing_ok=True)
        self._creds = self._svc = None

    def _service(self):
        if self._creds is None:
            if not TOKEN_PATH.exists():
                raise AuthError("로그인이 필요합니다.")
            self._creds = Credentials.from_authorized_user_file(str(TOKEN_PATH))
        if not self._creds.valid:
            if not self._creds.refresh_token:
                raise AuthError("로그인이 필요합니다.")
            try:
                self._creds.refresh(Request())
            except RefreshError as e:
                self._creds = self._svc = None
                raise AuthError("로그인이 만료되었습니다. 다시 로그인해 주세요.") from e
            TOKEN_PATH.write_text(self._creds.to_json(), "utf-8")
        if self._svc is None:
            self._svc = build("calendar", "v3", credentials=self._creds, cache_discovery=False)
            self._tasks_svc = self._drive_svc = None
        return self._svc

    def _tasks(self):
        self._service()  # 로그인·토큰 갱신
        if getattr(self, "_tasks_svc", None) is None:
            self._tasks_svc = build("tasks", "v1", credentials=self._creds, cache_discovery=False)
        return self._tasks_svc

    @staticmethod
    def _token_scopes():
        try:
            return json.loads(TOKEN_PATH.read_text("utf-8")).get("scopes", [])
        except (OSError, ValueError):
            return []

    @classmethod
    def has_tasks_scope(cls):
        return TASKS_SCOPE in cls._token_scopes()

    @classmethod
    def has_drive_scope(cls):
        return DRIVE_SCOPE in cls._token_scopes()

    def _drive(self):
        self._service()  # 로그인·토큰 갱신
        if getattr(self, "_drive_svc", None) is None:
            self._drive_svc = build("drive", "v3", credentials=self._creds, cache_discovery=False)
        return self._drive_svc

    # ---------- 트래커 드라이브 동기화(앱 전용 숨은 폴더) ----------
    def mood_download(self):
        """(파일 id, 내용 dict) — 아직 없으면 (None, None)"""
        svc = self._drive()
        r = self._call(svc.files().list(spaces="appDataFolder", q=f"name = '{MOODS_FILE}'", fields="files(id)",
                                        orderBy="createdTime", pageSize=10))
        files = r.get("files", [])
        if not files:
            return None, None
        file_id = files[0]["id"]  # 두 PC가 처음에 동시에 만들어 파일이 둘이어도 모두 먼저 만든 파일을 쓴다
        body = self._call(svc.files().get_media(fileId=file_id))
        try:
            data = json.loads(body.decode("utf-8") if isinstance(body, bytes) else body)
        except ValueError:
            data = None  # 깨진 파일은 이 PC 내용으로 덮어쓴다
        return file_id, data if isinstance(data, dict) else None

    def mood_upload(self, file_id, data):
        """파일 id를 돌려준다(처음이면 만든다)."""
        svc = self._drive()
        media = MediaInMemoryUpload(json.dumps(data, ensure_ascii=False).encode("utf-8"), mimetype="application/json")
        if file_id:
            self._call(svc.files().update(fileId=file_id, media_body=media))
            return file_id
        return self._call(svc.files().create(body={"name": MOODS_FILE, "parents": ["appDataFolder"]},
                                             media_body=media, fields="id"))["id"]

    def mood_sync(self, local):
        """드라이브 내용과 합쳐 다시 올리고, 합친 내용을 돌려준다(작업 스레드에서)."""
        file_id, remote = self.mood_download()
        merged = merge_sync(local, remote)
        if merged != remote:
            self.mood_upload(file_id, merged)
        return merged

    def _translate(self, e):
        """HttpError/RefreshError → 앱 예외로 바꾼다. 바꿀 게 없으면 None."""
        if isinstance(e, RefreshError):
            self._creds = self._svc = None
            return AuthError("로그인이 만료되었습니다. 다시 로그인해 주세요.")
        if isinstance(e, HttpError):
            if e.resp.status == 401:
                self._creds = self._svc = None
                return AuthError("로그인이 만료되었습니다. 다시 로그인해 주세요.")
            if e.resp.status == 403 and b"accessNotConfigured" in (e.content or b""):
                m = re.search(rb"apis/api/([a-z0-9.\-]+)/overview\?project=(\d+)", e.content)
                api, project = (m.group(1).decode(), m.group(2).decode()) if m else ("calendar-json.googleapis.com", "")
                return ApiDisabledError(f"https://console.cloud.google.com/apis/library/{api}"
                                        + (f"?project={project}" if project else ""))
        return None

    def _call(self, request):
        if request.method != "GET":
            self._writes += 1
        try:
            return request.execute(num_retries=2)
        except (RefreshError, HttpError) as e:
            mapped = self._translate(e)
            if mapped:
                raise mapped from e
            raise

    def _batch(self, requests):
        """여러 요청을 50개씩 묶어 보낸다. 응답을 요청 순서대로 돌려준다.
        이미 지워진 일정(404/410)을 고치거나 지우는 것, 이미 만들어진 일정(409)을 또 만드는 것은 성공으로 친다.
        실패가 있으면 다시 보내면 될 오류를 먼저 올린다(그래야 보낼 편지함이 작업을 버리지 않고 다시 보낸다)."""
        errors, responses = [], {}
        if any(r.method != "GET" for r in requests):
            self._writes += 1

        def cb(req_id, resp, exc):
            if isinstance(exc, HttpError):
                st, insert = exc.resp.status, requests[int(req_id)].method == "POST"
                if (st in (404, 410) and not insert) or (st == 409 and insert):
                    exc = None
            if exc is not None:
                errors.append(exc)
            responses[int(req_id)] = resp
        svc = self._service()
        for i in range(0, len(requests), 50):
            batch = svc.new_batch_http_request(callback=cb)
            for j, r in enumerate(requests[i:i + 50]):
                batch.add(r, request_id=str(i + j))
            try:
                batch.execute()
            except (RefreshError, HttpError) as e:
                mapped = self._translate(e)
                raise (mapped or e) from e
        if errors:
            first = next((e for e in errors if is_transient(e)), errors[0])
            mapped = self._translate(first)
            raise (mapped or first)
        return [responses.get(i) for i in range(len(requests))]

    # ---------- 읽기 ----------
    def fetch_all(self, start, end, want_holidays=True, region="south_korea"):
        """start~end(date) 범위의 캘린더 목록, 일정, 공휴일을 한 번에 가져온다."""
        svc = self._service()
        if self._event_colors is None:
            colors = self._call(svc.colors().get())
            self._event_colors = {k: v["background"] for k, v in colors.get("event", {}).items()}

        calendars, page = [], None
        while True:
            r = self._call(svc.calendarList().list(pageToken=page, maxResults=250))
            for c in r.get("items", []):
                calendars.append({
                    "id": c["id"],
                    "summary": c.get("summaryOverride") or c.get("summary", c["id"]),
                    "color": c.get("backgroundColor", "#4a90d9"),
                    "primary": bool(c.get("primary")),
                    "writable": c.get("accessRole") in ("owner", "writer"),
                    "selected": bool(c.get("selected", False)),
                })
                if c.get("primary") and c.get("timeZone"):
                    self._tz = c["timeZone"]
            page = r.get("nextPageToken")
            if not page:
                break

        events = []
        for cal in calendars:
            if is_holiday_calendar(cal["id"]):
                continue
            for item in self._list_events(cal["id"], start, end):
                ev = self._parse(item, cal)
                if ev:
                    events.append(ev)
        holidays = self.fetch_holidays(start, end, region) if want_holidays else {}
        # 카운트다운은 먼 미래일 수 있어서 따로 넓게 받는다
        today = date.today()
        countdowns = []
        # 'N일 경과' 카운트다운(사귄 날 등)은 아주 오래전 날짜일 수 있어 400일 앞까지 더 받는다. 거의 바뀌지 않으니
        # 한 시간에 한 번만 받는다(그 사이 이 PC에서 무언가 고쳤으면 다시 받는다).
        now, writes, old = datetime.now(), self._writes, {}
        if self._old_cds and self._old_cds[1] == writes and self._old_cds[0].date() == today \
                and now - self._old_cds[0] < timedelta(hours=1):
            now, old = self._old_cds[0], self._old_cds[2]
        for cal in calendars:
            if is_holiday_calendar(cal["id"]):
                continue
            for item in self._list_events(cal["id"], today - timedelta(days=400), today + timedelta(days=800),
                                          private=f"{P_CD}=1"):
                ev = self._parse(item, cal)
                if ev:
                    countdowns.append(ev)
            if cal["id"] not in old:  # 반복으로 펼치지 않고 한 번짜리만
                old[cal["id"]] = [it for it in self._list_events(cal["id"], None, today - timedelta(days=400),
                                                                 private=f"{P_CD}=1", single=False)
                                  if "recurrence" not in it and "recurringEventId" not in it]
            for item in old[cal["id"]]:
                ev = self._parse(item, cal)
                parts = ev and ev.countdown_parts()
                if parts and parts[2] == "passed":
                    countdowns.append(ev)
        self._old_cds = (now, writes, old)
        task_lists, tasks, tasks_status = self.fetch_tasks(start, end)
        return calendars + task_lists, events + tasks, holidays, (start, end), countdowns, tasks_status

    def fetch_holidays(self, start, end, region):
        holidays = {}
        try:
            items = list(self._list_events(holiday_calendar_id(region), start, end))
        except HttpError:
            return holidays  # 공휴일 캘린더를 못 읽어도 나머지는 표시
        for item in items:
            if "date" not in item.get("start", {}):
                continue
            d0 = date.fromisoformat(item["start"]["date"])
            d1 = date.fromisoformat(item["end"]["date"])
            name = item.get("summary", "")
            desc = (item.get("description") or "").strip().lower()
            public = not desc or desc.startswith(("공휴일", "public holiday"))
            for i in range(max(1, (d1 - d0).days)):
                d = d0 + timedelta(days=i)
                lst = holidays.setdefault(d, [])
                if all(n != name for n, _ in lst):
                    lst.append((name, public))
        return holidays

    def _list_events(self, cal_id, start, end, q=None, private=None, single=True):
        """single=False면 반복 일정을 회차로 펼치지 않는다(원본 하나로 온다)."""
        svc, page = self._service(), None
        while True:
            kw = dict(calendarId=cal_id, singleEvents=single, maxResults=2500, pageToken=page)
            if single:
                kw["orderBy"] = "startTime"  # 회차로 펼칠 때만 쓸 수 있다
            if start:
                kw["timeMin"] = _rfc3339(start)
            if end:
                kw["timeMax"] = _rfc3339(end)
            if q:
                kw["q"] = q
            if private:
                kw["privateExtendedProperty"] = private
            r = self._call(svc.events().list(**kw))
            yield from r.get("items", [])
            page = r.get("nextPageToken")
            if not page:
                return

    def _parse(self, item, cal):
        if item.get("status") == "cancelled":
            return None
        s, e = item.get("start", {}), item.get("end", {})
        if "date" in s:
            all_day, start, end = True, date.fromisoformat(s["date"]), date.fromisoformat(e["date"])
        elif "dateTime" in s:
            all_day, start, end = False, _local(s["dateTime"]), _local(e["dateTime"])
        else:
            return None
        color = self._event_colors.get(item.get("colorId"), cal["color"]) if self._event_colors else cal["color"]
        p = item.get("extendedProperties", {}).get("private", {})
        rem = item.get("reminders", {})
        popups = [o["minutes"] for o in rem.get("overrides", []) if o.get("method") == "popup"] \
            if not rem.get("useDefault", True) else []
        me = next((a for a in item.get("attendees", []) if a.get("self")), None)
        organizer = item.get("organizer", {})
        response = me.get("responseStatus", "needsAction") if me and not organizer.get("self") else ""
        return Event(
            id=item["id"], calendar_id=cal["id"], title=item.get("summary", "(제목 없음)"),
            start=start, end=end, all_day=all_day, color=color,
            description=item.get("description", ""), location=item.get("location", ""),
            recurring="recurringEventId" in item, html_link=item.get("htmlLink", ""),
            recurring_event_id=item.get("recurringEventId", ""), original=_original_start(item),
            **_props_values(p), popup_minutes=popups, writable=cal["writable"],
            response=response, organizer=(organizer.get("displayName") or organizer.get("email", "")) if response else "",
        )

    def search(self, calendars, keyword, start, end):
        """키워드로 일정 검색(구글 서버 검색)."""
        found = []
        for cal in calendars:
            if is_holiday_calendar(cal["id"]) or cal.get("tasks"):
                continue
            for item in self._list_events(cal["id"], start, end, q=keyword or None):
                ev = self._parse(item, cal)
                if ev:
                    found.append(ev)
        found.sort(key=lambda e: (e.first_day, e.sort_key()))
        return found

    def get_rrule(self, ev):
        """반복 일정 인스턴스의 원본 RRULE과 원본 시작을 가져온다."""
        if not ev.recurring_event_id:
            return "", None
        master = self._call(self._service().events().get(calendarId=ev.calendar_id, eventId=ev.recurring_event_id))
        return _rrule_line(master.get("recurrence")), master.get("start", {})

    # ---------- 쓰기 ----------
    def _zone(self):
        """시간대 이름. 구글은 시간 반복 일정에 이것이 없으면 거절한다. 동기화 전이면 캘린더 설정에서 읽는다."""
        if self._tz is None:
            try:
                self._tz = self._call(self._service().settings().get(setting="timezone")).get("value", "")
            except HttpError:
                self._tz = ""
        return self._tz or None

    def _times(self, v):
        if v["all_day"]:
            start = {"date": v["start"].isoformat(), "dateTime": None, "timeZone": None}
            end = {"date": (v["end"] + timedelta(days=1)).isoformat(), "dateTime": None, "timeZone": None}
        else:
            tz = self._zone()
            start = {"dateTime": v["start"].astimezone().isoformat(), "date": None, "timeZone": tz}
            end = {"dateTime": v["end"].astimezone().isoformat(), "date": None, "timeZone": tz}
        return start, end

    def _body(self, v, for_insert=False, reset_reminders=False):
        """reset_reminders: 알림을 지웠을 때만 구글 기본 알림으로 되돌린다(휴대폰에서 건 알림은 건드리지 않는다)."""
        start, end = self._times(v)
        body = {
            "summary": v["title"], "description": v.get("description", ""), "location": v.get("location", ""),
            "start": start, "end": end, "extendedProperties": {"private": event_props(v)},
        }
        rem = google_reminders(v)
        if rem:
            body["reminders"] = rem
        elif reset_reminders:
            body["reminders"] = {"useDefault": True, "overrides": None}
        if v.get("todo"):  # 반복 할 일은 휴대폰 알림을 울리지 않는다(계정의 '종일 일정 기본 알림'이 매 회차 울리지 않게)
            body["reminders"] = {"useDefault": False, "overrides": []}
        if for_insert:
            for k in ("start", "end"):
                body[k] = {kk: vv for kk, vv in body[k].items() if vv is not None}
            if v.get("recurrence"):
                body["recurrence"] = [v["recurrence"]] + list(v.get("exdates") or [])
            if v.get("event_id"):  # 미리 정한 id: 다시 보내도 409로 막혀 두 번 만들어지지 않는다
                body["id"] = v["event_id"]
        return body

    def create_event(self, v):
        """만든 일정의 id를 돌려준다(되돌리기·오프라인 대기에서 임시 id를 바꿔 끼우는 데 쓴다)."""
        try:
            return self._call(self._service().events().insert(calendarId=v["calendar_id"], body=self._body(v, True)))["id"]
        except HttpError as e:
            if e.resp.status == 409 and v.get("event_id"):  # 앞서 보낸 것이 이미 만들어졌다
                return v["event_id"]
            raise

    def task_to_todo(self, task_ev, v):
        """보통 할 일을 반복 할 일(v = todo_values)로: 반복 일정을 만든 뒤에만 할 일을 지운다
        (만들기가 거절되면 할 일은 남는다. 미리 정한 id라 다시 보내도 두 번 만들어지지 않는다)."""
        new_id = self.create_event(v)
        self.delete_task(task_ev)
        return new_id

    def update_event(self, ev, v, scope="one"):
        """scope: one=이번만, following=이번과 이후, all=전체(반복 일정일 때만 의미가 있다).
        v["recurrence"]: KEEP(None)=규칙 그대로, NO_REPEAT("")=반복 안 함, "RRULE:…"=새 규칙."""
        svc = self._service()
        if ev.recurring and scope == "all":
            return self._update_series(ev, v)
        if ev.recurring and scope == "following":
            master = self._call(svc.events().get(calendarId=ev.calendar_id, eventId=ev.recurring_event_id))
            if _is_first(ev, master):  # 첫 회차부터 = 전체(앞쪽을 자르면 빈 반복이 남는다)
                return self._update_series(ev, v)
            old = _rrule_line(master.get("recurrence"))
            rule = v.get("recurrence")
            same = rule is KEEP or rule == old
            if same:  # 규칙을 그대로 두면 끝(UNTIL)은 지키고 남은 횟수(COUNT)만 다시 센다
                rule = self._remaining_rule(ev, old) if old else NO_REPEAT
            v = _series_values(ev, v, master)
            # 예전 반복의 뒤쪽 예외(지운 회차·회차마다 둔 완료 표시)는 새 반복에 옮긴다(앞쪽을 자르면 사라진다).
            # 규칙을 바꿨으면 회차 날짜가 달라져 옮기지 않는다. 자르기 전에 읽어야 다시 보내도 같은 결과가 된다.
            exdates, marks = self._later_exceptions(ev, master, v) if same and rule else ([], [])
            # 뒤쪽 반복을 먼저 만들고(미리 정한 id라 다시 보내도 409로 막힌다) 그다음 앞쪽을 자른다.
            # 거꾸로 하면 자른 직후 끊겼을 때 다시 보내면서 잘린 규칙을 원래 규칙으로 읽게 된다.
            new_id = self.create_event(dict(v, recurrence=rule, exdates=exdates))
            try:
                self._mark_instances(v, new_id, marks)
            except HttpError as e:
                if is_transient(e):  # 표시는 덤이라 거절되면 넘어가고, 일시 장애면 다시 보낸다
                    raise
                log.warning("'이번 및 이후': 뒤쪽 회차의 완료 표시·옮긴 날짜를 옮기지 못함: %s", e)
            try:
                self._end_series_before(ev, master)
            except HttpError as e:
                if not is_transient(e):  # 앞쪽을 자를 수 없으면(남이 만든 반복 등) 뒤쪽을 지워 두 벌이 남지 않게 한다
                    self._call(svc.events().delete(calendarId=v["calendar_id"], eventId=new_id))
                raise
            return
        cal_id, ev_id = ev.calendar_id, ev.id
        if v["calendar_id"] != cal_id and not ev.recurring:
            moved = self._call(svc.events().move(calendarId=cal_id, eventId=ev_id, destination=v["calendar_id"]))
            cal_id, ev_id = v["calendar_id"], moved["id"]
        body = self._body(v, reset_reminders=bool(ev.alarm))
        if not ev.recurring and v.get("recurrence") is not KEEP:  # 단일 일정을 반복 일정으로(NO_REPEAT면 반복을 없앤다)
            body["recurrence"] = [v["recurrence"]] if v["recurrence"] else []
        self._call(svc.events().patch(calendarId=cal_id, eventId=ev_id, body=body))

    def _update_series(self, ev, v):
        svc = self._service()
        master = self._call(svc.events().get(calendarId=ev.calendar_id, eventId=ev.recurring_event_id))
        v = _series_values(ev, v, master)
        body = self._body(v, reset_reminders=bool(ev.alarm))
        # 시작·끝은 원본 날짜를 기준으로, 인스턴스에서 바뀐 만큼만 옮긴다
        if v["all_day"] and "date" in master["start"]:
            ds = v["start"] - ev.start
            de = (v["end"] + timedelta(days=1)) - ev.end
            body["start"]["date"] = (date.fromisoformat(master["start"]["date"]) + ds).isoformat()
            body["end"]["date"] = (date.fromisoformat(master["end"]["date"]) + de).isoformat()
        elif not v["all_day"] and "dateTime" in master["start"]:
            ds, de = v["start"] - ev.start, v["end"] - ev.end
            ms = datetime.fromisoformat(master["start"]["dateTime"]) + ds
            me = datetime.fromisoformat(master["end"]["dateTime"]) + de
            body["start"]["dateTime"], body["end"]["dateTime"] = ms.isoformat(), me.isoformat()
        rule = v.get("recurrence")
        if rule is not KEEP:  # NO_REPEAT("")이면 반복을 없앤다
            keep = [r for r in master.get("recurrence", []) if not r.upper().startswith("RRULE:")]
            body["recurrence"] = keep + [rule] if rule else []
        self._call(svc.events().patch(calendarId=ev.calendar_id, eventId=ev.recurring_event_id, body=body))

    def _remaining_rule(self, ev, rule):
        """'이번과 이후'로 나눈 뒤쪽 규칙: UNTIL은 그대로 두고, COUNT는 앞쪽에서 이미 지나간 횟수만큼 뺀다."""
        m = re.search(r"COUNT=(\d+)", rule, re.I)
        if not m:
            return rule
        svc, used, page = self._service(), 0, None
        before = _when(ev.slot)
        while True:
            r = self._call(svc.events().instances(calendarId=ev.calendar_id, eventId=ev.recurring_event_id,
                                                  timeMax=before, showDeleted=True, pageToken=page, maxResults=2500))
            used += len(r.get("items", []))
            page = r.get("nextPageToken")
            if not page:
                break
        return rule[:m.start(1)] + str(max(1, int(m.group(1)) - used)) + rule[m.end(1):]

    def _later_exceptions(self, ev, master, v):
        """'이번과 이후'로 새로 만들 뒤쪽 반복에 옮겨 줄 예전 반복의 예외
        → (EXDATE 줄 목록, [(새 회차 자리, 다시 둘 속성, 옮겨 둔 start·end 또는 None)])
        - 지운 회차: 새 반복에서도 빠지게 EXDATE로
        - 회차마다 따로 둔 완료·글자색, 다른 날·시각으로 옮긴 회차: 새 반복의 같은 회차를 다시 고친다(_mark_instances)
        회차 자리는 이 회차를 v의 시작으로 옮긴 만큼 함께 옮기고, 옮긴 회차는 옮겨 둔 날·시각 그대로 둔다.
        구글은 실제 시각으로 회차를 고르므로 이 회차 앞으로 옮긴 뒤쪽 회차도 찾게 앞뒤 2년을 본다."""
        slot = ev.slot
        if v["all_day"] == isinstance(slot, datetime):  # 종일 ↔ 시간 일정으로 바꿨으면 회차 자리가 맞지 않는다
            return [], []
        shift = v["start"] - slot
        base = master.get("extendedProperties", {}).get("private", {})
        svc, page, exdates, marks = self._service(), None, [], []
        while True:
            r = self._call(svc.events().instances(
                calendarId=ev.calendar_id, eventId=ev.recurring_event_id, timeMin=_when(slot - timedelta(days=730)),
                timeMax=_when(slot + timedelta(days=730)), showDeleted=True, pageToken=page, maxResults=2500))
            for it in r.get("items", []):
                o = _original_start(it)
                if o is None or type(o) is not type(slot) or o <= slot:
                    continue
                if it.get("status") == "cancelled":
                    exdates.append(o + shift)
                    continue
                p = it.get("extendedProperties", {}).get("private", {})
                diff = {k: p.get(k, "") for k in (P_DONE, P_COLOR) if p.get(k, "") != base.get(k, "")}
                times = None
                if _when_value(it.get("start") or {}) not in (None, o):
                    times = {k: {kk: vv for kk, vv in it[k].items() if kk in ("date", "dateTime", "timeZone")}
                             for k in ("start", "end")}
                if diff or times:
                    marks.append((o + shift, diff, times))
            page = r.get("nextPageToken")
            if not page:
                break
        if not exdates:
            return [], marks
        if isinstance(slot, datetime):
            return ["EXDATE:" + ",".join(d.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ") for d in exdates)], marks
        return ["EXDATE;VALUE=DATE:" + ",".join(d.strftime("%Y%m%d") for d in exdates)], marks

    def _mark_instances(self, v, new_id, marks):
        """새 반복(new_id)의 회차들에 예전 회차의 완료·글자색·옮긴 날짜를 다시 둔다(다시 보내도 같은 결과)."""
        if not marks:
            return
        want = {o: (diff, times) for o, diff, times in marks}
        props = event_props(v)
        svc, page, reqs = self._service(), None, []
        while True:
            r = self._call(svc.events().instances(
                calendarId=v["calendar_id"], eventId=new_id, timeMin=_when(min(want)),
                timeMax=_when(max(want) + timedelta(days=1)), pageToken=page, maxResults=2500))
            for it in r.get("items", []):
                o = _original_start(it)
                if o in want:
                    diff, times = want[o]
                    body = dict(times or {}, extendedProperties={"private": dict(props, **diff)})
                    reqs.append(svc.events().patch(calendarId=v["calendar_id"], eventId=it["id"], body=body))
            page = r.get("nextPageToken")
            if not page:
                break
        if reqs:
            self._batch(reqs)

    def _end_series_before(self, ev, master=None):
        """반복 일정을 이 회차 앞에서 끝낸다(master: 이미 받아 둔 원본)."""
        svc = self._service()
        if master is None:
            master = self._call(svc.events().get(calendarId=ev.calendar_id, eventId=ev.recurring_event_id))
        rule = _rrule_line(master.get("recurrence"))
        slot = ev.slot  # 다른 날로 옮긴 회차는 원래 자리 앞에서 끊어야 그 회차도 뒤쪽에 들어간다
        if isinstance(slot, datetime):
            until = (slot.astimezone(timezone.utc) - timedelta(seconds=1)).strftime("%Y%m%dT%H%M%SZ")
        else:
            until = (slot - timedelta(days=1)).strftime("%Y%m%d")
        rec = [r for r in master.get("recurrence", []) if not r.upper().startswith("RRULE:")]
        rec.append(rrule_with_until(rule, until))
        self._call(svc.events().patch(calendarId=ev.calendar_id, eventId=ev.recurring_event_id,
                                      body={"recurrence": rec}))

    def delete_event(self, ev, scope="one"):
        svc = self._service()
        if ev.recurring and scope == "all":
            self._call(svc.events().delete(calendarId=ev.calendar_id, eventId=ev.recurring_event_id))
        elif ev.recurring and scope == "following":
            master = self._call(svc.events().get(calendarId=ev.calendar_id, eventId=ev.recurring_event_id))
            if _is_first(ev, master):  # 첫 회차부터 지우기 = 전체 지우기
                self._call(svc.events().delete(calendarId=ev.calendar_id, eventId=ev.recurring_event_id))
            else:
                self._end_series_before(ev, master)
        else:
            self._call(svc.events().delete(calendarId=ev.calendar_id, eventId=ev.id))

    def set_props(self, ev, **props):
        """완료·글자색 같은 DesktopCal식 속성만 바꾼다. 앱은 쓰지 않고 실제 구글 시험(t_google)이 회차를 표시할 때 쓴다."""
        cur = {"done": ev.done, "text_color": ev.text_color, "order": ev.order, "line": ev.line,
               "alarm": ev.alarm, "countdown": ev.countdown, "todo": ev.todo}
        cur.update(props)
        self._call(self._service().events().patch(
            calendarId=ev.calendar_id, eventId=ev.id, body={"extendedProperties": {"private": event_props(cur)}}))

    def apply_ops(self, ops):
        """셀 편집기 저장 결과를 한 번에 반영. ops = [(kind, ...)]"""
        svc, reqs = self._service(), []
        for op in ops:
            if op[0] == "create":
                reqs.append(svc.events().insert(calendarId=op[1], body=self._body(op[2], True)))
            elif op[0] == "patch":
                reqs.append(svc.events().patch(calendarId=op[1], eventId=op[2], body=op[3]))
            elif op[0] == "delete":
                reqs.append(svc.events().delete(calendarId=op[1], eventId=op[2]))
        if not reqs:
            return []
        responses = self._batch(reqs)
        return [op[2].get("event_id") or (r or {}).get("id", "") for op, r in zip(ops, responses) if op[0] == "create"]

    def move_event(self, ev, delta_days):
        """일정(또는 할 일)을 delta_days만큼 옮긴다. 반복 일정은 이 회차만 옮긴다."""
        if ev.task_list:
            list_id, task_id = self._task_ids(ev)
            due = ev.start + timedelta(days=delta_days)
            self._call(self._tasks().tasks().patch(tasklist=list_id, task=task_id,
                                                   body={"due": f"{due.isoformat()}T00:00:00.000Z"}))
            return
        d = timedelta(days=delta_days)
        if ev.all_day:
            body = {"start": {"date": (ev.start + d).isoformat()}, "end": {"date": (ev.end + d).isoformat()}}
        else:
            body = {"start": {"dateTime": (ev.start + d).astimezone().isoformat()},
                    "end": {"dateTime": (ev.end + d).astimezone().isoformat()}}
        self._call(self._service().events().patch(calendarId=ev.calendar_id, eventId=ev.id, body=body))

    def respond_event(self, ev, status):
        """초대받은 일정에 참석(accepted) / 미정(tentative) / 거절(declined)로 응답한다. 주최자에게 알림이 간다."""
        svc = self._service()
        item = self._call(svc.events().get(calendarId=ev.calendar_id, eventId=ev.id))
        attendees = item.get("attendees", [])
        for a in attendees:
            if a.get("self"):
                a["responseStatus"] = status
        self._call(svc.events().patch(calendarId=ev.calendar_id, eventId=ev.id, body={"attendees": attendees},
                                      sendUpdates="all"))

    # ---------- 할 일(Google Tasks) ----------
    def fetch_tasks(self, start, end):
        """마감일이 start~end 안에 있는 할 일. (목록들, 할 일 Event들, 상태) 상태: ok | scope | ('disabled', url) | error"""
        if not self.has_tasks_scope():
            return [], [], "scope"
        try:
            svc = self._tasks()
            lists, page = [], None
            while True:
                r = self._call(svc.tasklists().list(maxResults=100, pageToken=page))
                lists += r.get("items", [])
                page = r.get("nextPageToken")
                if not page:
                    break
            cals, events = [], []
            for tl in lists:
                cid = f"tasks:{tl['id']}"
                cals.append({"id": cid, "summary": f"할 일: {tl.get('title', '')}", "color": TASK_COLOR,
                             "primary": False, "writable": False, "selected": True, "tasks": True})
                page = None
                while True:
                    r = self._call(svc.tasks().list(
                        tasklist=tl["id"], showCompleted=True, showHidden=True, maxResults=100, pageToken=page,
                        dueMin=f"{start.isoformat()}T00:00:00.000Z", dueMax=f"{end.isoformat()}T00:00:00.000Z"))
                    for item in r.get("items", []):
                        if item.get("due") and not item.get("deleted"):
                            events.append(self._parse_task(item, tl["id"], cid))
                    page = r.get("nextPageToken")
                    if not page:
                        break
            return cals, events, "ok"
        except ApiDisabledError as e:
            return [], [], ("disabled", e.url)
        except HttpError as e:
            if e.resp.status == 403:
                return [], [], "scope"
            return [], [], "error"

    @staticmethod
    def _parse_task(item, list_id, cal_id):
        d = date.fromisoformat(item["due"][:10])  # 할 일 마감일은 날짜만 의미가 있다(시각은 항상 00:00)
        return Event(
            id=f"task:{list_id}:{item['id']}", calendar_id=cal_id, title=item.get("title") or "(제목 없음)",
            start=d, end=d + timedelta(days=1), all_day=True, color=TASK_COLOR,
            description=item.get("notes", ""), html_link=item.get("webViewLink", ""),
            done=item.get("status") == "completed", task_list=list_id, writable=True)

    @staticmethod
    def _task_ids(ev):
        _, list_id, task_id = ev.id.split(":", 2)
        return list_id, task_id

    @staticmethod
    def _task_body(title, notes, due, done):
        body = {"title": title, "notes": notes, "due": f"{due.isoformat()}T00:00:00.000Z",
                "status": "completed" if done else "needsAction"}
        if not done:
            body["completed"] = None
        return body

    def update_task(self, ev, title, notes, due, done):
        list_id, task_id = self._task_ids(ev)
        self._call(self._tasks().tasks().patch(tasklist=list_id, task=task_id,
                                               body=self._task_body(title, notes, due, done)))

    def set_task_done(self, ev, done):
        list_id, task_id = self._task_ids(ev)
        body = {"status": "completed" if done else "needsAction"}
        if not done:
            body["completed"] = None
        self._call(self._tasks().tasks().patch(tasklist=list_id, task=task_id, body=body))

    def delete_task(self, ev):
        list_id, task_id = self._task_ids(ev)
        self._call(self._tasks().tasks().delete(tasklist=list_id, task=task_id))

    def create_task(self, list_id, title, notes, due, done=False):
        item = self._call(self._tasks().tasks().insert(tasklist=list_id, body=self._task_body(title, notes, due, done)))
        return f"task:{list_id}:{item['id']}"
