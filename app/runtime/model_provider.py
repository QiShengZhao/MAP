import json
from dataclasses import dataclass, field

@dataclass
class ToolCall:
    id: str
    name: str
    args: dict

@dataclass
class ModelResult:
    content: str = ""
    tool_calls: list = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    provider: str = ""
    cost_usd: float = 0.0

    def as_message(self):
        msg = {"role": "assistant", "content": self.content or None}
        if self.tool_calls:
            msg["tool_calls"] = [
                {"id": c.id, "type": "function",
                 "function": {"name": c.name,
                              "arguments": json.dumps(c.args, ensure_ascii=False)}}
                for c in self.tool_calls]
        return msg