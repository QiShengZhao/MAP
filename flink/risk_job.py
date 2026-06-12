"""Flink 实时风控指标聚合作业。

run-events ──keyBy(tenant_id)──> 1min 滚动窗口(允许 10s 乱序) ──> risk-metrics

提交:
  flink run -py flink/risk_job.py \
    -pyreq requirements-flink.txt \
    --kafka-bootstrap kafka:9092
"""
import argparse
import json
import logging

from pyflink.common import Duration, Time, Types, WatermarkStrategy
from pyflink.common.serialization import SimpleStringSchema
from pyflink.datastream import StreamExecutionEnvironment
from pyflink.datastream.connectors.kafka import (
    DeliveryGuarantee, KafkaOffsetsInitializer, KafkaRecordSerializationSchema,
    KafkaSink, KafkaSource)
from pyflink.datastream.functions import ProcessWindowFunction
from pyflink.datastream.window import TumblingEventTimeWindows

log = logging.getLogger("flink.risk")

FAIL_EVENTS = {"tool.failed", "run.failed", "guardrail.blocked", "budget.exceeded"}


def parse_event(raw: str):
    try:
        ev = json.loads(raw)
        payload = ev.get("payload")
        p = json.loads(payload) if isinstance(payload, str) else (payload or {})
        return (
            ev["tenant_id"],
            int(ev["ts_ms"]),
            ev["event_type"],
            p.get("tool_name") or "",
            float(ev.get("cost_usd") or p.get("cost_usd") or 0.0),
            int(p.get("total_tokens") or 0),
        )
    except Exception:
        return None


class AggregateWindow(ProcessWindowFunction):
    def process(self, key, ctx: ProcessWindowFunction.Context, elements):
        tool_calls = errors = sandbox_execs = approval_denied = total = 0
        cost = 0.0
        tokens = 0
        tools: set[str] = set()

        for (_tid, _ts, etype, tool, c, tok) in elements:
            total += 1
            cost += c
            tokens += tok
            if etype == "tool.call":
                tool_calls += 1
                if tool:
                    tools.add(tool)
            if etype in FAIL_EVENTS:
                errors += 1
            if etype == "sandbox.exec":
                sandbox_execs += 1
            if etype == "approval.rejected":
                approval_denied += 1

        ws, we = ctx.window().start, ctx.window().end
        metrics = {
            "tool_call_rate": float(tool_calls),
            "error_rate": errors / total if total else 0.0,
            "cost_per_min": cost,
            "distinct_tools": float(len(tools)),
            "sandbox_exec_rate": float(sandbox_execs),
            "approval_denied": float(approval_denied),
            "token_rate": float(tokens),
        }
        for name, value in metrics.items():
            yield json.dumps({
                "tenant_id": key,
                "window_start": ws,
                "window_end": we,
                "metric": name,
                "value": value,
                "dims": {},
            })


def build_job(bootstrap: str, source_topic: str = "run-events-json",
              group: str = "flink-risk-agg"):
    env = StreamExecutionEnvironment.get_execution_environment()
    env.set_parallelism(2)
    env.enable_checkpointing(30_000)

    source = (KafkaSource.builder()
              .set_bootstrap_servers(bootstrap)
              .set_topics(source_topic)
              .set_group_id(group)
              .set_starting_offsets(KafkaOffsetsInitializer.latest())
              .set_value_only_deserializer(SimpleStringSchema())
              .build())

    sink = (KafkaSink.builder()
            .set_bootstrap_servers(bootstrap)
            .set_record_serializer(
                KafkaRecordSerializationSchema.builder()
                .set_topic("risk-metrics")
                .set_value_serialization_schema(SimpleStringSchema())
                .build())
            .set_delivery_guarantee(DeliveryGuarantee.AT_LEAST_ONCE)
            .build())

    wm = (WatermarkStrategy
          .for_bounded_out_of_orderness(Duration.of_seconds(10))
          .with_timestamp_assigner(lambda e, _ts: e[1])
          .with_idleness(Duration.of_seconds(30)))

    (env.from_source(source, WatermarkStrategy.no_watermarks(), source_topic)
        .map(parse_event)
        .filter(lambda x: x is not None)
        .assign_timestamps_and_watermarks(wm)
        .key_by(lambda x: x[0], key_type=Types.STRING())
        .window(TumblingEventTimeWindows.of(Time.minutes(1)))
        .process(AggregateWindow(), output_type=Types.STRING())
        .sink_to(sink))

    return env


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser()
    ap.add_argument("--kafka-bootstrap", default="kafka:9092")
    ap.add_argument("--source-topic", default="run-events-json")
    args, _ = ap.parse_known_args()
    build_job(args.kafka_bootstrap, args.source_topic).execute("risk-metric-aggregation")
