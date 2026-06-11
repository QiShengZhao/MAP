from sqlalchemy import select
from app.domain.models import RunState

class StateService:
    @staticmethod
    async def load(db, tenant_id, run_id):
        return (await db.execute(select(RunState).where(
            RunState.run_id == run_id,
            RunState.tenant_id == tenant_id))).scalar_one_or_none()

    @staticmethod
    async def checkpoint(db, tenant_id, run_id, *, current_agent, turn,
                         history, pending_tool_call=None):
        state = await StateService.load(db, tenant_id, run_id)
        if state is None:
            state = RunState(run_id=run_id, tenant_id=tenant_id)
            db.add(state)
        state.current_agent = current_agent
        state.turn = turn
        state.history = history
        state.pending_tool_call = pending_tool_call
        await db.flush()
        return state

    @staticmethod
    async def clear(db, tenant_id, run_id):
        state = await StateService.load(db, tenant_id, run_id)
        if state:
            await db.delete(state)