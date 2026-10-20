"""Parallel uploads: several worker slots pick different jobs at the same time."""

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app import bandwidth, network, transfers
from app.db import SessionLocal
from app.models import TransferJob, TransferStatus


@pytest.fixture
def only_these_jobs(monkeypatch):
    """Park every queued job, hand back a helper that queues fresh ones.

    The DB is shared across tests; parked jobs get their due time back
    afterwards, and the policy is restored.
    """
    from tests.test_tether import _queue_job

    with SessionLocal() as db:
        parked = {
            j.id: j.next_attempt_at
            for j in db.scalars(
                select(TransferJob).where(TransferJob.status == TransferStatus.QUEUED)
            )
        }
        for job_id in parked:
            db.get(TransferJob, job_id).next_attempt_at = transfers._now() + timedelta(days=1)
        policy = bandwidth.get_policy(db)
        saved = (policy.enabled, policy.parallel_uploads, policy.bwlimit_kbps,
                 policy.metered_mode, policy.metered_budget_mb, policy.metered_used_bytes,
                 policy.metered_day, policy.schedule_json)
        policy.enabled = False
        policy.schedule_json = "[]"
        db.commit()
    monkeypatch.setattr(network, "metered_uplink", lambda: (False, "WLAN Home"))

    def queue(client, admin_auth, size):
        media = _queue_job(client, admin_auth, b"p" * size)
        with SessionLocal() as db:
            return db.scalar(select(TransferJob.id).where(TransferJob.media_id == media["id"]))

    yield queue
    with SessionLocal() as db:
        for job_id, when in parked.items():
            job = db.get(TransferJob, job_id)
            if job is not None:
                job.next_attempt_at = when
        policy = bandwidth.get_policy(db)
        (policy.enabled, policy.parallel_uploads, policy.bwlimit_kbps,
         policy.metered_mode, policy.metered_budget_mb, policy.metered_used_bytes,
         policy.metered_day, policy.schedule_json) = saved
        db.commit()
    transfers._inflight.clear()


def _set(**fields):
    with SessionLocal() as db:
        policy = bandwidth.get_policy(db)
        for key, value in fields.items():
            setattr(policy, key, value)
        db.commit()


def test_second_slot_takes_a_different_job(client, admin_auth, monkeypatch, only_these_jobs):
    first = only_these_jobs(client, admin_auth, 64)
    second = only_these_jobs(client, admin_auth, 64)
    started = []

    def fake_process_job(db, job, **kw):
        started.append(job.id)
        if len(started) == 1:
            # While the first upload is still running, another slot polls.
            assert transfers.inflight_count() == 1
            assert transfers.process_one() is True

    monkeypatch.setattr(transfers, "process_job", fake_process_job)
    assert transfers.process_one() is True
    assert sorted(started) == sorted([first, second])
    assert transfers.inflight_count() == 0


def test_no_job_left_for_a_second_slot(client, admin_auth, monkeypatch, only_these_jobs):
    only_these_jobs(client, admin_auth, 64)
    nested = []

    def fake_process_job(db, job, **kw):
        nested.append(transfers.process_one())

    monkeypatch.setattr(transfers, "process_job", fake_process_job)
    assert transfers.process_one() is True
    assert nested == [False]


def test_metered_budget_counts_running_uploads(client, admin_auth, monkeypatch, only_these_jobs):
    only_these_jobs(client, admin_auth, 4096)
    only_these_jobs(client, admin_auth, 4096)
    # 6 KB left: one 4 KB file fits, but not a second one next to it.
    _set(metered_mode="limit", metered_budget_mb=1,
         metered_used_bytes=1024 * 1024 - 6144, metered_day=date.today().isoformat())
    monkeypatch.setattr(network, "metered_uplink", lambda: (True, "USB-Tethering (Android)"))
    nested = []

    def fake_process_job(db, job, **kw):
        assert kw["metered"] is True
        if not nested:
            nested.append(transfers.process_one())

    monkeypatch.setattr(transfers, "process_job", fake_process_job)
    assert transfers.process_one() is True
    assert nested == [False]


def test_bwlimit_is_shared_between_slots(client, admin_auth, monkeypatch, only_these_jobs):
    only_these_jobs(client, admin_auth, 64)
    _set(parallel_uploads=2, bwlimit_kbps=100)
    seen = []
    monkeypatch.setattr(
        transfers, "process_job", lambda db, job, **kw: seen.append(kw["bwlimit_kbps"])
    )
    assert transfers.process_one() is True
    assert seen == [50]


def test_slot_active_follows_setting(only_these_jobs):
    _set(parallel_uploads=2)
    assert [transfers._slot_active(i) for i in range(4)] == [True, True, False, False]
    _set(parallel_uploads=1)
    assert [transfers._slot_active(i) for i in range(4)] == [True, False, False, False]


def test_bandwidth_api_parallel_uploads(client, admin_auth, only_these_jobs):
    body = client.put("/api/bandwidth", headers=admin_auth, json={"parallel_uploads": 3}).json()
    assert body["parallel_uploads"] == 3
    assert body["running_uploads"] == 0
    bad = client.put("/api/bandwidth", headers=admin_auth, json={"parallel_uploads": 5})
    assert bad.status_code == 422
