FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium python3 bash && rm -rf /var/lib/apt/lists/* \
 && useradd -m -u 1000 agent && mkdir -p /workspace && chown agent /workspace
USER agent
WORKDIR /workspace
CMD ["sleep", "infinity"]
