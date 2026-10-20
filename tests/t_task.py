import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, ".")
SP = Path(sys.argv[1])
from PySide6.QtWidgets import QApplication, QDialogButtonBox

qapp = QApplication([])
import xetical.config as C
C.Config.save = lambda self: None
import xetical.jobs as J
J.OUTBOX_PATH = SP / "outbox_t.json"
import xetical.app as A
from xetical.dialogs import TaskDialog
from xetical.model import Event

a = A.App()
a.outbox.add = lambda *args, **kw: print("  보낼 작업:", args[0])
a.sync = lambda: None
T = date.today()
cals = [{"id": "tasks:L1", "summary": "할 일: 내 할 일 목록", "color": "#4285f4", "primary": False, "writable": False,
         "selected": True, "tasks": True}]
ev = Event("task:L1:k1", "tasks:L1", "화분 물 주기", T, T + timedelta(days=1), True, task_list="L1")
a.store.set(cals, [ev], {}, (T - timedelta(days=30), T + timedelta(days=60)))

for label in ("취소", "저장 및 동기화"):
    a._open_task_dialog(ev)
    dlg = next(d for d in a._dialogs if isinstance(d, TaskDialog))
    qapp.processEvents()
    btn = next(b for b in dlg.findChildren(QDialogButtonBox)[0].buttons() if b.text() == label)
    btn.click()
    qapp.processEvents()
    print(f"[{label}] 누른 뒤 창 보임: {dlg.isVisible()} | 남은 할 일 창 수: {sum(isinstance(d, TaskDialog) and d.isVisible() for d in a._dialogs)}")
a.shutdown()
