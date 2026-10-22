"""트래커(무드 트래커·운동 체크 등): 달력 창 아래쪽에 올해 365칸을 보여 주고 날마다 색을 칠한다.

- 칠한 날은 이 PC의 data/moods.json에 둔다: {"version": 2, "days": {트래커 id: {"YYYY-MM-DD": [색, 바꾼 시각]}}}
  지운 날도 [""(빈 색), 바꾼 시각]으로 남겨 둔다(동기화할 때 '지웠다'는 것을 다른 PC에 알리려고).
- 트래커 목록(이름·고를 색과 색 이름, 바꾼 시각 "t")은 설정 mood_trackers에, 지운 트래커는 mood_deleted에 있다.
- 설정에서 켜면 구글 드라이브의 앱 전용 숨은 폴더(appDataFolder)에 같은 내용을 올려 PC끼리 맞춘다(merge_sync).
- 모양: week = 7줄×53열(1월 1일부터 차례로, 또는 줄 = 요일), month = 12줄(월)×31칸. 올해(1~12월)로 고정이다.
  칸과 트래커 이름만 그린다(요일·월 이름, 색 범례, 풍선 도움말 없음).
- 오늘과 지난 날만, 칸을 더블클릭(또는 오른쪽 클릭)해서 칠한다. 한 번 클릭으로는 칠하지 않는다.
"""
import time
from datetime import date, timedelta

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPen

from .config import MOODS_PATH, load_json, save_json


def upgrade_days(raw):
    """저장된 칠한 날 → {트래커 id: {날짜: [색, 시각]}}. 예전 모양({날짜: 색})은 시각 0으로 바꾼다."""
    if isinstance(raw, dict) and raw.get("version") == 2:
        raw = raw.get("days", {})
    days = {}
    for tracker_id, marks in (raw or {}).items():
        if not isinstance(marks, dict):
            continue
        out = days.setdefault(tracker_id, {})
        for d, v in marks.items():
            if isinstance(v, str):
                out[d] = [v, 0.0]
            elif isinstance(v, list) and len(v) == 2 and isinstance(v[1], (int, float)):
                out[d] = [v[0] if isinstance(v[0], str) else "", float(v[1])]
    return days


def merge_days(a, b):
    """날짜마다 나중에 바꾼 쪽을 남긴다(지운 것도 '빈 색'으로 이긴다)."""
    out = {t: dict(m) for t, m in a.items()}
    for tracker_id, marks in b.items():
        mine = out.setdefault(tracker_id, {})
        for d, v in marks.items():
            if d not in mine or v[1] > mine[d][1]:
                mine[d] = list(v)
    return out


def _without_t(tracker):
    return {k: v for k, v in tracker.items() if k != "t"}


def stamp_trackers(old, new, deleted, now=None):
    """설정에서 트래커 목록을 바꿀 때: 새로 만들었거나 바뀐 트래커에 바꾼 시각("t")을 찍고,
    빠진 트래커는 지운 목록(deleted: {트래커 id: 지운 시각})에 넣는다. new·deleted를 고친다."""
    now = time.time() if now is None else now
    before = {t["id"]: t for t in old}
    for t in new:
        if t["id"] not in before or _without_t(before[t["id"]]) != _without_t(t):
            t["t"] = now
    ids = {t["id"] for t in new}
    for tracker_id in before:
        if tracker_id not in ids:
            deleted[tracker_id] = now


def merge_trackers(a, a_deleted, b, b_deleted):
    """트래커 목록 합치기: 트래커마다 나중에 바꾼 쪽을 쓰고(같으면 a), 그보다 나중에 지운 트래커는 뺀다.
    한쪽에만 있는 트래커는 남는다(다른 PC에서 새로 만든 트래커가 사라지지 않게). → (목록, 지운 목록)"""
    deleted = dict(a_deleted)
    for tracker_id, ts in b_deleted.items():
        if ts > deleted.get(tracker_id, 0):
            deleted[tracker_id] = ts
    best, order = {}, []
    for t in a + b:
        cur = best.get(t["id"])
        if cur is None:
            order.append(t["id"])
            best[t["id"]] = t
        elif t.get("t", 0) > cur.get("t", 0):
            best[t["id"]] = t
    trackers = [best[i] for i in order if i not in deleted or best[i].get("t", 0) > deleted[i]]
    return trackers, deleted


def merge_sync(local, remote):
    """드라이브 동기화: 이 PC와 드라이브의 내용을 합친다.
    local·remote = {"trackers": [...], "deleted": {id: 시각}, "days": {...}} (remote는 없으면 None)
    트래커는 트래커마다, 칠한 날은 날짜마다 나중에 바꾼 쪽을 쓴다. 남은 트래커에 없는 칠한 날은 버린다."""
    remote = remote or {}
    remote_trackers = [t for t in remote.get("trackers") or [] if isinstance(t, dict) and "id" in t]
    remote_deleted = remote.get("deleted") if isinstance(remote.get("deleted"), dict) else {}
    trackers, deleted = merge_trackers(local["trackers"], local["deleted"], remote_trackers, remote_deleted)
    ids = {t["id"] for t in trackers}
    days = merge_days(local["days"], upgrade_days(remote.get("days", {})))
    return {"trackers": trackers, "deleted": deleted, "days": {t: m for t, m in days.items() if t in ids}}


class MoodData:
    """칠한 날짜: {트래커 id: {"YYYY-MM-DD": [색, 바꾼 시각]}}"""

    def __init__(self, path=MOODS_PATH):
        self.path = path
        self.days = upgrade_days(load_json(path, {}))

    def _save(self):
        save_json(self.path, {"version": 2, "days": self.days})

    def get(self, tracker_id, d):
        v = self.days.get(tracker_id, {}).get(d.isoformat())
        return v[0] if v else ""

    def set(self, tracker_id, d, color):
        self.days.setdefault(tracker_id, {})[d.isoformat()] = [color or "", time.time()]
        self._save()

    def drop(self, tracker_id):
        """트래커를 지우면 칠한 기록도 지운다."""
        if self.days.pop(tracker_id, None) is not None:
            self._save()

    def merge(self, days):
        """동기화: 날짜마다 나중에 바꾼 쪽을 남긴다."""
        self.days = merge_days(self.days, upgrade_days(days))
        self._save()

    def restore(self, days):
        """설정 백업 가져오기: 백업에 있는 날은 백업 쪽으로 덮어쓴다(지금 시각으로 찍어 다른 PC에도 퍼진다)."""
        now = time.time()
        for tracker_id, marks in upgrade_days(days).items():
            for d, (color, _t) in marks.items():
                self.days.setdefault(tracker_id, {})[d] = [color, now]
        self._save()

    def replace(self, days):
        self.days = days
        self._save()


def active_tracker(cfg):
    trackers = cfg["mood_trackers"]
    return next((t for t in trackers if t["id"] == cfg["mood_active"]), trackers[0] if trackers else None)


class MoodPanel:
    """달력 창 아래쪽 트래커 칸: 크기 계산, 그리기, 눌린 곳 찾기. 그릴 때마다 눌릴 곳(tabs·boxes)을 새로 적는다."""

    def __init__(self):
        self.tabs, self.boxes = [], []   # [(QRectF, 트래커 id)], [(QRectF, date)]

    # ---------- 칸 배치 ----------
    @staticmethod
    def year_days(year):
        d, end = date(year, 1, 1), date(year + 1, 1, 1)
        while d < end:
            yield d
            d += timedelta(days=1)

    @staticmethod
    def _week_first(year, week_start):
        """주 단위 첫 열의 첫날(1월 1일이 든 주의 시작 요일). week_start: 0=월 … 6=일"""
        jan1 = date(year, 1, 1)
        return jan1 - timedelta(days=(jan1.weekday() - week_start) % 7)

    def grid(self, cfg, year):
        """(줄 수, 열 수, 날짜 → (줄, 열))"""
        if cfg["mood_layout"] == "month":
            return 12, 31, lambda d: (d.month - 1, d.day - 1)
        if cfg["mood_week_from"] == "jan1":  # 1월 1일을 왼쪽 위에 두고 위→아래, 왼→오른쪽으로 7칸씩
            jan1 = date(year, 1, 1)
            cols = ((date(year, 12, 31) - jan1).days) // 7 + 1
            return 7, cols, lambda d: ((d - jan1).days % 7, (d - jan1).days // 7)
        ws = cfg["week_start"]  # 줄 = 요일(GitHub처럼): 1월 1일은 그 요일 줄에 놓이고 첫 열 위쪽은 빈다
        first = self._week_first(year, ws)
        cols = (date(year, 12, 31) - first).days // 7 + 1
        return 7, cols, lambda d: ((d.weekday() - ws) % 7, (d - first).days // 7)

    def _metrics(self, cfg, width, s, font, year):
        rows, cols, _ = self.grid(cfg, year)
        fm = QFontMetrics(font)
        pad = 6 * s
        box = min((width - 2 * pad) / cols, 15 * s)
        head = fm.height() + 4 * s  # 트래커 이름 줄(작게). 요일·월 이름과 색 범례는 두지 않는다(날짜는 풍선 도움말로)
        return {"rows": rows, "cols": cols, "pad": pad, "box": max(box, 3.0), "head": head}

    def height(self, cfg, width, s, font, year):
        k = self._metrics(cfg, width, s, self._small(font), year)
        return round(k["head"] + k["rows"] * k["box"] + 2 * k["pad"])

    @staticmethod
    def _small(font):
        f = QFont(font)
        f.setPointSizeF(max(6.0, font.pointSizeF() * 0.85))
        return f

    # ---------- 그리기 ----------
    def paint(self, p, rect, cfg, data, today, font, fill, s):
        self.tabs, self.boxes = [], []
        p.fillRect(rect, fill)
        font = self._small(font)
        k = self._metrics(cfg, rect.width(), s, font, today.year)
        fm = QFontMetrics(font)
        text_color = QColor(cfg["item_font"]["color"])
        dim = QColor(text_color)
        dim.setAlpha(120)
        p.setFont(font)
        tracker = active_tracker(cfg)

        # 트래커 이름(누르면 바꿈)
        x, y, h = rect.left() + k["pad"], rect.top() + k["pad"] / 2, k["head"] - k["pad"] / 2
        for t in cfg["mood_trackers"]:
            w = fm.horizontalAdvance(t["name"]) + 12 * s
            r = QRectF(x, y, w, h)
            on = tracker is not None and t["id"] == tracker["id"]
            bold = QFont(font)
            bold.setBold(on)
            p.setFont(bold)
            p.setPen(text_color if on else dim)
            p.drawText(r, Qt.AlignCenter, t["name"])
            if on:
                p.fillRect(QRectF(r.left() + 4 * s, r.bottom() - 2 * s, r.width() - 8 * s, 2 * s),
                           QColor(cfg["title_button_color"]))
            self.tabs.append((r, t["id"]))
            x += w + 2 * s
        p.setFont(font)
        if tracker is None:
            p.setPen(dim)
            p.drawText(QRectF(x, y, rect.right() - x, h), Qt.AlignVCenter, "설정 → 트래커에서 트래커를 추가하세요")
            return

        # 칸
        rows, cols, place = self.grid(cfg, today.year)
        box, gap = k["box"], max(1.0, k["box"] * 0.16)
        left = rect.left() + k["pad"]
        top = rect.top() + k["head"]
        empty = QColor(text_color)
        empty.setAlpha(28)
        future = QColor(text_color)
        future.setAlpha(40)
        for d in self.year_days(today.year):
            row, col = place(d)
            r = QRectF(left + col * box, top + row * box, box - gap, box - gap)
            color = data.get(tracker["id"], d) if data else ""
            if d > today:  # 앞으로의 날: 테두리만
                p.setPen(QPen(future, 1))
                p.setBrush(Qt.NoBrush)
                p.drawRect(r.adjusted(0.5, 0.5, -0.5, -0.5))
                continue
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(color) if color else empty)
            p.drawRect(r)
            if d == today:
                p.setPen(QPen(QColor(cfg["today_border"]), max(1.0, 1.5 * s)))
                p.setBrush(Qt.NoBrush)
                p.drawRect(r.adjusted(-1, -1, 1, 1))
            self.boxes.append((r.adjusted(-gap / 2, -gap / 2, gap / 2, gap / 2), d))

    # ---------- 눌린 곳 ----------
    def hit(self, pos):
        """('tab', 트래커 id) | ('box', date) | None"""
        for r, tracker_id in self.tabs:
            if r.contains(pos):
                return "tab", tracker_id
        for r, d in self.boxes:
            if r.contains(pos):
                return "box", d
        return None

    def contains(self, pos):
        return any(r.contains(pos) for r, _ in self.tabs) or any(r.contains(pos) for r, _ in self.boxes)
