from app.domain.models import RunEvent
from app.execution.runner import Runner


async def test_runner_loads_existing_event_sequence(db_session):
    db_session.add(RunEvent(tenant_id="t1", run_id="r1", seq=3,
                            type="run.started", payload={}))
    await db_session.commit()

    runner = Runner("t1", "r1")

    assert await runner._load_event_seq(db_session) == 3
