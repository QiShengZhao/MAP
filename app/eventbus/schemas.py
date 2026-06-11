"""Avro Schema：BACKWARD 兼容演进。v2 仅追加带默认值字段。"""

RUN_EVENT_V1 = {
    "type": "record", "name": "RunEvent", "namespace": "agentplatform.events",
    "fields": [
        {"name": "event_id",   "type": "string"},
        {"name": "run_id",     "type": "string"},
        {"name": "tenant_id",  "type": "string"},
        {"name": "seq",        "type": "long"},
        {"name": "event_type", "type": "string"},
        {"name": "ts_ms",      "type": "long"},
        {"name": "payload",    "type": "string"},
    ],
}

RUN_EVENT_V2 = {
    **RUN_EVENT_V1,
    "fields": RUN_EVENT_V1["fields"] + [
        {"name": "workspace_id", "type": ["null", "string"], "default": None},
        {"name": "agent_name",   "type": ["null", "string"], "default": None},
        {"name": "cost_usd",     "type": ["null", "double"], "default": None},
        {"name": "trace_id",     "type": ["null", "string"], "default": None},
    ],
}

RISK_METRIC_V1 = {
    "type": "record", "name": "RiskMetric", "namespace": "agentplatform.risk",
    "fields": [
        {"name": "tenant_id",    "type": "string"},
        {"name": "window_start", "type": "long"},
        {"name": "window_end",   "type": "long"},
        {"name": "metric",       "type": "string"},
        {"name": "value",        "type": "double"},
        {"name": "dims",         "type": {"type": "map", "values": "string"}, "default": {}},
    ],
}

SUBJECTS = {
    "run-events-value":   RUN_EVENT_V2,
    "risk-metrics-value": RISK_METRIC_V1,
}
