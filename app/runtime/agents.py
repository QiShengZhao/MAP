from dataclasses import dataclass, field
from sqlalchemy import select
from app.domain.models import AgentDef

HANDOFF_PREFIX = "transfer_to_"
AGENT_TOOL_PREFIX = "ask_agent_"

@dataclass
class ResolvedAgent:
    name: str
    instructions: str
    model: str
    tool_names: list = field(default_factory=list)
    handoffs: list = field(default_factory=list)

DEFAULT_AGENT = ResolvedAgent(name="default",
    instructions="You are a general-purpose AI agent.", model="gpt-4o")

class AgentRegistry:
    def __init__(self, agents, agent_tools):
        self.agents, self.agent_tools = agents, agent_tools

    @classmethod
    async def load(cls, db, tenant_id):
        rows = (await db.execute(select(AgentDef).where(
            AgentDef.tenant_id == tenant_id,
            AgentDef.enabled == True))).scalars().all()
        agents, agent_tools, default_name = {}, [], None
        for a in rows:
            agents[a.name] = ResolvedAgent(
                name=a.name, instructions=a.instructions, model=a.model,
                tool_names=a.tools or [], handoffs=a.handoffs or [])
            if a.as_tool:
                agent_tools.append(a.name)
            if a.is_default:
                default_name = a.name
        if not agents:
            agents["default"] = DEFAULT_AGENT
            default_name = "default"
        reg = cls(agents, agent_tools)
        reg.default_name = default_name or next(iter(agents))
        return reg

    def get(self, name):
        return self.agents.get(name) or self.agents[self.default_name]

    def virtual_tool_schemas(self, current):
        schemas = []
        for target in current.handoffs:
            if target not in self.agents:
                continue
            t = self.agents[target]
            schemas.append({"type": "function", "function": {
                "name": f"{HANDOFF_PREFIX}{target}",
                "description": f"Hand off to agent '{target}': "
                               f"{t.instructions[:200]}",
                "parameters": {"type": "object", "properties": {
                    "reason": {"type": "string"}}, "required": ["reason"]}}})
        for name in self.agent_tools:
            if name == current.name:
                continue
            t = self.agents[name]
            schemas.append({"type": "function", "function": {
                "name": f"{AGENT_TOOL_PREFIX}{name}",
                "description": f"Ask agent '{name}': {t.instructions[:200]}",
                "parameters": {"type": "object", "properties": {
                    "question": {"type": "string"}},
                    "required": ["question"]}}})
        return schemas

    @staticmethod
    def parse_handoff(tool_name):
        return tool_name[len(HANDOFF_PREFIX):] \
            if tool_name.startswith(HANDOFF_PREFIX) else None

    @staticmethod
    def parse_agent_tool(tool_name):
        return tool_name[len(AGENT_TOOL_PREFIX):] \
            if tool_name.startswith(AGENT_TOOL_PREFIX) else None