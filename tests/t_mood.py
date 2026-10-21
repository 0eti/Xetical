"""트래커(무드 트래커) 시험: 칸 배치·그리기·누르기·색 칠하기·되돌리기·설정·백업. 구글은 쓰지 않는다."""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, ".")
SP = Path(sys.argv[1])
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication, QMenu

qapp = QApplication([])
import xetical.config as C
C.Config.save = lambda self: None
C.CONFIG_PATH = SP / "config_none.json"  # 사용자 설정(data/config.json)이 아니라 기본값으로 시험한다
import xetical.jobs as J
J.OUTBOX_PATH = SP / "outbox_t.json"
import xetical.model as M
M.CACHE_PATH = SP / "cache_t.json"
import xetical.mood as MD
MD.MOODS_PATH = SP / "moods_t.json"
import xetical.reminders as R
R.FIRED_PATH = SP / "fired_t.json"
import xetical.app as A
A.CELLS_PATH = SP / "cells_t.json"
A.MoodData = lambda: MD.MoodData(SP / "moods_t.json")
from xetical.model import Event

results = []


def check(name, cond, info=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{info}]" if info and not cond else ""))


class FakeG:
    def __getattr__(self, name):
        if name == "fetch_all":
            return lambda *a: (a_cals(), [], {}, (a[0], a[1]), [], "ok")
        return lambda *a: None

    def has_token(self):
        return True

    def has_client_secret(self):
        return True


def a_cals():
    return list(a.store.calendars)


T = date.today()
a = A.App()
a.gcal = a.outbox.gcal = FakeG()
a.store.set([{"id": "me", "summary": "캘린더", "color": "#4a90d9", "primary": True, "writable": True, "selected": True}],
            [Event("e1", "me", "일정", T, T + timedelta(days=1), True)], {}, (T - timedelta(days=60), T + timedelta(days=90)))
w = a.win
w.resize(900, 700)


def paint():
    w.grab()  # paintEvent를 돌려 눌릴 곳(칸·이름)을 새로 적는다


# ---------- 데이터 ----------
data = MD.MoodData(SP / "moods_x.json")
data.set("mood", T, "#00b050")
check("데이터: 저장하고 다시 읽기", MD.MoodData(SP / "moods_x.json").get("mood", T) == "#00b050")
data.set("mood", T, "")
check("데이터: 지우기", MD.MoodData(SP / "moods_x.json").get("mood", T) == "")

# ---------- 프린트 기능 삭제 ----------
check("프린트 메뉴 없음", not any("프린트" in act.text() for act in a.menu.actions()) and not hasattr(a, "print_calendar"))

# ---------- 꺼져 있으면 아무것도 없음 ----------
a.cfg["mood_on"] = False
paint()
grid_off = w._layout()["grid"].height()
check("꺼져 있으면 트래커 칸 없음", not w.mood.boxes and w._layout()["mood_h"] == 0)

# ---------- 주 단위 ----------
a.cfg["mood_on"] = True
a.cfg["mood_layout"] = "week"
paint()
L = w._layout()
days_in_year = (date(T.year + 1, 1, 1) - date(T.year, 1, 1)).days
check("켜면 달력 칸 높이가 그만큼 줄어듦", L["mood_h"] > 0 and abs(L["grid"].height() - (grid_off - L["mood_h"])) < 1,
      (grid_off, L["grid"].height(), L["mood_h"]))
check("주 단위: 누를 수 있는 칸 = 1월 1일 ~ 오늘", len(w.mood.boxes) == (T - date(T.year, 1, 1)).days + 1, len(w.mood.boxes))
rows, cols, place = w.mood.grid(a.cfg, T.year)
check("주 단위: 7줄 × 53~54열, 올해 날짜가 모두 다른 칸", rows == 7 and cols in (53, 54)
      and len({place(d) for d in MD.MoodPanel.year_days(T.year)}) == days_in_year, (rows, cols))
check("주 단위: 칸이 달력 창 안에 들어감", all(w.rect().contains(r.toRect()) for r, _ in w.mood.boxes))
check("트래커 이름 탭 2개", [t for _, t in w.mood.tabs] == ["mood", "workout"])
jan1 = date(T.year, 1, 1)
check("주 단위 기본: 1월 1일이 왼쪽 위, 위→아래로 차례", place(jan1) == (0, 0) and place(jan1 + timedelta(days=1)) == (1, 0)
      and place(jan1 + timedelta(days=7)) == (0, 1) and cols == 53, (place(jan1), cols))
a.cfg["mood_week_from"] = "weekday"
rows_w, cols_w, place_w = w.mood.grid(a.cfg, T.year)
check("주 단위 '요일 줄에 맞춤': 1월 1일은 그 요일 줄", place_w(jan1) == ((jan1.weekday() - a.cfg["week_start"]) % 7, 0)
      and len({place_w(d) for d in MD.MoodPanel.year_days(T.year)}) == days_in_year, place_w(jan1))
a.cfg["mood_week_from"] = "jan1"
paint()

# ---------- 칸은 더블클릭해야 색 고르기 ----------
asked = []
w.moodMenuRequested.disconnect()
w.moodMenuRequested.connect(lambda d, pos: asked.append(d))
today_rect = next(r for r, d in w.mood.boxes if d == T)
w.on_press(today_rect.center(), QPoint(0, 0), Qt.LeftButton)
check("한 번 클릭으로는 색 고르기가 뜨지 않음", asked == [], asked)
w.on_press(today_rect.center(), QPoint(0, 0), Qt.LeftButton)
check("더블클릭하면 색 고르기", asked == [T], asked)
w._last_click = (0.0, w._last_click[1])
w.on_double(today_rect.center(), Qt.LeftButton)
check("아이콘 클릭 통과 모드의 더블클릭도 색 고르기", asked == [T, T], asked)
check("앞으로의 날은 누를 수 없음", all(d <= T for _, d in w.mood.boxes))
w.on_press(today_rect.center(), QPoint(0, 0), Qt.RightButton)
check("오른쪽 클릭도 색 고르기", asked == [T, T, T], asked)

# 메뉴로 색 칠하기
shown = []


class _Menu(QMenu):
    def exec(self, *a_):
        shown.append([act.text() for act in self.actions()])
        next(act for act in self.actions() if act.text() == "좋음").trigger()


A.QMenu = _Menu
a.show_mood_menu(T, QPoint(0, 0))
check("색 메뉴: 지금 트래커의 색 이름 + 지우기 + 설정", shown and {"좋음", "보통", "나쁨", "지우기", "트래커 설정…"} <= set(shown[0]), shown)
check("색 칠하기: 저장됨", a.moods.get("mood", T) == "#00b050")
w._tip = None
w.on_move(today_rect.center(), QPoint(0, 0))
check("칸에 마우스를 올려도 풍선 도움말 없음", w._tip is None, w._tip)
a.undo()
check("되돌리기: 칠하기 취소", a.moods.get("mood", T) == "")

# 색 이름이 빈칸이면 색 코드를 보이지 않음
a.cfg["mood_trackers"][0]["colors"].append({"color": "#7030a0", "label": ""})
shown.clear()


class _Menu2(QMenu):
    def exec(self, *a_):
        shown.append([act.text() for act in self.actions()])


A.QMenu = _Menu2
a.show_mood_menu(T, QPoint(0, 0))
check("색 이름이 빈칸이면 메뉴에 색 코드가 나오지 않음", shown and "#7030a0" not in shown[0] and "" in shown[0], shown)
a.cfg["mood_trackers"][0]["colors"].pop()
A.QMenu = QMenu

# 지난 날
yday = T - timedelta(days=1)
if yday.year == T.year:
    a.set_mood(yday, "#ff0000")
    check("지난 날도 칠할 수 있음", a.moods.get("mood", yday) == "#ff0000")

# ---------- 트래커 바꾸기 ----------
paint()
tab = next(r for r, t in w.mood.tabs if t == "workout")
w.on_press(tab.center(), QPoint(0, 0), Qt.LeftButton)
check("이름을 누르면 트래커가 바뀜", a.cfg["mood_active"] == "workout")
a.set_mood(T, "#0070c0")
check("트래커마다 따로 저장", a.moods.get("workout", T) == "#0070c0" and a.moods.get("mood", T) == "")
a.choose_tracker("mood")

# ---------- 월 단위 ----------
a.cfg["mood_layout"] = "month"
paint()
rows, cols, place = w.mood.grid(a.cfg, T.year)
check("월 단위: 12줄 × 31칸", (rows, cols) == (12, 31) and place(date(T.year, 3, 5)) == (2, 4))
check("월 단위: 누를 수 있는 칸 = 1월 1일 ~ 오늘", len(w.mood.boxes) == (T - date(T.year, 1, 1)).days + 1)
check("월 단위: 칸이 달력 창 안에 들어감", all(w.rect().contains(r.toRect()) for r, _ in w.mood.boxes))

# ---------- 설정 창 ----------
from xetical.settings import SettingsWindow, TrackerEditor
s = SettingsWindow(a)
names = [s.nav.item(i).text() for i in range(s.nav.count())]
check("설정 창에 '트래커' 탭", "트래커" in names and names.index("트래커") == A.PAGE_TRACKER, names)
check("계정·정보 탭 번호", names[A.PAGE_ACCOUNT] == "계정" and names[A.PAGE_ABOUT] == "정보", names)
ed = s.findChild(TrackerEditor)
import xetical.settings as ST
ST.QInputDialog.getText = staticmethod(lambda *a_, **k: ("물 마시기", True))
ed._add()
check("설정: 트래커 추가", [t["name"] for t in a.cfg["mood_trackers"]][-1] == "물 마시기")
new_id = a.cfg["mood_trackers"][-1]["id"]
ed._add_color()
ed._edit_color(1, label="2L 이상")
check("설정: 색 추가·이름", [c["label"] for c in a.cfg["mood_trackers"][-1]["colors"]] == ["했음", "2L 이상"])
a.moods.set(new_id, T, "#00b050")
ST.QMessageBox.question = staticmethod(lambda *a_, **k: ST.QMessageBox.Yes)
ed.list.setCurrentRow(len(a.cfg["mood_trackers"]) - 1)
ed._remove()
check("설정: 트래커 삭제 + 칠한 기록도 지움", new_id not in [t["id"] for t in a.cfg["mood_trackers"]]
      and new_id not in a.moods.days)
s.close()

# ---------- 백업 ----------
a.set_mood(T, "#ffc000")
path = SP / "backup_t.json"
a.export_settings(str(path))
saved = json.loads(path.read_text("utf-8"))
check("설정 백업에 트래커 목록과 칠한 날", "mood_trackers" in saved and saved["moods"]["mood"][T.isoformat()][0] == "#ffc000")
a.set_mood(T, "")
a.import_settings_file(str(path))
check("백업 가져오기로 칠한 날 복원", a.moods.get("mood", T) == "#ffc000")

# ================================================================ 구글 드라이브 동기화
# ---------- 저장 모양 ----------
old = MD.upgrade_days({"mood": {"2026-01-01": "#ff0000"}})
check("예전 저장 모양(날짜: 색)을 시각 0으로 바꿔 읽음", old == {"mood": {"2026-01-01": ["#ff0000", 0.0]}}, old)
fresh_data = MD.MoodData(SP / "moods_y.json")
fresh_data.set("mood", T, "#00b050")
fresh_data.set("mood", T, "")
check("지운 날은 '빈 색 + 시각'으로 남김(다른 PC에 지웠다고 알리려고)",
      MD.MoodData(SP / "moods_y.json").days["mood"][T.isoformat()][0] == "" and fresh_data.get("mood", T) == "")

# ---------- 합치기 규칙 ----------
TR_A = [{"id": "mood", "name": "기분", "colors": []}]
TR_B = [{"id": "mood", "name": "기분", "colors": []}, {"id": "run", "name": "달리기", "colors": []}]
local = {"trackers": TR_A, "trackers_t": 10.0, "days": {"mood": {"2026-03-01": ["#red", 5.0], "2026-03-02": ["#a", 9.0]}}}
remote = {"trackers": TR_B, "trackers_t": 20.0,
          "days": {"mood": {"2026-03-01": ["", 7.0], "2026-03-02": ["#b", 3.0], "2026-03-03": ["#c", 1.0]},
                   "run": {"2026-03-01": ["#g", 2.0]}}}
m = MD.merge_sync(local, remote)
check("합치기: 날짜마다 나중에 바꾼 쪽(지운 것도 이김)", m["days"]["mood"] == {
    "2026-03-01": ["", 7.0], "2026-03-02": ["#a", 9.0], "2026-03-03": ["#c", 1.0]}, m["days"])
check("합치기: 트래커 목록은 나중에 바꾼 쪽", m["trackers"] == TR_B and m["trackers_t"] == 20.0 and "run" in m["days"])
m2 = MD.merge_sync({"trackers": TR_B, "trackers_t": 30.0, "days": {}}, {"trackers": TR_A, "trackers_t": 20.0,
                                                                     "days": {"gone": {"2026-03-01": ["#x", 1.0]}}})
check("합치기: 지운 트래커의 칠한 날은 버림", "gone" not in m2["days"] and m2["trackers"] == TR_B)
check("합치기: 드라이브에 아직 없으면 이 PC 것 그대로", MD.merge_sync(local, None)["days"] == local["days"])


# ---------- 드라이브 파일 읽고 쓰기(가짜 드라이브 API) ----------
class _Req:
    def __init__(self, fn):
        self.fn = fn

    def execute(self, num_retries=0):
        return self.fn()


class _Files:
    def __init__(self, store):
        self.store = store

    def list(self, **kw):
        assert kw.get("spaces") == "appDataFolder"
        return _Req(lambda: {"files": [{"id": "f1"}] if "f1" in self.store else []})

    def get_media(self, fileId):
        return _Req(lambda: self.store[fileId])

    def create(self, body, media_body, fields):
        assert body["parents"] == ["appDataFolder"]
        self.store["f1"] = media_body.getbytes(0, media_body.size())
        return _Req(lambda: {"id": "f1"})

    def update(self, fileId, media_body):
        self.store[fileId] = media_body.getbytes(0, media_body.size())
        self.store["updates"] = self.store.get("updates", 0) + 1
        return _Req(lambda: {"id": fileId})


class _Drive:
    def __init__(self):
        self.store = {}

    def files(self):
        return _Files(self.store)


from xetical.gcal import GCal
g = GCal()
drive = _Drive()
g._drive = lambda: drive
first = g.mood_sync(local)
check("드라이브: 처음이면 앱 전용 폴더에 파일을 만들어 올림", "f1" in drive.store
      and json.loads(drive.store["f1"])["days"] == local["days"])
other_pc = {"trackers": TR_A, "trackers_t": 10.0, "days": {"mood": {"2026-03-05": ["#z", 50.0]}}}
merged = g.mood_sync(other_pc)
check("드라이브: 다른 PC가 받으면 둘이 합쳐짐", merged["days"]["mood"].keys() >= {"2026-03-01", "2026-03-05"}
      and json.loads(drive.store["f1"])["days"]["mood"]["2026-03-05"] == ["#z", 50.0])
before = drive.store.get("updates", 0)
g.mood_sync(merged)
check("드라이브: 바뀐 게 없으면 다시 올리지 않음", drive.store.get("updates", 0) == before)

# ---------- 앱: 켜기·다시 로그인 안내·받은 내용 반영 ----------
class SyncG(FakeG):
    scope = False
    remote = None

    def has_drive_scope(self):
        return self.scope

    def mood_sync(self, loc):
        return MD.merge_sync(loc, self.remote)


sg = SyncG()
a.gcal = sg
a.cfg["mood_sync"] = True
a.set_status("")
a.sync_moods()
check("드라이브 권한이 없으면 '다시 로그인' 안내", "다시 로그인" in w.status and w.status_clickable, w.status)
sg.scope = True
a.cfg["mood_trackers_t"] = 1.0
sg.remote = {"trackers": [{"id": "mood", "name": "기분", "colors": [{"color": "#00b050", "label": "좋음"}]},
                          {"id": "sleep", "name": "잠", "colors": [{"color": "#7030a0", "label": "푹 잠"}]}],
             "trackers_t": 99.0, "days": {"sleep": {T.isoformat(): ["#7030a0", 1e12]}}}
import time as _time
a.sync_moods()
end = _time.time() + 5
while a._mood_syncing and _time.time() < end:
    qapp.processEvents()
    _time.sleep(0.02)
check("앱: 다른 PC에서 바꾼 트래커 목록을 받아 옴", [t["id"] for t in a.cfg["mood_trackers"]] == ["mood", "sleep"]
      and a.cfg["mood_trackers_t"] == 99.0, a.cfg["mood_trackers"])
check("앱: 다른 PC에서 칠한 날을 받아 옴", a.moods.get("sleep", T) == "#7030a0")
check("앱: 목록에서 빠진 트래커(운동)의 기록은 지움", "workout" not in a.moods.days)

a.shutdown()
print(f"\n{sum(ok for _, ok in results)}/{len(results)} 통과")
