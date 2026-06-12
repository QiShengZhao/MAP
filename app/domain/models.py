import uuid, enum
from datetime import datetime
from sqlalchemy import (String, ForeignKey, JSON, Enum, Index, Integer,
                        DateTime, UniqueConstraint, Text, BigInteger)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

def uid(): return str(uuid.uuid4())
def now(): return datetime.utcnow()

class Base(DeclarativeBase): pass

# ---------- 租户 / 用户 ----------
class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(128))
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    plan: Mapped[str] = mapped_column(String(32), default="free")
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class TenantMember(Base):
    __tablename__ = "tenant_members"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(32), default="member")

class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class WorkspaceMember(Base):
    __tablename__ = "workspace_members"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(32), default="member")

# ---------- Session / Message / Run ----------
class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    user_id: Mapped[str] = mapped_column(String(36))
    title: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(32), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class Message(Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class RunStatus(str, enum.Enum):
    queued = "queued"; running = "running"
    awaiting_approval = "awaiting_approval"
    paused = "paused"
    completed = "completed"; failed = "failed"; cancelled = "cancelled"

class Run(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    user_id: Mapped[str] = mapped_column(String(36))
    status: Mapped[RunStatus] = mapped_column(Enum(RunStatus), default=RunStatus.queued)
    status_reason: Mapped[str] = mapped_column(Text, default="")
    agent_config: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    usage: Mapped[dict] = mapped_column(JSON, default=dict)
    trace_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

class RunEvent(Base):
    __tablename__ = "run_events"
    __table_args__ = (Index("ix_run_events_run_seq", "run_id", "seq", unique=True),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class Artifact(Base):
    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(512))
    mime_type: Mapped[str] = mapped_column(String(128), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

# ---------- 审批 ----------
class ApprovalStatus(str, enum.Enum):
    pending = "pending"; approved = "approved"
    rejected = "rejected"; expired = "expired"

class ApprovalRequest(Base):
    __tablename__ = "approval_requests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    tool_call_id: Mapped[str] = mapped_column(String(64))
    tool_name: Mapped[str] = mapped_column(String(128))
    tool_args: Mapped[dict] = mapped_column(JSON)
    status: Mapped[ApprovalStatus] = mapped_column(
        Enum(ApprovalStatus), default=ApprovalStatus.pending)
    requested_by: Mapped[str] = mapped_column(String(36))
    decided_by: Mapped[str] = mapped_column(String(36), default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

# ---------- 计量 / 策略 / Skill ----------
class UsageRecord(Base):
    __tablename__ = "usage_records"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    workspace_id: Mapped[str] = mapped_column(String(36), index=True, default="")
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    quantity: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class TenantPolicy(Base):
    __tablename__ = "tenant_policies"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), unique=True)
    allowed_tools: Mapped[list] = mapped_column(JSON, default=list)
    approval_required_tools: Mapped[list] = mapped_column(JSON, default=list)
    blocked_domains: Mapped[list] = mapped_column(JSON, default=list)
    max_concurrent_runs: Mapped[int] = mapped_column(Integer, default=5)
    max_tokens_per_day: Mapped[int] = mapped_column(BigInteger, default=1_000_000)
    max_cost_per_day_usd: Mapped[float] = mapped_column(default=50.0)
    max_cost_per_run_usd: Mapped[float] = mapped_column(default=2.0)

class Skill(Base):
    __tablename__ = "skills"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    instructions: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(default=True)

# ---------- 多 Agent / 状态 / 沙箱会话 ----------
class AgentDef(Base):
    __tablename__ = "agent_defs"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text, default="")
    instructions: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(64), default="gpt-4o")
    tools: Mapped[list] = mapped_column(JSON, default=list)
    handoffs: Mapped[list] = mapped_column(JSON, default=list)
    as_tool: Mapped[bool] = mapped_column(default=False)
    is_default: Mapped[bool] = mapped_column(default=False)
    enabled: Mapped[bool] = mapped_column(default=True)

class RunState(Base):
    __tablename__ = "run_states"
    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    current_agent: Mapped[str] = mapped_column(String(64), default="default")
    turn: Mapped[int] = mapped_column(Integer, default=0)
    history: Mapped[list] = mapped_column(JSON, default=list)
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=0)
    pending_tool_call: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)

class SandboxSession(Base):
    __tablename__ = "sandbox_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    session_id: Mapped[str] = mapped_column(String(36), unique=True)
    namespace: Mapped[str] = mapped_column(String(64))
    pod_name: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="running")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    last_used_at: Mapped[datetime] = mapped_column(DateTime, default=now)

# ---------- 计费 ----------
class BillingAccount(Base):
    __tablename__ = "billing_accounts"
    tenant_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    stripe_customer_id: Mapped[str] = mapped_column(String(64), default="")
    base_subscription_id: Mapped[str] = mapped_column(String(64), default="")
    usage_subscription_id: Mapped[str] = mapped_column(String(64), default="")
    si_tokens: Mapped[str] = mapped_column(String(64), default="")
    si_sandbox: Mapped[str] = mapped_column(String(64), default="")
    si_seats: Mapped[str] = mapped_column(String(64), default="")
    billing_interval: Mapped[str] = mapped_column(String(16), default="month")
    seats_purchased: Mapped[int] = mapped_column(Integer, default=0)
    plan: Mapped[str] = mapped_column(String(32), default="free")
    status: Mapped[str] = mapped_column(String(32), default="none")
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

class UsageReportCursor(Base):
    __tablename__ = "usage_report_cursors"
    tenant_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    last_reported_at: Mapped[datetime] = mapped_column(DateTime, default=now)

# ---------- 风控 ----------
class RiskRule(Base):
    __tablename__ = "risk_rules"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(default=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    condition: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(String(32), default="flag")
    action_params: Mapped[dict] = mapped_column(JSON, default=dict)
    severity: Mapped[str] = mapped_column(String(16), default="warning")
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=600)
    actions: Mapped[list] = mapped_column(JSON, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_by: Mapped[str] = mapped_column(String(36), default="")
    created_by: Mapped[str] = mapped_column(String(36), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)

    @property
    def expression(self) -> str:
        return self.condition

class RiskIncident(Base):
    __tablename__ = "risk_incidents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    rule_id: Mapped[str] = mapped_column(String(36), default="")
    rule_name: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(16), default="warning")
    action: Mapped[str] = mapped_column(String(32), default="")
    action_executed: Mapped[bool] = mapped_column(default=False)
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    actions_taken: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)