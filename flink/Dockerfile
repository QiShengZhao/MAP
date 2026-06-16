# PyFlink 运行镜像：官方 Flink + Python 环境 + 作业代码
FROM flink:1.18.1-scala_2.12-java11

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip curl && \
    ln -sf /usr/bin/python3 /usr/bin/python && \
    rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir apache-flink==1.18.1

RUN curl -fsSL -o /opt/flink/lib/flink-sql-connector-kafka-3.1.0-1.18.jar \
    https://repo1.maven.org/maven2/org/apache/flink/flink-sql-connector-kafka/3.1.0-1.18/flink-sql-connector-kafka-3.1.0-1.18.jar

RUN curl -fsSL -o /opt/flink/lib/flink-s3-fs-hadoop-1.18.1.jar \
    https://repo1.maven.org/maven2/org/apache/flink/flink-s3-fs-hadoop/1.18.1/flink-s3-fs-hadoop-1.18.1.jar

COPY flink/configure-flink.sh /opt/flink/configure-flink.sh
RUN chmod +x /opt/flink/configure-flink.sh

COPY flink/risk_job.py /opt/jobs/risk_job.py
