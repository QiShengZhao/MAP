import json
from typing import Any

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    ENV: str = "dev"                       # dev | test | staging | production

    # 基础设施
    DATABASE_URL: str = (
        "postgresql+asyncpg://agent_runtime:agent_runtime"
        "@localhost:5432/agent_platform"
    )
    REDIS_URL: str = "redis://localhost:6379/0"

    # JWT（兼容旧字段 + kid 轮换）
    JWT_SECRET: str = "dev-secret"
    JWT_EXPIRE_MINUTES: int = 720
    JWT_KEYS: dict[str, str] = {}
    JWT_ACTIVE_KID: str = "default"
    JWT_ACCESS_TTL_SECONDS: int = 900
    JWT_REFRESH_TTL_SECONDS: int = 86400 * 14

    # 网络安全
    CORS_ORIGINS: list[str] = ["http://localhost:8000", "http://127.0.0.1:8000"]
    TRUSTED_HOSTS: list[str] = ["*"]
    MAX_BODY_BYTES: int = 2 * 1024 * 1024
    RATE_LIMIT_DEFAULT: str = "120/minute"
    RATE_LIMIT_AUTH: str = "10/minute"
    SECURE_COOKIES: bool = False

    # 模型
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    MODEL_PROVIDERS_JSON: str = "[]"
    MODEL_ALIASES_JSON: str = '{"default":"gpt-4o"}'
    MODEL_PRICING_JSON: str = "{}"
    ROUTE_STRATEGY: str = "cost"
    ROUTE_COST_LATENCY_WEIGHT: float = 0.3
    ROUTE_EXPLORATION_RATE: float = 0.05

    # 对象存储
    S3_ENDPOINT: str = "http://localhost:9000"
    S3_ACCESS_KEY: str = "minioadmin"
    S3_SECRET_KEY: str = "minioadmin"
    S3_BUCKET: str = "agent-artifacts"

    # 沙箱
    SANDBOX_BACKEND: str = "local"         # local | docker | k8s
    DOCKER_HOST: str = ""
    SANDBOX_IMAGE: str = "agent-sandbox:latest"
    SANDBOX_TTL_SECONDS: int = 1800
    SANDBOX_CPU_CORES: float = 1.0
    SANDBOX_DISK_MB: int = 512
    SANDBOX_DOCKER_ALLOW_NET: bool = False
    BROWSER_IMAGE: str = "agent-browser:latest"
    ARTIFACT_SIDECAR_IMAGE: str = "agent-sidecar:latest"
    SANDBOX_RUNTIME_CLASS: str = "gvisor"
    SANDBOX_RUNTIME_FALLBACK: bool = True
    SANDBOX_LOCAL_BASE_DIR: str = "/tmp/agent-sandbox"
    SANDBOX_LOCAL_NO_NET: bool = False
    SANDBOX_CPU_SECONDS: int = 60
    SANDBOX_MEM_MB: int = 1024
    SANDBOX_MAX_FILE_MB: int = 100
    KUBE_IN_CLUSTER: bool = False
    INTERNAL_API_URL: str = "http://api:8000"
    INTERNAL_TOKEN: str = "internal-secret"

    # Kafka / Schema Registry
    KAFKA_BOOTSTRAP: str = "kafka:9092"
    KAFKA_RF: int = 1
    KAFKA_SECURITY_PROTOCOL: str = "PLAINTEXT"
    KAFKA_SASL_MECHANISM: str = "SCRAM-SHA-512"
    KAFKA_SASL_USERNAME: str = ""
    KAFKA_SASL_PASSWORD: str = ""
    KAFKA_SSL_CAFILE: str = ""
    KAFKA_TOPIC_RUN_EVENTS: str = "run-events"
    KAFKA_TOPIC_RUN_QUEUE: str = "run-queue"
    KAFKA_TOPIC_USAGE: str = "usage-records"
    KAFKA_TOPIC_DLQ: str = "run-events.dlq"
    DLQ_MAX_RETRIES: int = 3
    EVENT_BUS: str = "redis"                  # kafka | redis
    SCHEMA_REGISTRY_URL: str = "http://schema-registry:8081"
    SCHEMA_REGISTRY_USER: str = ""
    SCHEMA_REGISTRY_PASSWORD: str = ""
    EVENT_SERIALIZATION: str = "json"         # avro | json
    RELAY_MIRROR_JSON: bool = False           # avro 模式下镜像 JSON 到 run-events-json（Flink）
    SCHEMA_COMPAT_MODE: str = "BACKWARD"

    # Stripe
    STRIPE_API_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    STRIPE_PRICE_PRO_BASE: str = ""
    STRIPE_PRICE_TOKENS_TIERED: str = ""
    STRIPE_PRICE_SANDBOX_TIERED: str = ""
    STRIPE_PRICE_BASE_MONTH: str = ""
    STRIPE_PRICE_BASE_YEAR: str = ""
    STRIPE_PRICE_SEAT_MONTH: str = ""
    STRIPE_PRICE_SEAT_YEAR: str = ""
    STRIPE_AUTOMATIC_TAX: bool = False
    STRIPE_SUPPORTED_CURRENCIES: str = "usd,eur,cny"
    STRIPE_DEFAULT_CURRENCY: str = "usd"
    BILLING_TRIAL_DAYS: int = 14
    BILLING_PUBLIC_URL: str = "http://localhost:8000"
    SEATS_INCLUDED_IN_BASE: int = 3

    PLATFORM_DAILY_BUDGET_USD: float = 10000.0
    RISK_DEFAULT_WEBHOOK: str = ""
    RISK_WEBHOOK_SECRET: str = "dev-webhook-secret"
    OTEL_EXPORTER_OTLP_ENDPOINT: str = ""
    MAX_AGENT_TURNS: int = 25
    RUN_LOCK_TTL: int = 900
    PAUSED_RUN_MAX_DAYS: int = 7
    CHECKPOINT_S3_THRESHOLD_MESSAGES: int = 150
    CHECKPOINT_INLINE_TAIL_MESSAGES: int = 20
    FLINK_DEPLOYMENT_TARGET: str = "local"  # local | application
    FLINK_CHECKPOINT_URI: str = "file:///opt/flink/checkpoints"
    PLATFORM_ADMIN_EMAILS: str = ""

    @field_validator("JWT_KEYS", "CORS_ORIGINS", "TRUSTED_HOSTS", mode="before")
    @classmethod
    def _parse_json_list_or_dict(cls, v: Any):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return v
        return v

    @model_validator(mode="after")
    def _production_hardening(self):
        if self.ENV != "production":
            return self
        problems = []
        if self.JWT_SECRET in ("dev-secret", "dev-only-secret") or len(self.JWT_SECRET) < 32:
            problems.append("JWT_SECRET must be >=32 bytes and non-default")
        if "*" in self.CORS_ORIGINS:
            problems.append("CORS_ORIGINS must not contain '*'")
        if "*" in self.TRUSTED_HOSTS:
            problems.append("TRUSTED_HOSTS must be explicit")
        if self.KAFKA_SECURITY_PROTOCOL == "PLAINTEXT":
            problems.append("Kafka must use SASL_SSL in production")
        if self.SANDBOX_BACKEND not in ("k8s", "docker"):
            problems.append("SANDBOX_BACKEND must be 'k8s' or 'docker' in production")
        if self.SANDBOX_BACKEND == "docker" and self.SANDBOX_DOCKER_ALLOW_NET:
            problems.append("SANDBOX_DOCKER_ALLOW_NET must be false in production")
        if not self.INTERNAL_TOKEN or len(self.INTERNAL_TOKEN) < 32:
            problems.append("INTERNAL_TOKEN required (>=32 bytes)")
        if not self.SECURE_COOKIES:
            problems.append("SECURE_COOKIES must be true")
        if len(self.RISK_WEBHOOK_SECRET) < 32:
            problems.append("RISK_WEBHOOK_SECRET must be >=32 bytes in production")
        if problems:
            raise RuntimeError("PRODUCTION CONFIG REFUSED:\n- " + "\n- ".join(problems))
        return self

    class Config:
        env_file = ".env"


settings = Settings()
