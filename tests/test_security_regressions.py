import pytest

from app.api.deps import AuthContext
from app.api.routes_ws import authenticate_websocket, validate_workspace_access
from app.domain.models import Workspace
from app.security.jwt_keys import issue_access, issue_refresh


async def test_websocket_rejects_refresh_token(db_session, redis, monkeypatch):
    from app.api import routes_ws

    monkeypatch.setattr(routes_ws, "redis_client", redis)
    token = issue_refresh("user-1", "tenant-1")

    with pytest.raises(ValueError, match="access token"):
        await authenticate_websocket(token, "tenant-1", db_session)


async def test_websocket_rejects_client_tenant_mismatch(
        db_session, redis, monkeypatch):
    from app.api import routes_ws

    monkeypatch.setattr(routes_ws, "redis_client", redis)
    token = issue_access("user-1", "tenant-1", "member")

    with pytest.raises(ValueError, match="tenant mismatch"):
        await authenticate_websocket(token, "tenant-2", db_session)


async def test_websocket_requires_workspace_membership(db_session):
    workspace = Workspace(
        id="workspace-1", tenant_id="tenant-1", name="Private")
    db_session.add(workspace)
    await db_session.commit()

    auth = AuthContext(
        user_id="user-1", tenant_id="tenant-1", role="member")
    with pytest.raises(ValueError, match="workspace access denied"):
        await validate_workspace_access(workspace.id, auth, db_session)


async def test_websocket_admin_can_access_tenant_workspace(db_session):
    workspace = Workspace(
        id="workspace-1", tenant_id="tenant-1", name="Ops")
    db_session.add(workspace)
    await db_session.commit()

    auth = AuthContext(
        user_id="admin-1", tenant_id="tenant-1", role="admin")
    result = await validate_workspace_access(workspace.id, auth, db_session)

    assert result.id == workspace.id

