from datetime import datetime, timedelta
import json
import logging
import threading
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from sqlalchemy import and_, or_, select, update

from src.alarm_push.media import alarm_video_path, public_media_url
from src.core.settings import settings
from src.db.database import SessionLocal, engine
from src.db.models import AlarmPushJob, AlarmPushTarget


logger = logging.getLogger(__name__)
CONFIG_TARGET_ID = "config-default"
EVENT_FIELDS = (
    "id", "channel_id", "task_id", "algorithm_id", "model_id", "event_type",
    "class_name", "conf", "snapshot_path", "video_path", "content",
    "handling_status", "remark", "status", "created_at", "updated_at",
)


def ensure_schema():
    for table in (AlarmPushTarget.__table__, AlarmPushJob.__table__):
        table.create(bind=engine, checkfirst=True)
    sync_config_target()


def validate_push_url(value: str) -> str:
    url = value.strip()
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("push_url must be an HTTP or HTTPS URL")
    if parts.username is not None or parts.password is not None or parts.fragment:
        raise ValueError("push_url cannot contain credentials or a fragment")
    # Validate malformed/out-of-range ports before the worker sends a request.
    _ = parts.port
    return url


def sync_config_target(session_factory=SessionLocal):
    """The configuration file owns one stable target; API targets remain unchanged."""
    url = settings.ALARM_PUSH_URL.strip()
    enabled = settings.ALARM_PUSH_ENABLED
    if enabled and not url:
        raise ValueError("ALARM_PUSH_URL is required when ALARM_PUSH_ENABLED is true")
    if url:
        url = validate_push_url(url)
    now = datetime.now()
    with session_factory() as session:
        target = session.get(AlarmPushTarget, CONFIG_TARGET_ID)
        if target is None:
            if not url:
                return
            target = AlarmPushTarget(
                id=CONFIG_TARGET_ID, name="Configured Java alarm receiver", created_at=now,
            )
            session.add(target)
        if url:
            target.push_url = url
        target.enabled = enabled
        target.updated_at = now
        session.commit()


def enqueue_event(session, event: dict, *, need_video_clip: bool = False):
    """Use the caller's event transaction; never make network requests here."""
    if event.get("event_type") == "debug_detection":
        return
    targets = session.scalars(select(AlarmPushTarget).where(AlarmPushTarget.enabled.is_(True))).all()
    if not targets:
        return
    payload = {key: event.get(key) for key in EVENT_FIELDS}
    payload["snapshot_path"] = public_media_url(payload["snapshot_path"])
    video = payload["video_path"]
    if not video and need_video_clip:
        video = str(alarm_video_path(str(event["id"])))
    payload["video_path"] = public_media_url(video)
    _enqueue_payload(session, str(event["id"]), payload, targets=targets)


def enqueue_metric_history(session, history: dict):
    payload = {"message_type": "metric_history", **history}
    payload["snapshot_path"] = public_media_url(history.get("snapshot_path"))
    _enqueue_payload(session, f"metric_history:{history['id']}", payload)


def _enqueue_payload(session, source_id: str, payload: dict, *, targets=None):
    if targets is None:
        targets = session.scalars(select(AlarmPushTarget).where(AlarmPushTarget.enabled.is_(True))).all()
    now = datetime.now()
    for target in targets:
        session.add(AlarmPushJob(
            id=uuid4().hex, target_id=target.id, event_id=source_id,
            payload_json=dict(payload), status="pending", attempts=0,
            created_at=now, updated_at=now,
        ))


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def send_json(url: str, payload: dict, *, timeout: float, idempotency_key: str) -> int:
    request = Request(
        url, method="POST", data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Idempotency-Key": idempotency_key, "hbjq": "hbjq"},
    )
    try:
        with build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            return response.status
    except HTTPError as exc:
        try:
            return exc.code
        finally:
            exc.close()


class AlarmPushWorker:
    def __init__(self, session_factory=SessionLocal, sender=send_json):
        self.session_factory = session_factory
        self.sender = sender
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="alarm-push", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=max(settings.ALARM_PUSH_TIMEOUT_SECONDS, 1) + 2)

    def _run(self):
        while not self._stop.is_set():
            try:
                self.process_once()
            except Exception:
                logger.exception("Alarm push polling failed")
            self._stop.wait(1)

    def process_once(self):
        now = datetime.now()
        due = or_(
            and_(AlarmPushJob.status.in_(("pending", "retry")),
                 or_(AlarmPushJob.next_retry_at.is_(None), AlarmPushJob.next_retry_at <= now)),
            and_(AlarmPushJob.status == "sending", AlarmPushJob.lease_until <= now),
        )
        with self.session_factory() as session:
            ids = session.scalars(
                select(AlarmPushJob.id).join(AlarmPushTarget, AlarmPushTarget.id == AlarmPushJob.target_id)
                .where(due, AlarmPushTarget.enabled.is_(True))
                .order_by(AlarmPushJob.created_at, AlarmPushJob.id).limit(20)
            ).all()
        for job_id in ids:
            if self._stop.is_set():
                break
            self._deliver(job_id)

    def _deliver(self, job_id):
        now = datetime.now()
        token = uuid4().hex
        maximum = max(settings.ALARM_PUSH_MAX_ATTEMPTS, 1)
        timeout = max(settings.ALARM_PUSH_TIMEOUT_SECONDS, 1)
        with self.session_factory() as session:
            job = session.get(AlarmPushJob, job_id)
            if job is None:
                return
            target = session.get(AlarmPushTarget, job.target_id)
            if target is None or not target.enabled:
                return
            # Conditional update claims one job even when multiple processes poll.
            due = or_(
                and_(AlarmPushJob.status.in_(("pending", "retry")),
                     or_(AlarmPushJob.next_retry_at.is_(None), AlarmPushJob.next_retry_at <= now)),
                and_(AlarmPushJob.status == "sending", AlarmPushJob.lease_until <= now),
            )
            if job.attempts >= maximum:
                session.execute(update(AlarmPushJob).where(AlarmPushJob.id == job_id, due).values(
                    status="failed", last_error="delivery attempts exhausted", claim_token=None,
                    lease_until=None, updated_at=now,
                ))
                session.commit()
                return
            claimed = session.execute(update(AlarmPushJob).where(AlarmPushJob.id == job_id, due).values(
                status="sending", claim_token=token, attempts=AlarmPushJob.attempts + 1,
                lease_until=now + timedelta(seconds=timeout + 30), updated_at=now,
            ), execution_options={"synchronize_session": False})
            if claimed.rowcount != 1:
                session.rollback()
                return
            payload, url, attempt = dict(job.payload_json), target.push_url, job.attempts + 1
            idempotency_key = f"{job.target_id}:{job.event_id}"
            session.commit()

        status_code = None
        error = None
        retryable = True
        try:
            status_code = self.sender(
                url, payload, timeout=timeout, idempotency_key=idempotency_key,
            )
            if not 200 <= status_code < 300:
                error = f"HTTP {status_code}"
                retryable = status_code in (408, 429) or status_code >= 500
        except Exception as exc:
            error = str(exc)[:2000]
        now = datetime.now()
        state = "success" if error is None else (
            "retry" if retryable and attempt < maximum else "failed"
        )
        with self.session_factory() as session:
            session.execute(update(AlarmPushJob).where(
                AlarmPushJob.id == job_id, AlarmPushJob.claim_token == token,
                AlarmPushJob.status == "sending",
            ).values(
                status=state, last_error=error, response_status=status_code,
                next_retry_at=now + timedelta(seconds=5 * (2 ** (attempt - 1))) if state == "retry" else None,
                sent_at=now if state == "success" else None,
                claim_token=None, lease_until=None, updated_at=now,
            ))
            session.commit()
        logger.info("Alarm push job=%s status=%s http=%s attempt=%s", job_id, state, status_code, attempt)


alarm_push_worker = AlarmPushWorker()
