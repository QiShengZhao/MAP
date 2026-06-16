import json, shlex
from dataclasses import dataclass
from typing import Callable, Awaitable
from app.runtime.sandbox import SandboxManager
from app.platform_services.artifact_service import ArtifactService
from app.memory.service import MemoryService

@dataclass
class ToolContext:
    tenant_id: str
    run_id: str
    session_id: str
    db: object
    emit: Callable[..., Awaitable]
    usage: object
    workspace_id: str = ""
    user_id: str = ""

class ToolDef:
    def __init__(self, name, schema, handler, high_risk=False):
        self.name, self.schema, self.handler = name, schema, handler
        self.high_risk = high_risk

class ToolRegistry:
    _tools: dict = {}

    @classmethod
    def register(cls, name, description, parameters, high_risk=False):
        def deco(fn):
            cls._tools[name] = ToolDef(
                name, {"name": name, "description": description,
                       "parameters": parameters}, fn, high_risk)
            return fn
        return deco

    @classmethod
    def for_tenant(cls, policy):
        inst = cls.__new__(cls)
        allowed = set(policy.allowed_tools or [])
        inst.tools = {n: t for n, t in cls._tools.items()
                      if not allowed or n in allowed}
        return inst

    def schemas(self, only=None):
        items = self.tools.values() if not only else \
                [t for n, t in self.tools.items() if n in only]
        return [{"type": "function", "function": t.schema} for t in items]

    def requires_approval(self, name, policy):
        t = self.tools.get(name)
        if not t:
            return False
        return t.high_risk or name in (policy.approval_required_tools or [])

    async def execute(self, ctx, call):
        t = self.tools.get(call.name)
        if not t:
            return json.dumps({"error": f"unknown tool: {call.name}"})
        try:
            out = await t.handler(ctx, **call.args)
            return out if isinstance(out, str) else json.dumps(
                out, ensure_ascii=False)
        except TypeError as e:
            return json.dumps({"error": f"bad arguments: {e}"})
        except Exception as e:
            return json.dumps({"error": str(e)})

# ============ 内置工具 ============

@ToolRegistry.register("run_command",
    "Run a shell command inside the isolated sandbox. CWD is /workspace.",
    {"type": "object", "properties": {
        "command": {"type": "string"},
        "timeout": {"type": "integer", "default": 120}},
     "required": ["command"]})
async def run_command(ctx, command, timeout=120):
    sbx = await SandboxManager.get_or_create(ctx.tenant_id, ctx.session_id)
    return await sbx.exec(command, timeout=timeout)

@ToolRegistry.register("write_file",
    "Write a text file inside the sandbox /workspace.",
    {"type": "object", "properties": {
        "path": {"type": "string"}, "content": {"type": "string"}},
     "required": ["path", "content"]})
async def write_file(ctx, path, content):
    sbx = await SandboxManager.get_or_create(ctx.tenant_id, ctx.session_id)
    await sbx.write_file(path, content)
    return {"ok": True, "path": path}

@ToolRegistry.register("read_file",
    "Read a text file from the sandbox /workspace.",
    {"type": "object", "properties": {"path": {"type": "string"}},
     "required": ["path"]})
async def read_file(ctx, path):
    sbx = await SandboxManager.get_or_create(ctx.tenant_id, ctx.session_id)
    return await sbx.read_file(path)

@ToolRegistry.register("save_artifact",
    "Persist a sandbox file as a downloadable artifact.",
    {"type": "object", "properties": {
        "path": {"type": "string"}, "name": {"type": "string"},
        "mime_type": {"type": "string", "default": "application/octet-stream"}},
     "required": ["path", "name"]})
async def save_artifact(ctx, path, name, mime_type="application/octet-stream"):
    sbx = await SandboxManager.get_or_create(ctx.tenant_id, ctx.session_id)
    data = await sbx.read_file_bytes(path)
    artifact = await ArtifactService.save(
        ctx.db, ctx.tenant_id, ctx.run_id, ctx.session_id,
        name=name, data=data, mime=mime_type)
    await ctx.emit(ctx.db, "artifact.created",
                   {"artifact_id": artifact.id, "name": name,
                    "size": len(data)})
    return {"ok": True, "artifact_id": artifact.id, "size": len(data)}

@ToolRegistry.register("memory_search",
    "Search shared long-term memory visible to this workspace/session.",
    {"type": "object", "properties": {
        "query": {"type": "string"},
        "scope": {"type": "string", "enum": ["user", "workspace", "session", "run"]},
        "limit": {"type": "integer", "default": 8}},
     "required": ["query"]})
async def memory_search(ctx, query, scope=None, limit=8):
    rows = await MemoryService.search(
        ctx.db,
        tenant_id=ctx.tenant_id,
        workspace_id=ctx.workspace_id or None,
        session_id=ctx.session_id,
        run_id=ctx.run_id,
        user_id=ctx.user_id or None,
        query=query,
        scope=scope,
        limit=limit,
    )
    return [{"id": r.id, "scope": r.scope, "kind": r.kind,
             "title": r.title, "content": r.content,
             "confidence": r.confidence} for r in rows]

@ToolRegistry.register("memory_write",
    "Write durable memory for future agent runs.",
    {"type": "object", "properties": {
        "scope": {"type": "string", "enum": ["user", "workspace", "session", "run"]},
        "kind": {"type": "string", "enum": ["fact", "preference", "decision", "task", "summary", "note"]},
        "title": {"type": "string"},
        "content": {"type": "string"},
        "confidence": {"type": "number", "default": 0.6}},
     "required": ["scope", "kind", "title", "content"]})
async def memory_write(ctx, scope, kind, title, content, confidence=0.6):
    item = await MemoryService.write(
        ctx.db,
        tenant_id=ctx.tenant_id,
        workspace_id=ctx.workspace_id or None,
        session_id=ctx.session_id if scope in ("session", "run") else None,
        run_id=ctx.run_id if scope == "run" else None,
        user_id=ctx.user_id or None,
        scope=scope,
        kind=kind,
        title=title,
        content=content,
        source_type="tool",
        source_id=ctx.run_id,
        confidence=confidence,
    )
    await ctx.db.flush()
    return {"ok": True, "id": item.id, "title": item.title}

@ToolRegistry.register("memory_update",
    "Update a visible memory item by id.",
    {"type": "object", "properties": {
        "memory_id": {"type": "string"},
        "title": {"type": "string"},
        "content": {"type": "string"},
        "confidence": {"type": "number"},
        "pinned": {"type": "boolean"}},
     "required": ["memory_id"]})
async def memory_update(ctx, memory_id, title=None, content=None,
                        confidence=None, pinned=None):
    item = await MemoryService.get_visible(
        ctx.db, tenant_id=ctx.tenant_id, memory_id=memory_id,
        workspace_id=ctx.workspace_id or None, session_id=ctx.session_id,
        run_id=ctx.run_id, user_id=ctx.user_id or None)
    if not item:
        return {"ok": False, "error": "memory not found"}
    await MemoryService.update(ctx.db, item, title=title, content=content,
                               confidence=confidence, pinned=pinned)
    return {"ok": True, "id": item.id, "title": item.title}

@ToolRegistry.register("memory_forget",
    "Soft-delete a visible memory item by id.",
    {"type": "object", "properties": {"memory_id": {"type": "string"}},
     "required": ["memory_id"]})
async def memory_forget(ctx, memory_id):
    ok = await MemoryService.forget(
        ctx.db, tenant_id=ctx.tenant_id, memory_id=memory_id,
        workspace_id=ctx.workspace_id or None, session_id=ctx.session_id,
        run_id=ctx.run_id, user_id=ctx.user_id or None)
    return {"ok": ok}

@ToolRegistry.register("web_fetch",
    "Fetch a URL via sandbox and return text content.",
    {"type": "object", "properties": {"url": {"type": "string"}},
     "required": ["url"]})
async def web_fetch(ctx, url):
    sbx = await SandboxManager.get_or_create(ctx.tenant_id, ctx.session_id)
    return await sbx.exec(
        f"curl -sL --max-time 30 {json.dumps(url)} | head -c 20000")

@ToolRegistry.register("browser_visit",
    "Open URL in headless browser, return rendered DOM text.",
    {"type": "object", "properties": {"url": {"type": "string"}},
     "required": ["url"]})
async def browser_visit(ctx, url):
    sbx = await SandboxManager.get_or_create(ctx.tenant_id, ctx.session_id)
    return await sbx.exec(
        f"chromium --headless=new --no-sandbox --disable-gpu "
        f"--dump-dom {json.dumps(url)} 2>/dev/null | head -c 15000",
        timeout=60, container="browser")

@ToolRegistry.register("browser_screenshot",
    "Screenshot a URL; saved to /workspace/artifacts (auto-uploaded).",
    {"type": "object", "properties": {
        "url": {"type": "string"}, "name": {"type": "string"}},
     "required": ["url", "name"]})
async def browser_screenshot(ctx, url, name):
    sbx = await SandboxManager.get_or_create(ctx.tenant_id, ctx.session_id)
    out = await sbx.exec(
        f"mkdir -p /workspace/artifacts && "
        f"chromium --headless=new --no-sandbox --disable-gpu "
        f"--screenshot=/workspace/artifacts/{shlex.quote(name)}.png "
        f"--window-size=1280,800 {json.dumps(url)} 2>&1",
        timeout=60, container="browser")
    return {"ok": True, "saved": f"artifacts/{name}.png", "log": out[-500:]}

@ToolRegistry.register("deploy_to_production",
    "Deploy a service to production. HIGH RISK - requires approval.",
    {"type": "object", "properties": {
        "service": {"type": "string"}, "version": {"type": "string"}},
     "required": ["service", "version"]},
    high_risk=True)
async def deploy_to_production(ctx, service, version):
    return {"ok": True, "service": service, "version": version,
            "message": "deployment triggered"}
