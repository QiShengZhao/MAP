FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash curl git jq nodejs npm && rm -rf /var/lib/apt/lists/* \
 && useradd -m -u 1000 agent && mkdir /workspace && chown agent /workspace
USER agent
WORKDIR /workspace
