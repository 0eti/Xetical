import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, ".")
SP = Path(sys.argv[1])
from PySide6.QtWidgets import QApplication

qapp = QApplication([])
import xetical.config as C
C.Config.save = lambda self: None
import xetical.jobs as J
J.OUTBOX_PATH = SP / "outbox_r.json"
J.OUTBOX_PATH.unlink(missing_ok=True)
import xetical.app as A
from xetical.model import Event

results = []


def check(name, cond, info=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{info}]" if info and not cond else ""))


def pump(cond, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        qapp.processEvents()
        if cond():
            return
        time.sleep(0.02)


class FakeG:
    def __init__(self):
        self.calls, self.n = [], 0

    def __getattr__(self, name):
        def fn(*args):
            self.calls.append((name,) + args)
            self.n += 1
            return f"task:L1:new{self.n}" if name == "create_task" else None
        return fn


T = date.today()
past = T - timedelta(days=3)
cals = [{"id": "tasks:L1", "summary": "할 일", "color": "#4285f4", "primary": False, "writable": False,
         "selected": True, "tasks": True}]


def setup(done):
    a = A.App()
    fake = FakeG()
    a.gcal = a.outbox.gcal = fake
    a.sync = lambda: None
    real = Event("task:L1:k1", "tasks:L1", "화분 물 주기", past, past + timedelta(days=1), True, task_list="L1", done=done)
    a.store.set(cals, [real], {}, (T - timedelta(days=30), T + timedelta(days=60)))
    a.cfg["task_repeats"] = {"L1|화분 물 주기": "weekly"}
    a._project_task_repeats()
    return a, fake, real


# 1) 지금 회차가 이미 완료된 상태(사용자 상황) → 예정이 다시 보여야 한다
a, fake, real = setup(done=True)
virt = sorted([e for e in a.store.events if e.virtual], key=lambda e: e.start)
check("완료된 회차 뒤로도 예정이 보임(버그 재현 → 고침)", virt and virt[0].first_day == past + timedelta(days=7), [e.first_day for e in virt][:3])
# 예정(다음 주)을 누름 → 그 날짜에 '완료된 할 일'을 만든다
a.toggle_task(virt[0])
pump(lambda: len(a.outbox) == 0)
c = next((x for x in fake.calls if x[0] == "create_task"), None)
check("예정을 누르면 그 날짜 회차를 완료된 할 일로 만듦", c and c[4] == past + timedelta(days=7) and c[5] is True, c)
virt2 = sorted([e for e in a.store.events if e.virtual], key=lambda e: e.start)
check("그 다음 회차부터 예정이 이어짐", virt2 and virt2[0].first_day == past + timedelta(days=14), [e.first_day for e in virt2][:3])
fake.calls.clear()
a.undo()
pump(lambda: len(a.outbox) == 0)
check("되돌리기: 만든 회차 삭제", any(x[0] == "delete_task" and x[1].id == "task:L1:new1" for x in fake.calls), fake.calls)
a.shutdown()

# 2) 지금 회차가 미완료 → 체크하면 완료 + 다음 회차를 진짜 할 일로 만든다
a, fake, real = setup(done=False)
a.toggle_task(real)
pump(lambda: len(a.outbox) == 0)
names = [x[0] for x in fake.calls]
nxt = next((x for x in fake.calls if x[0] == "create_task"), None)
check("체크하면 완료 + 다음 회차 생성", names[:2] == ["set_task_done", "create_task"] and nxt[4] == past + timedelta(days=7)
      and len(nxt) == 5, fake.calls)
virt = sorted([e for e in a.store.events if e.virtual], key=lambda e: e.start)
check("새 회차 뒤로 예정이 이어짐", virt and virt[0].first_day == past + timedelta(days=14))
check("안내 문구", "다음 회차" in a.win.status, a.win.status)
fake.calls.clear()
a.undo()
pump(lambda: len(a.outbox) == 0)
check("되돌리기: 완료 취소 + 만든 다음 회차 삭제",
      ("set_task_done", real, False) in [(x[0], x[1], x[2]) for x in fake.calls if x[0] == "set_task_done"] or
      any(x[0] == "set_task_done" and x[2] is False for x in fake.calls), fake.calls)
check("되돌리기: 다음 회차도 지움", any(x[0] == "delete_task" for x in fake.calls))
# 3) 다음 회차가 이미 있으면 또 만들지 않는다
fake.calls.clear()
real.done = False
a.store.events.append(Event("task:L1:k2", "tasks:L1", "화분 물 주기", past + timedelta(days=7), past + timedelta(days=8), True,
                            task_list="L1"))
a.store.reindex()
a.toggle_task(real)
pump(lambda: len(a.outbox) == 0)
check("다음 회차가 이미 있으면 중복으로 만들지 않음", not any(x[0] == "create_task" for x in fake.calls), fake.calls)
# 4) 반복이 아닌 할 일은 다음 회차를 만들지 않는다
fake.calls.clear()
plain = Event("task:L1:p", "tasks:L1", "일회성", T, T + timedelta(days=1), True, task_list="L1")
a.store.events.append(plain)
a.toggle_task(plain)
pump(lambda: len(a.outbox) == 0)
check("반복 아닌 할 일은 그대로", [x[0] for x in fake.calls] == ["set_task_done"], fake.calls)
a.shutdown()
print(f"\n{sum(results)}/{len(results)} 통과")
