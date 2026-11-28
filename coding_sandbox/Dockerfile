# Only the reviewed requirements and trusted workspace helper are in the build context.
FROM node:22-bookworm AS node
FROM python:3.12-bookworm
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY requirements.txt /opt/coding/requirements.txt
RUN python -m pip install --no-cache-dir -r /opt/coding/requirements.txt
COPY policy.py worker.py /opt/coding/
RUN chmod -R a-w /opt/coding && mkdir -p /workspace && chown 10001:10001 /workspace
USER 10001:10001
WORKDIR /workspace
ENV HOME=/tmp PYTHONDONTWRITEBYTECODE=1 MY_AI_AGENT_DATA_DIR=/tmp/agent-data
ENTRYPOINT ["/bin/sleep", "infinity"]
