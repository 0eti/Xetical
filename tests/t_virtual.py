import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, ".")
SP = Path(sys.argv[1])
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

qapp = QApplication([])
import xetical.config as C
C.Config.save = lambda self: None
import xetical.jobs as J
J.OUTBOX_PATH = SP / "outbox_v.json"
import xetical.app as A
from xetical.model import Event

a = A.App()
sent = []
a.outbox.add = lambda op, *args, **kw: sent.append((op, args[0].id, args[1]))
T = date.today()
past = T - timedelta(days=3)  # 화면 밖(지난주)에 있는 진짜 할 일
cals = [{"id": "tasks:L1", "summary": "할 일", "color": "#4285f4", "primary": False, "writable": False,
         "selected": True, "tasks": True}]
real = Event("task:L1:k1", "tasks:L1", "화분 물 주기", past, past + timedelta(days=1), True, task_list="L1")
a.store.set(cals, [real], {}, (T - timedelta(days=30), T + timedelta(days=60)))
a.cfg["task_repeats"] = {"L1|화분 물 주기": "weekly"}
a._project_task_repeats()
w = a.win
w.anchor = w.today_anchor()
w.resize(800, 600)
w.grab()
hits = [(r, ev) for r, ev in w._check_hits]
print("화면의 ☐ 자리:", [(ev.first_day.isoformat(), ev.virtual) for _r, ev in hits])
r, ev = hits[0]
w.on_press(r.center(), w.mapToGlobal(r.center().toPoint()), Qt.LeftButton)
print("보낸 작업:", sent, "| 진짜 할 일 완료됨:", real.done, "| 안내:", w.status)
a.shutdown()
