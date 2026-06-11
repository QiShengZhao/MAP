import asyncio, json, logging, random, time
from dataclasses import dataclass, field
from app.config import settings
from app.infra.redis_client import redis_client
from app.runtime.model_provider import ModelResult, ToolCall
from app.runtime.model_stats import SharedProviderStats

log = logging.getLogger("model-router")

class AllProvidersFailed(Exception): pass

class CircuitBreaker:
    """closed -> open(冷却) -> half_open -> closed"""
    def __init__(self, fail_threshold=4, cooldown=30.0):
        self.fail_threshold, self.cooldown = fail_threshold, cooldown
        self.failures, self.opened_at, self.state = 0, 0.0, "closed"

    def allow(self):
        if self.state == "open":
            if time.monotonic() - self.opened_at >= self.cooldown:
                self.state = "half_open"
                return True
            return False
        return True

    def record_success(self):
        self.failures, self.state = 0, "closed"

    def record_failure(self):
        self.failures += 1
        if self.state == "half_open" or self.failures >= self.fail_threshold:
            self.state, self.opened_at = "open", time.monotonic()

@dataclass
class ProviderConfig:
    name: str
    type: str
    api_key: str
    base_url: str = ""
    models: list = field(default_factory=list)
    model_map: dict = field(default_factory=dict)
    priority: int = 1
    weight: int = 10
    timeout: float = 120.0

class OpenAIAdapter:
    """OpenAI / Azure / vLLM 等 OpenAI 兼容端点"""
    def __init__(self, cfg: ProviderConfig):
        from openai import AsyncOpenAI
        self.cfg = cfg
        self.breaker = CircuitBreaker()
        self.client = AsyncOpenAI(api_key=cfg.api_key,
                                  base_url=cfg.base_url or None,
                                  timeout=cfg.timeout)

    def supports(self, model):
        return model in self.cfg.models or model in self.cfg.model_map

    def real_model(self, model):
        return self.cfg.model_map.get(model, model)

    async def chat(self, model, messages, tools, on_delta) -> ModelResult:
        kwargs = dict(model=self.real_model(model), messages=messages,
                      stream=True, stream_options={"include_usage": True})
        if tools:
            kwargs["tools"] = tools
        stream = await self.client.chat.completions.create(**kwargs)
        result, partial = ModelResult(), {}
        async for chunk in stream:
            if chunk.usage:
                result.usage = {"prompt": chunk.usage.prompt_tokens,
                                "completion": chunk.usage.completion_tokens}
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                result.content += delta.content
                if on_delta:
                    await on_delta(delta.content)
            for tc in delta.tool_calls or []:
                p = partial.setdefault(tc.index,
                                       {"id": tc.id or "", "name": "", "args": ""})
                if tc.id: p["id"] = tc.id
                if tc.function and tc.function.name:
                    p["name"] += tc.function.name
                if tc.function and tc.function.arguments:
                    p["args"] += tc.function.arguments
        for p in partial.values():
            try:
                args = json.loads(p["args"]) if p["args"] else {}
            except json.JSONDecodeError:
                args = {"_raw": p["args"]}
            result.tool_calls.append(ToolCall(p["id"], p["name"], args))
        return result

class AnthropicAdapter:
    def __init__(self, cfg: ProviderConfig):
        from anthropic import AsyncAnthropic
        self.cfg = cfg
        self.breaker = CircuitBreaker()
        self.client = AsyncAnthropic(api_key=cfg.api_key, timeout=cfg.timeout)

    def supports(self, model):
        return model in self.cfg.models or model in self.cfg.model_map

    def real_model(self, model):
        return self.cfg.model_map.get(model, model)

    @staticmethod
    def _convert(messages, tools):
        system, out = "", []
        for m in messages:
            if m["role"] == "system":
                system += m["content"] + "\n"
            elif m["role"] == "tool":
                out.append({"role": "user", "content": [{
                    "type": "tool_result", "tool_use_id": m["tool_call_id"],
                    "content": str(m["content"])}]})
            elif m["role"] == "assistant" and m.get("tool_calls"):
                blocks = []
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                for tc in m["tool_calls"]:
                    blocks.append({"type": "tool_use", "id": tc["id"],
                                   "name": tc["function"]["name"],
                                   "input": json.loads(
                                       tc["function"]["arguments"] or "{}")})
                out.append({"role": "assistant", "content": blocks})
            else:
                out.append({"role": m["role"], "content": m["content"] or ""})
        a_tools = [{"name": t["function"]["name"],
                    "description": t["function"].get("description", ""),
                    "input_schema": t["function"]["parameters"]}
                   for t in (tools or [])]
        return system.strip(), out, a_tools

    async def chat(self, model, messages, tools, on_delta) -> ModelResult:
        system, msgs, a_tools = self._convert(messages, tools)
        result = ModelResult()
        async with self.client.messages.stream(
                model=self.real_model(model), max_tokens=8192,
                system=system or None, messages=msgs,
                tools=a_tools or None) as stream:
            async for event in stream:
                if event.type == "content_block_delta" and \
                        getattr(event.delta, "type", "") == "text_delta":
                    result.content += event.delta.text
                    if on_delta:
                        await on_delta(event.delta.text)
            final = await stream.get_final_message()
        result.usage = {"prompt": final.usage.input_tokens,
                        "completion": final.usage.output_tokens}
        for block in final.content:
            if block.type == "tool_use":
                result.tool_calls.append(
                    ToolCall(block.id, block.name, block.input or {}))
        return result

ADAPTERS = {"openai": OpenAIAdapter, "openai_compatible": OpenAIAdapter,
            "anthropic": AnthropicAdapter}

class ModelRouter:
    _instance = None

    def __init__(self):
        self.adapters = []
        for raw in json.loads(settings.MODEL_PROVIDERS_JSON or "[]"):
            cfg = ProviderConfig(**raw)
            self.adapters.append(ADAPTERS[cfg.type](cfg))
        if not self.adapters:
            self.adapters.append(OpenAIAdapter(ProviderConfig(
                name="openai-default", type="openai",
                api_key=settings.OPENAI_API_KEY,
                base_url=settings.OPENAI_BASE_URL,
                models=["gpt-4o", "gpt-4o-mini"])))
        self.aliases = json.loads(settings.MODEL_ALIASES_JSON or "{}")
        self.pricing = json.loads(settings.MODEL_PRICING_JSON or "{}")
        self.stats = {a.cfg.name: SharedProviderStats(a.cfg.name)
                      for a in self.adapters}

    @classmethod
    def get(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def estimate_cost_usd(self, provider, model, est_prompt, est_completion):
        price = (self.pricing.get(f"{provider}/{model}")
                 or self.pricing.get(model)
                 or {"prompt": 5.0, "completion": 15.0})
        return (est_prompt * price["prompt"]
                + est_completion * price["completion"]) / 1_000_000

    @staticmethod
    def estimate_tokens(messages):
        chars = sum(len(str(m.get("content") or "")) for m in messages)
        prompt = max(64, chars // 4)
        return prompt, max(128, int(prompt * 0.4))

    async def effective_score(self, adapter, model, est_p, est_c):
        """期望成本/(1-失败率) + 延迟惩罚，越低越优"""
        name = adapter.cfg.name
        cost = self.estimate_cost_usd(name, model, est_p, est_c)
        st = await self.stats[name].get()
        p = min(st["fail_rate"], 0.95)
        latency_penalty = (st["latency_ms"] / 1000.0) \
            * settings.ROUTE_COST_LATENCY_WEIGHT * max(cost, 0.0001)
        return cost / (1 - p) + latency_penalty

    async def _candidates(self, model, messages):
        ok = [a for a in self.adapters
              if a.supports(model) and a.breaker.allow()]
        if not ok:
            return []
        if settings.ROUTE_STRATEGY != "cost":
            return sorted(ok, key=lambda a: a.cfg.priority)
        est_p, est_c = self.estimate_tokens(messages)
        scored = []
        for a in ok:
            scored.append((await self.effective_score(a, model, est_p, est_c), a))
        scored.sort(key=lambda x: x[0])
        result = [a for _, a in scored]
        # ε-greedy 探索
        if len(result) > 1 and random.random() < settings.ROUTE_EXPLORATION_RATE:
            i = random.randint(1, len(result) - 1)
            result[0], result[i] = result[i], result[0]
        return result

    async def chat(self, model, messages, tools, on_delta=None,
                   on_provider=None) -> ModelResult:
        model = self.aliases.get(model, model)
        candidates = await self._candidates(model, messages) or \
            [a for a in self.adapters if a.supports(model)]
        errors = []
        for adapter in candidates:
            name = adapter.cfg.name
            start = time.monotonic()
            first_ms = [0.0]

            async def timed_delta(text):
                if first_ms[0] == 0:
                    first_ms[0] = (time.monotonic() - start) * 1000
                if on_delta:
                    await on_delta(text)

            try:
                if on_provider:
                    est = self.estimate_cost_usd(
                        name, model, *self.estimate_tokens(messages))
                    await on_provider(name, est)
                result = await asyncio.wait_for(
                    adapter.chat(model, messages, tools, timed_delta),
                    timeout=adapter.cfg.timeout + 10)
                adapter.breaker.record_success()
                await self.stats[name].record(True, first_ms[0])
                result.provider = name
                result.cost_usd = self.estimate_cost_usd(
                    name, model, result.usage.get("prompt", 0),
                    result.usage.get("completion", 0))
                return result
            except Exception as e:
                adapter.breaker.record_failure()
                await self.stats[name].record(False)
                errors.append(f"{name}: {type(e).__name__}: {e}")
                log.warning("provider %s failed: %s, failover", name, e)
        raise AllProvidersFailed("; ".join(errors))

    async def routing_table(self, model):
        est_p, est_c = 1000, 400
        out = []
        for a in self.adapters:
            if not a.supports(model):
                continue
            st = await self.stats[a.cfg.name].get()
            out.append({"provider": a.cfg.name, "breaker": a.breaker.state,
                        "score": round(await self.effective_score(
                            a, model, est_p, est_c), 6),
                        **st})
        return sorted(out, key=lambda x: x["score"])