import pytest

from app.domain.models import Run, RunStatus
from app.execution.run_statemachine import (ALLOWED, InvalidTransition,
                                            StaleTransition, transition,
                                            utcnow_naive)


async def _mk_run(db, status="running"):
    run = Run(tenant_id="t1", session_id="s1", user_id="u1",
              status=RunStatus(status))
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return str(run.id)


async def test_legal_pause_and_resume_cycle(db_session):
    rid = await _mk_run(db_session, "running")
    await transition(db_session, rid, "running", "paused", reason="risk:r1")
    await transition(db_session, rid, "paused", "queued", reason="resume")
    await transition(db_session, rid, "queued", "running")


@pytest.mark.parametrize("frm,to", [
    ("queued", "paused"),
    ("paused", "running"),
    ("completed", "queued"),
    ("paused", "awaiting_approval"),
])
async def test_illegal_transitions(db_session, frm, to):
    rid = await _mk_run(db_session, frm)
    with pytest.raises(InvalidTransition):
        await transition(db_session, rid, frm, to)


async def test_cas_detects_concurrent_change(db_session):
    rid = await _mk_run(db_session, "running")
    await transition(db_session, rid, "running", "cancelled")
    with pytest.raises(StaleTransition):
        await transition(db_session, rid, "running", "paused")


def test_every_status_has_transition_entry():
    assert set(ALLOWED) == {"queued", "running", "awaiting_approval",
                            "paused", "completed", "failed", "cancelled"}


def test_transition_timestamps_match_model_timezone():
    assert utcnow_naive().tzinfo is None
