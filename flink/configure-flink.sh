#!/bin/bash
# Append checkpoint backend settings from environment before starting Flink.
set -euo pipefail
URI="${FLINK_CHECKPOINT_URI:-file:///opt/flink/checkpoints}"
cat >> /opt/flink/conf/flink-conf.yaml <<EOF

# injected by configure-flink.sh
state.backend: rocksdb
state.checkpoints.dir: ${URI}
execution.checkpointing.interval: 30s
EOF
exec /docker-entrypoint.sh "$@"
