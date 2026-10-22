"""구글에 보낼 변경을 줄 세워 보내는 '보낼 편지함'.

- 인터넷이 끊겨 있으면 변경을 data/outbox.json에 보관했다가, 연결되면 순서대로 다시 보낸다.
- 화면에는 먼저 반영하려고 새 일정에 'tmp-N' 임시 id를 붙인다. 구글이 진짜 id를 돌려주면
  id_map에 적어 두고, 뒤에 오는 작업(수정·삭제·되돌리기)에 들어 있는 임시 id를 진짜 id로 바꿔 보낸다.
"""
import logging
import socket
import uuid
from datetime import date, datetime

import httplib2
from google.auth.exceptions import TransportError
from googleapiclient.errors import HttpError
from PySide6.QtCore import QObject, QTimer, Signal

from .config import OUTBOX_PATH, load_json, save_json
from .gcal import ApiDisabledError, AuthError, is_transient
from .model import KEEP, NO_REPEAT, Event

log = logging.getLogger("xetical")
RETRY_MS = 60_000


# ---------- 직렬화 (날짜·Event를 JSON으로) ----------
def encode(o):
    if isinstance(o, Event):
        return {"$e": o.to_json()}
    if isinstance(o, datetime):
        return {"$t": o.isoformat()}
    if isinstance(o, date):
        return {"$d": o.isoformat()}
    if isinstance(o, dict):
        return {k: encode(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [encode(v) for v in o]
    return o


def decode(o):
    if isinstance(o, dict):
        if "$e" in o:
            return Event.from_json(o["$e"])
        if "$t" in o:
            return datetime.fromisoformat(o["$t"])
        if "$d" in o:
            return date.fromisoformat(o["$d"])
        return {k: decode(v) for k, v in o.items()}
    if isinstance(o, list):
        return [decode(v) for v in o]
    return o


def resolve(o, id_map):
    """구조 안의 임시 id 문자열을 진짜 id로 바꾼다."""
    if isinstance(o, str):
        return id_map.get(o, o)
    if isinstance(o, dict):
        return {k: resolve(v, id_map) for k, v in o.items()}
    if isinstance(o, list):
        return [resolve(v, id_map) for v in o]
    return o


def tmp_ids(o):
    """구조(encode한 것) 안에 든 임시 id(tmp-…) 문자열들."""
    if isinstance(o, str):
        return {o} if o.startswith("tmp-") else set()
    if isinstance(o, dict):
        return set().union(*map(tmp_ids, o.values()))
    if isinstance(o, list):
        return set().union(*map(tmp_ids, o))
    return set()


def new_tmp():
    return f"tmp-{uuid.uuid4().hex[:12]}"


def is_offline(e):
    """다시 시도하면 될 오류(인터넷 끊김·구글 서버 일시 장애·요청 한도 초과)인가."""
    if isinstance(e, HttpError):
        return is_transient(e)
    return isinstance(e, (httplib2.HttpLib2Error, TransportError, socket.error, TimeoutError, ConnectionError))


def assign_event_ids(op, args):
    """새로 만들 구글 일정에 id를 미리 정해 붙이고 그 목록을 돌려준다(args는 encode한 사본).
    다시 보내도 구글이 409(이미 있음)로 막아 주니 두 번 만들어지지 않고, 임시 id의 짝도 바로 알 수 있다."""
    if op == "create_event":
        values = [args[0]]
    elif op == "apply_ops":
        values = [o[2] for o in args[0] if o[0] == "create"]
    elif op == "update_event" and len(args) > 2 and args[2] == "following":  # '이번과 이후'는 뒤쪽 반복을 새로 만든다
        values = [args[1]]
    else:
        return []
    for v in values:
        v.setdefault("event_id", uuid.uuid4().hex)  # 구글 일정 id는 0-9a-v 글자만 쓸 수 있다
    return [v["event_id"] for v in values] if op != "update_event" else []


def upgrade_job(job):
    """예전(2026-10-21 전) 작업을 지금 약속에 맞춘다.
    - 일정 수정 값의 recurrence ""는 그때 '규칙 그대로'였다 → KEEP
    - '이번과 이후'에 미리 정한 id가 없다 → 붙여서 다시 보내도 두 번 만들어지지 않게"""
    if job.get("v", 1) < 2 and job["op"] == "update_event":
        v = job["args"][1]
        if isinstance(v, dict) and v.get("recurrence") == NO_REPEAT:
            v["recurrence"] = KEEP
        assign_event_ids(job["op"], job["args"])
    job["v"] = 2
    return job


def run_job(gcal, job, id_map):
    """작업 스레드에서 실행. 새로 만든 항목들의 진짜 id 목록을 돌려준다."""
    args = decode(resolve(job["args"], id_map))
    result = getattr(gcal, job["op"])(*args)
    if isinstance(result, list):
        return result
    return [result] if isinstance(result, str) else []


class Outbox(QObject):
    changed = Signal()            # 대기 개수가 바뀜
    drained = Signal()            # 다 보냄
    blocked = Signal(object)      # 보내지 못하고 멈춤(오류 객체)
    failed = Signal(str, object)  # 한 작업이 거절당해 버림(작업 이름, 오류)

    def __init__(self, gcal, worker):
        super().__init__()
        self.gcal, self.worker = gcal, worker
        data = load_json(OUTBOX_PATH, {})
        self.jobs = [upgrade_job(j) for j in data.get("jobs", [])]
        self.id_map = data.get("ids", {})
        self.busy = False
        self.keep_ids = lambda: []  # 짝을 버리면 안 되는 다른 곳(되돌리기)의 작업들. app이 넣어 준다
        self.retry = QTimer(self, singleShot=True, interval=RETRY_MS, timeout=self.process)

    def __len__(self):
        return len(self.jobs)

    def _save(self):
        if len(self.id_map) > 500:  # 오래된 짝은 버리되, 아직 보낼 작업·되돌리기가 가리키는 임시 id의 짝은 남긴다
            keep = tmp_ids(self.jobs) | tmp_ids(encode(self.keep_ids()))
            items = list(self.id_map.items())
            self.id_map = {k: v for i, (k, v) in enumerate(items) if i >= len(items) - 300 or k in keep}
        save_json(OUTBOX_PATH, {"jobs": self.jobs, "ids": self.id_map})

    def add(self, op, *args, tmps=(), label=""):
        args = encode(list(args))
        for tmp, real in zip(tmps, assign_event_ids(op, args)):
            if tmp:
                self.id_map[tmp] = real
        self.jobs.append({"op": op, "args": args, "tmps": list(tmps), "label": label, "v": 2})
        self._save()
        self.changed.emit()
        self.process()

    def clear(self):
        """로그아웃: 이 계정에 보내려던 변경과 임시 id 짝을 버린다(다른 계정으로 보내지 않게)."""
        self.jobs, self.id_map = [], {}
        self.retry.stop()
        self._save()
        self.changed.emit()

    def real_id(self, any_id):
        return self.id_map.get(any_id, any_id)

    def process(self):
        if self.busy or not self.jobs:
            return
        self.busy = True
        job = self.jobs[0]
        self.worker.submit(run_job, self.gcal, job, dict(self.id_map), callback=lambda r, e: self._done(job, r, e))

    def _done(self, job, created, err):
        self.busy = False
        creates = job["op"].startswith("create") or job["op"] == "apply_ops"
        if isinstance(err, HttpError) and err.resp.status in (404, 410) and not creates:
            err = None  # 이미 지워졌거나 없는 일정을 고치거나 지우려 한 것 → 할 일이 없으니 성공으로 친다
            # (만들기가 404면 캘린더·목록이 없어진 것이라 실패로 알린다. 칸 편집 묶음에서 고치기·지우기의
            #  404는 _batch가 이미 성공으로 걸러 내므로, 여기까지 온 404는 만들기다)
        if err is not None and (is_offline(err) or isinstance(err, (AuthError, ApiDisabledError))):
            self.retry.start()
            self.blocked.emit(err)
            return
        if self.jobs and self.jobs[0] is job:
            self.jobs.pop(0)
        if err is not None:
            log.error("변경을 보내지 못해 버림: %s", job.get("label") or job["op"], exc_info=err)
            self.failed.emit(job.get("label") or job["op"], err)
        else:
            for tmp, real in zip(job.get("tmps", []), created or []):
                if tmp and real:
                    self.id_map[tmp] = real
        self._save()
        self.changed.emit()
        if self.jobs:
            self.process()
        else:
            self.drained.emit()
