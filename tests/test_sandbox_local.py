import pytest

from app.runtime.sandbox_local import LocalSandboxBackend


@pytest.fixture
async def sb():
    b = LocalSandboxBackend()
    h = await b.ensure("tenant-a", "sess-1")
    yield b, h
    await b.terminate(h)


async def test_exec_basic(sb):
    b, h = sb
    r = await b.exec(h, "echo hello && echo err >&2; exit 3")
    assert r.exit_code == 3
    assert "hello" in r.stdout
    assert "err" in r.stderr


async def test_timeout_kills_process_group(sb):
    b, h = sb
    r = await b.exec(h, "sleep 30", timeout=1)
    assert r.exit_code == 124


async def test_workdir_isolation_between_sessions():
    b = LocalSandboxBackend()
    h1 = await b.ensure("t", "s1")
    h2 = await b.ensure("t", "s2")
    await b.put_file(h1, "secret.txt", b"x")
    with pytest.raises(FileNotFoundError):
        await b.get_file(h2, "secret.txt")
    await b.terminate(h1)
    await b.terminate(h2)


async def test_path_escape_blocked(sb):
    b, h = sb
    with pytest.raises(PermissionError):
        await b.put_file(h, "../../etc/cron.d/evil", b"x")
