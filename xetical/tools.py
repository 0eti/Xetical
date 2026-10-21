"""☰ 메뉴의 도구들: 내용 검색, 내보내기."""
import csv
import os
from datetime import date, timedelta

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from .dialogs import from_qdate, to_qdate
from .widgets import ColorButton

WEEKDAYS = "월화수목금토일"


def _period(kind, a=None, b=None):
    today = date.today()
    span = {"1m": 31, "3m": 92, "6m": 183, "1y": 366, "all": 365 * 5}.get(kind)
    if span:
        return today - timedelta(days=span), today + timedelta(days=span)
    return a, b + timedelta(days=1)


class _PeriodPicker(QHBoxLayout):
    def __init__(self, choices):
        super().__init__()
        self.kind = QComboBox()
        for k, label in choices:
            self.kind.addItem(label, k)
        self.kind.addItem("사용자 지정", "custom")
        self.a, self.b = QDateEdit(calendarPopup=True), QDateEdit(calendarPopup=True)
        for w, d in ((self.a, date.today() - timedelta(days=30)), (self.b, date.today() + timedelta(days=30))):
            w.setDisplayFormat("yyyy-MM-dd")
            w.setDate(to_qdate(d))
        self.addWidget(self.kind)
        self.addWidget(self.a)
        self.addWidget(QLabel("~"))
        self.addWidget(self.b)
        self.kind.currentIndexChanged.connect(self._sync)
        self._sync()

    def _sync(self):
        custom = self.kind.currentData() == "custom"
        self.a.setEnabled(custom)
        self.b.setEnabled(custom)

    def range(self):
        return _period(self.kind.currentData(), from_qdate(self.a.date()), from_qdate(self.b.date()))


# ---------------------------------------------------------------- 내용 검색
class SearchDialog(QDialog):
    def __init__(self, app):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.app = app
        self.setWindowTitle("내용 검색")
        self.resize(620, 480)
        self.keyword = QLineEdit()
        self.keyword.setPlaceholderText("검색할 내용")
        self.period = _PeriodPicker([("all", "전체"), ("1m", "1개월"), ("3m", "3개월"), ("1y", "1년")])
        self.state = QComboBox()
        for k, label in (("all", "전체"), ("done", "완료"), ("undone", "미완료")):
            self.state.addItem(label, k)
        self.kind = QComboBox()
        for k, label in (("all", "전체"), ("normal", "일반"), ("repeat", "반복"), ("alarm", "알림"),
                         ("countdown", "카운트다운")):
            self.kind.addItem(label, k)
        self.color = ColorButton("", allow_none=True)
        go = QPushButton("검색")
        go.setDefault(True)
        go.clicked.connect(self.search)
        self.keyword.returnPressed.connect(self.search)
        self.result = QTreeWidget()
        self.result.setHeaderLabels(["날짜", "내용", "캘린더"])
        self.result.setColumnWidth(0, 120)
        self.result.setColumnWidth(1, 330)
        self.result.itemDoubleClicked.connect(self._jump)
        self.info = QLabel("결과를 더블클릭하면 그 날짜로 이동합니다.")

        form = QFormLayout()
        top = QHBoxLayout()
        top.addWidget(self.keyword, 1)
        top.addWidget(go)
        form.addRow("키워드", top)
        form.addRow("기간", self.period)
        filters = QHBoxLayout()
        filters.addWidget(QLabel("상태"))
        filters.addWidget(self.state)
        filters.addWidget(QLabel("유형"))
        filters.addWidget(self.kind)
        filters.addWidget(QLabel("글자색"))
        filters.addWidget(self.color)
        filters.addStretch()
        form.addRow("필터", filters)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.result, 1)
        lay.addWidget(self.info)

    def search(self):
        a, b = self.period.range()
        cals = [c for c in self.app.store.calendars if self.app.win.visible_calendar(c["id"])]
        self.info.setText("검색 중…")
        self.result.clear()
        self.app.worker.submit(self.app.gcal.search, cals, self.keyword.text().strip(), a, b, callback=self._done)

    def _done(self, events, err):
        if err:
            self.info.setText(f"검색 실패: {err}")
            return
        state, kind, color = self.state.currentData(), self.kind.currentData(), self.color.color
        shown = 0
        for ev in events:
            if state == "done" and not ev.done or state == "undone" and ev.done:
                continue
            if kind == "repeat" and not ev.recurring or kind == "alarm" and not ev.alarm \
                    or kind == "countdown" and not ev.countdown \
                    or kind == "normal" and (ev.recurring or ev.alarm or ev.countdown):
                continue
            if color and (ev.text_color or "").lower() != color.lower():
                continue
            d = ev.first_day
            cal = self.app.store.calendar(ev.calendar_id)
            item = QTreeWidgetItem([f"{d:%Y-%m-%d} ({WEEKDAYS[d.weekday()]})",
                                    ("✓ " if ev.done else "") + ev.title, cal["summary"] if cal else ""])
            item.setData(0, Qt.UserRole, d)
            if ev.text_color:
                item.setForeground(1, QColor(ev.text_color))
            self.result.addTopLevelItem(item)
            shown += 1
        self.info.setText(f"{shown}개 찾음. 결과를 더블클릭하면 그 날짜로 이동합니다.")

    def _jump(self, item):
        self.app.win.go_to(item.data(0, Qt.UserRole))


# ---------------------------------------------------------------- 내보내기
class ExportDialog(QDialog):
    def __init__(self, app):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.app = app
        self.setWindowTitle("내보내기")
        self.fmt = QComboBox()
        self.fmt.addItem("텍스트 (.txt)", "txt")
        self.fmt.addItem("Excel 목록 (.csv)", "csv")
        self.period = _PeriodPicker([("6m", "최근 6개월"), ("1y", "최근 1년"), ("all", "전체")])
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("내보내기")
        buttons.button(QDialogButtonBox.Cancel).setText("취소")
        buttons.accepted.connect(self.run)
        buttons.rejected.connect(self.reject)
        form = QFormLayout(self)
        form.addRow("형식", self.fmt)
        form.addRow("기간", self.period)
        form.addRow(buttons)

    def run(self):
        fmt = self.fmt.currentData()
        path, _ = QFileDialog.getSaveFileName(self, "내보내기", os.path.expanduser(f"~/Documents/Xetical.{fmt}"),
                                              "텍스트 (*.txt)" if fmt == "txt" else "CSV (*.csv)")
        if not path:
            return
        a, b = self.period.range()
        self.app.worker.submit(self.app.gcal.fetch_all, a, b, False, callback=lambda r, e: self._write(path, fmt, r, e))

    def _write(self, path, fmt, res, err):
        if err:
            QMessageBox.warning(self, "Xetical", f"일정을 가져오지 못했습니다.\n{err}")
            return
        calendars, events = res[0], res[1]
        names = {c["id"]: c["summary"] for c in calendars}
        events = sorted((e for e in events if self.app.win.visible_calendar(e.calendar_id)),
                        key=lambda e: (e.first_day, e.sort_key()))
        if fmt == "csv":
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["날짜", "시작", "종료", "내용", "완료", "반복", "캘린더", "장소", "메모"])
                for e in events:
                    w.writerow([f"{e.first_day:%Y-%m-%d}", "" if e.all_day else f"{e.start:%H:%M}",
                                "" if e.all_day else f"{e.end:%H:%M}", e.title, "O" if e.done else "",
                                "O" if e.recurring else "", names.get(e.calendar_id, ""), e.location, e.description])
        else:
            lines, cur = [], None
            for e in events:
                if e.first_day != cur:
                    cur = e.first_day
                    lines.append(f"\n{cur:%Y-%m-%d} ({WEEKDAYS[cur.weekday()]})")
                when = "" if e.all_day else f"[{e.start:%H:%M}-{e.end:%H:%M}] "
                lines.append(f"  {'[완료] ' if e.done else ''}{when}{e.title}")
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines).lstrip())
        self.accept()
        QMessageBox.information(None, "Xetical", f"{len(events)}개 일정을 내보냈습니다.\n{path}")

