"""DesktopCal식 셀 편집기.

날짜 칸을 더블클릭하면 칸 위에 편집창이 뜬다. 한 줄이 구글 캘린더의 '하루 종일' 일정 하나가 된다.
완료(취소선)·글자색·순서는 일정의 extendedProperties에 저장되어 다른 PC에서도 똑같이 보인다.
시간·반복·알림·카운트다운이 있는 일정은 위쪽 목록에 따로 나오고, 누르면 자세한 편집 창이 열린다.
"""
from datetime import timedelta

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeySequence, QShortcut, QTextBlockFormat, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QPushButton, QSizeGrip, QTextEdit,
                               QToolButton, QVBoxLayout, QWidget)

from .dialogs import ask_scope
from .gcal import event_props
from .view import make_font
from .widgets import color_menu, play_sound

WEEKDAYS = "월화수목금토일"


def line_text(title):
    """일정 제목을 칸 편집기의 한 줄 글자로. 줄바꿈·앞뒤·겹친 공백은 한 칸으로 모은다
    (휴대폰 등에서 만든 'A\\nB'·'헬스 ' 같은 제목을 열었다 닫기만 해도 쪼개지거나 고쳐 써지지 않게)."""
    return " ".join(title.split())


class _Line:
    """한 줄의 상태. block.userState()가 이 목록의 번호를 가리킨다."""

    def __init__(self, ev=None, done=False, color=""):
        self.ev, self.done, self.color = ev, done, color


class LineEdit(QTextEdit):
    contextRequested = Signal(QPoint)

    def contextMenuEvent(self, e):
        self.contextRequested.emit(e.globalPos())


class CellEditor(QWidget):
    committed = Signal(object, list, list, list)  # date, ops, 화면에 바로 반영할 줄 목록, 반복 일정 전체·이후 작업
    openEvent = Signal(object)                 # 위쪽 목록의 일정 편집
    newStructured = Signal(object, str, str)   # date, 'repeat' | 'alarm' | 'countdown', 제목(커서 줄 글자)
    cellColorPicked = Signal(object, str)      # date, 색('' = 없음)

    def __init__(self, cfg, store, day, cell_rect, default_cal, recent_colors):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.cfg, self.store, self.day = cfg, store, day
        self.default_cal = default_cal
        self.recent = recent_colors
        self._closing = False
        self._guard = False
        self._lines = []
        evs = store.on_day(day, lambda _c: True)
        self.line_events = sorted([e for e in evs if e.is_line_candidate], key=lambda e: (e.order, e.title))
        self.other_events = [e for e in evs if not e.is_line_candidate]

        self.setStyleSheet("""
            CellEditor { background:#2e2e2e; border:1px solid #6b6b6b; }
            QLabel { color:#ddd; }
            QTextEdit { background:#262626; border:none; color:#fff; selection-background-color:#2f80ed; }
            QToolButton { color:#eee; background:transparent; border:1px solid transparent; border-radius:3px;
                          padding:2px 4px; font-size:13px; }
            QToolButton:hover { background:#444; border-color:#555; }
            QToolButton:checked { background:#2f80ed; }
            QPushButton { color:#fff; background:#2f80ed; border:none; border-radius:3px; padding:3px 10px; }
            QPushButton:hover { background:#4791f0; }
            QPushButton#item { background:transparent; color:#eee; text-align:left; padding:1px 4px; }
            QPushButton#item:hover { background:#444; }
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(3)

        head = QHBoxLayout()
        title = QLabel(f"{day.month}월 {day.day}일 ({WEEKDAYS[day.weekday()]})")
        title.setStyleSheet("color:#ffffc0;font-weight:bold;")
        head.addWidget(title)
        head.addStretch()
        close = QToolButton(text="✕")
        close.setToolTip("저장 및 닫기")
        close.clicked.connect(self.save_and_close)
        head.addWidget(close)
        lay.addLayout(head)

        # 위쪽: 반복·알림·카운트다운·시간 일정 목록(DesktopCal EventContainer)
        for ev in self.other_events:
            b = QPushButton(self._item_label(ev))
            b.setObjectName("item")
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(f"color:{ev.text_color or '#eee'};" + ("text-decoration:line-through;" if ev.done else ""))
            b.clicked.connect(lambda _=False, ev=ev: self._open_event(ev))
            lay.addWidget(b)

        self.edit = LineEdit()
        self.edit.setAcceptRichText(False)
        self.edit.setFont(make_font(cfg["item_font"]))
        self.edit.setPlaceholderText("한 줄에 일정 하나씩 입력하세요")
        self.edit.contextRequested.connect(self._context_menu)
        lay.addWidget(self.edit, 1)

        bar = QHBoxLayout()
        bar.setSpacing(2)

        def tool(text, tip, fn):
            b = QToolButton(text=text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            bar.addWidget(b)
            return b
        tool("✓", f"완료 표시 ({cfg['hotkey_done']})" if cfg["hotkey_done_on"] else "완료 표시", self.toggle_done)
        self.btn_text_color = tool("A", "글자색", lambda: self._color_popup(self.btn_text_color, self.set_line_color))
        self.btn_bg = tool("■", "셀 배경색", lambda: self._color_popup(self.btn_bg, self._pick_cell_color))
        tool("↻", "반복 작업 추가", lambda: self._structured("repeat"))
        tool("⏰", "알림 추가", lambda: self._structured("alarm"))
        tool("⏳", "카운트다운 추가", lambda: self._structured("countdown"))
        bar.addStretch()
        save = QPushButton("저장 및 닫기")
        save.clicked.connect(self.save_and_close)
        bar.addWidget(save)
        bar.addWidget(QSizeGrip(self), 0, Qt.AlignBottom | Qt.AlignRight)
        lay.addLayout(bar)

        if cfg["hotkey_done_on"] and cfg["hotkey_done"]:
            QShortcut(QKeySequence(cfg["hotkey_done"]), self, self.toggle_done)
        QShortcut(QKeySequence("Esc"), self, self.save_and_close)

        self._load()
        self.edit.textChanged.connect(self._normalize)
        self._place(cell_rect)

    # ---------- 배치 ----------
    def _place(self, cell_rect):
        w, h = self.cfg["editor_size"]
        w, h = max(160, w, cell_rect.width()), max(150, h, cell_rect.height())
        screen = QApplication.screenAt(cell_rect.center()) or QApplication.primaryScreen()
        a = screen.availableGeometry()
        x = min(max(a.left(), cell_rect.left()), a.right() - w)
        y = min(max(a.top(), cell_rect.top()), a.bottom() - h)
        self.setGeometry(QRect(x, y, w, h))

    def _item_label(self, ev):
        if ev.task_list:
            return ("☑ " if ev.done else "☐ ") + ev.title + (" (예정)" if ev.virtual else "")
        icons = ("↻" if ev.recurring else "") + ("⏰" if ev.alarm else "") + ("⏳" if ev.countdown else "")
        if ev.all_day:
            when = "" if len(ev.days()) == 1 else f"[{ev.start:%m/%d}~{ev.end - timedelta(days=1):%m/%d}] "
        else:
            when = f"[{ev.start:%H:%M}-{ev.end:%H:%M}] "
        ro = "" if ev.writable else " (읽기 전용)"
        return f"{icons} {when}{ev.title}{ro}".strip()

    # ---------- 줄 서식 ----------
    def _fmt(self, line):
        f = QTextCharFormat()
        f.setForeground(QColor(line.color or self.cfg["item_font"]["color"]))
        f.setFontStrikeOut(line.done)
        return f

    def _apply(self, block):
        line = self._lines[block.userState()]
        cur = QTextCursor(block)
        cur.movePosition(QTextCursor.StartOfBlock)
        cur.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
        fmt = self._fmt(line)
        cur.setCharFormat(fmt)
        cur.setBlockCharFormat(fmt)
        bf = QTextBlockFormat()  # 반복 일정 줄은 배경을 살짝 다르게(1-8)
        if line.ev is not None and line.ev.recurring:
            bf.setBackground(QColor("#2f3746"))
        cur.setBlockFormat(bf)

    def _load(self):
        self._guard = True
        doc = self.edit.document()
        doc.clear()
        cur = QTextCursor(doc)
        for i, ev in enumerate(self.line_events):
            if i:
                cur.insertBlock()
            cur.insertText(line_text(ev.title))
            self._lines.append(_Line(ev, ev.done, ev.text_color))
            cur.block().setUserState(len(self._lines) - 1)
        if not self.line_events:
            self._lines.append(_Line())
            doc.firstBlock().setUserState(0)
        b = doc.firstBlock()
        while b.isValid():
            self._apply(b)
            b = b.next()
        self._guard = False
        self.edit.moveCursor(QTextCursor.End)

    def _normalize(self):
        """새로 생긴 줄(Enter로 나뉜 줄)은 기본 서식의 새 일정으로 만든다."""
        if self._guard:
            return
        self._guard = True
        seen = set()
        b = self.edit.document().firstBlock()
        while b.isValid():
            nxt = b.next()
            ln = self._lines[b.userState()] if b.userState() >= 0 else None
            if ln and not b.text() and nxt.isValid() and nxt.userState() < 0 and nxt.text() \
                    and (ln.ev is None or line_text(ln.ev.title) == line_text(nxt.text())):
                # 줄 맨 앞에서 Enter: Qt는 번호를 위쪽 빈 줄에 남기고 글자를 새 줄로 내린다 → 번호도 글자를 따라 내린다
                nxt.setUserState(b.userState())
                b.setUserState(-1)
                self._apply(nxt)
            st = b.userState()
            if st < 0 or st in seen:  # 새 줄, 또는 줄 복사로 같은 번호가 둘 생긴 경우
                self._lines.append(_Line())
                b.setUserState(len(self._lines) - 1)
                self._apply(b)
            seen.add(b.userState())
            b = b.next()
        self._guard = False

    def _current_lines(self):
        """선택 영역(없으면 커서 줄)에 걸친 줄들."""
        cur = self.edit.textCursor()
        doc = self.edit.document()
        b = doc.findBlock(cur.selectionStart())
        end = doc.findBlock(cur.selectionEnd())
        out = []
        while b.isValid():
            if b.userState() >= 0:
                out.append(b)
            if b == end:
                break
            b = b.next()
        return out

    def toggle_done(self):
        blocks = self._current_lines()
        if not blocks:
            return
        target = not all(self._lines[b.userState()].done for b in blocks)
        self._guard = True
        for b in blocks:
            self._lines[b.userState()].done = target
            self._apply(b)
        self._guard = False
        if target:
            play_sound(self.cfg["done_sound"])

    def set_line_color(self, color):
        self._remember(color)
        self._guard = True
        for b in self._current_lines():
            self._lines[b.userState()].color = color
            self._apply(b)
        self._guard = False

    def _remember(self, color):
        if color:
            if color in self.recent:
                self.recent.remove(color)
            self.recent.insert(0, color)
            del self.recent[8:]

    def _color_popup(self, button, fn):
        color_menu(fn, self.recent, parent=self).exec(button.mapToGlobal(button.rect().bottomLeft()))

    def _pick_cell_color(self, color):
        self._remember(color)
        self.cellColorPicked.emit(self.day, color)

    def _context_menu(self, pos):
        m = self.edit.createStandardContextMenu()
        m.addSeparator()
        blocks = self._current_lines()
        all_done = blocks and all(self._lines[b.userState()].done for b in blocks)
        m.addAction("미완료로 표시" if all_done else "완료로 표시", self.toggle_done)
        cm = color_menu(self.set_line_color, self.recent, none_label="기본 글자색", parent=m)
        cm.setTitle("글자색")
        m.addMenu(cm)
        m.addSeparator()
        m.addAction("반복 작업 추가", lambda: self._structured("repeat"))
        m.addAction("알림 추가", lambda: self._structured("alarm"))
        m.addAction("카운트다운 추가", lambda: self._structured("countdown"))
        m.exec(pos)

    # ---------- 저장 ----------
    def collect(self):
        """편집 내용 → (구글 작업 목록, 화면 반영용 줄 목록, 범위를 물어야 하는 반복 일정 변경)."""
        lines = []
        b = self.edit.document().firstBlock()
        while b.isValid():
            text = b.text().strip()
            if text:
                ln = self._lines[b.userState()] if b.userState() >= 0 else _Line()
                lines.append({"text": text, "ev": ln.ev, "done": ln.done, "color": ln.color or ""})
            b = b.next()
        used = {ln["ev"].id for ln in lines if ln["ev"]}
        orphans = [e for e in self.line_events if e.id not in used]
        for ln in lines:  # 줄을 나누거나 합쳐서 번호를 잃은 줄은 같은 내용의 기존 일정과 다시 짝짓는다
            if ln["ev"] is None:
                match = next((e for e in orphans if line_text(e.title) == line_text(ln["text"])), None)
                if match:
                    ln["ev"] = match
                    orphans.remove(match)
        ops, ask = [], []
        alarm = ""
        if self.cfg["default_alarm_on"] and self.cfg["default_alarm_lines"]:
            alarm = f"{self.cfg['default_alarm_time']}|{','.join(str(x) for x in self.cfg['default_alarm_days'])}"
        # 무엇이든 바뀌었으면 순서 번호를 모두 새로 매긴다(휴대폰에서 만든 일정은 번호가 모두 0이라, 이름만 바꿔도
        # 제목순으로 다시 섞인다). 열었다 닫기만 했으면 구글에 쓰지 않는다.
        changed = [ln["ev"].id if ln["ev"] else None for ln in lines] != [e.id for e in self.line_events] or any(
            (ln["ev"].done, ln["ev"].text_color or "", line_text(ln["ev"].title))
            != (ln["done"], ln["color"], ln["text"])
            for ln in lines if ln["ev"])
        for i, ln in enumerate(lines):
            ev = ln["ev"]
            props = {"done": ln["done"], "text_color": ln["color"], "order": i if changed or not ev else ev.order,
                     "line": True}
            if ev:
                renamed = line_text(ev.title) != ln["text"]  # 열었을 때 보인 글자와 비교(공백만 고친 것도 이름 바꾸기)
                if ev.recurring and renamed:  # 반복 일정 이름 바꾸기는 범위를 묻는다
                    ask.append(("rename", ev, ln["text"]))
                own = (ev.done, ev.text_color or "") != (ln["done"], ln["color"]) or (renamed and not ev.recurring)
                if own or ev.order != props["order"]:
                    # 그 줄을 직접 고쳤을 때만 '줄'로 표시한다. 순서 번호만 바뀐 일정(휴대폰에서 만든 약속 등)이
                    # 줄이 되면 '미완료 자동 연기'가 오늘로 옮겨 버린다.
                    props.update(alarm=ev.alarm, countdown=ev.countdown, line=ev.line or (own and not ev.recurring))
                    body = {"extendedProperties": {"private": event_props(props)}}
                    if renamed and not ev.recurring:  # 이름을 안 바꿨으면 원래 제목(줄바꿈·공백 포함)을 그대로 둔다
                        body["summary"] = ln["text"]
                    ops.append(("patch", ev.calendar_id, ev.id, body))
            elif self.default_cal:
                v = dict(props, title=ln["text"], all_day=True, start=self.day, end=self.day, alarm=alarm,
                         description="", location="", calendar_id=self.default_cal)
                ops.append(("create", self.default_cal, v))
        for e in orphans:
            if e.recurring:
                ask.append(("delete", e, None))
            else:
                ops.append(("delete", e.calendar_id, e.id))
        return ops, lines, ask

    def commit(self):
        ops, lines, ask = self.collect()
        self.cfg["editor_size"] = [self.width(), self.height()]
        series = []
        if ask:
            verb = "삭제" if all(a[0] == "delete" for a in ask) else "수정"
            scope = ask_scope(None, verb)
            for kind, ev, title in ask if scope else []:
                if scope == "one":  # 이 날짜 회차만: 그냥 이 회차를 고치거나 지운다
                    if kind == "rename":
                        ops.append(("patch", ev.calendar_id, ev.id, {"summary": title}))
                    else:
                        ops.append(("delete", ev.calendar_id, ev.id))
                elif kind == "rename":
                    series.append(("update_event", ev, dict(ev.values(), title=title), scope))
                else:
                    series.append(("delete_event", ev, scope))
            if not scope:  # 취소: 반복 일정 이름은 원래대로 보이게 한다
                for ln in lines:
                    if ln["ev"] is not None and ln["ev"].recurring:
                        ln["text"] = ln["ev"].title
        if ops or series:
            self.committed.emit(self.day, ops, lines, series)

    def save_and_close(self):
        if self._closing:
            return
        self._closing = True
        self.commit()
        self.close()

    def _open_event(self, ev):
        self.save_and_close()
        self.openEvent.emit(ev)

    def _structured(self, kind):
        """반복·알림·카운트다운 추가: 커서 줄의 글자를 새 일정 창의 제목으로 넘긴다. 아직 저장하지 않은 새 줄이면
        그 줄은 칸에서 뺀다(같은 날에 한 줄 일정과 반복 일정이 둘 다 생기지 않게)."""
        title = ""
        blocks = self._current_lines()
        if blocks:
            b = blocks[0]
            title = b.text().strip()
            if title and self._lines[b.userState()].ev is None:
                cur = QTextCursor(b)
                cur.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
                cur.removeSelectedText()
        self.save_and_close()
        self.newStructured.emit(self.day, kind, title)

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == e.Type.ActivationChange and not self.isActiveWindow():
            QTimer.singleShot(150, self._maybe_autosave)

    def _maybe_autosave(self):
        """다른 곳을 누르면(포커스를 잃으면) DesktopCal처럼 저장하고 닫는다."""
        if self._closing or self.isActiveWindow():
            return
        if QApplication.activePopupWidget() or QApplication.activeModalWidget():
            return
        self.save_and_close()
