"""트래커(무드 트래커·운동 체크 등): 달력 창 아래쪽에 올해 365칸을 보여 주고 날마다 색을 칠한다.

- 구글 캘린더와 동기화하지 않는다. 칠한 날은 이 PC의 data/moods.json에 {트래커 id: {"YYYY-MM-DD": "#색"}}으로 둔다.
- 트래커 목록(이름·고를 색과 색 이름)은 설정 mood_trackers에 있다(설정 백업에 같이 들어간다).
- 모양: week = 7줄(요일)×53열(주, GitHub 잔디처럼), month = 12줄(월)×31칸. 올해(1~12월)로 고정이다.
  칸과 트래커 이름만 그린다(요일·월 이름, 색 범례 없음). 날짜와 색 이름은 마우스를 올리면 보인다.
- 오늘과 지난 날만 칠할 수 있다.
"""
from datetime import date, timedelta

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPen

from .config import MOODS_PATH, load_json, save_json



class MoodData:
    """칠한 날짜: {트래커 id: {"YYYY-MM-DD": "#색"}}"""

    def __init__(self, path=MOODS_PATH):
        self.path = path
        self.days = load_json(path, {})

    def get(self, tracker_id, d):
        return self.days.get(tracker_id, {}).get(d.isoformat(), "")

    def set(self, tracker_id, d, color):
        marks = self.days.setdefault(tracker_id, {})
        if color:
            marks[d.isoformat()] = color
        else:
            marks.pop(d.isoformat(), None)
        save_json(self.path, self.days)

    def drop(self, tracker_id):
        """트래커를 지우면 칠한 기록도 지운다."""
        if self.days.pop(tracker_id, None) is not None:
            save_json(self.path, self.days)

    def merge(self, days):
        """설정 가져오기: 같은 날짜는 가져온 쪽으로 덮어쓴다."""
        for tracker_id, marks in days.items():
            if isinstance(marks, dict):
                self.days.setdefault(tracker_id, {}).update(marks)
        save_json(self.path, self.days)


def active_tracker(cfg):
    trackers = cfg["mood_trackers"]
    return next((t for t in trackers if t["id"] == cfg["mood_active"]), trackers[0] if trackers else None)


def color_label(tracker, color):
    return next((c["label"] for c in tracker["colors"] if c["color"].lower() == color.lower()), "") if tracker else ""


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
        ws = cfg["week_start"]
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
