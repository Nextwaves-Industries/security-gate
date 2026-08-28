# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e
FROM python:3.11-slim-bookworm@sha256:0bee7276f83efd4a1ee05bbbf4281d95ed28e079220a9457f25a93e3f1e3c31b AS dependencies

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /build
COPY requirements-headless.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    export PIP_NO_CACHE_DIR=0 \
    && python -m venv /opt/venv \
    && /opt/venv/bin/pip install pip==26.2.1 \
    && /opt/venv/bin/pip install -r requirements-headless.txt \
    && /opt/venv/bin/pip check \
    && rm -f /opt/venv/bin/pip /opt/venv/bin/pip3 /opt/venv/bin/pip3.11 \
    && rm -rf /opt/venv/lib/python3.11/site-packages/pip \
      /opt/venv/lib/python3.11/site-packages/pip-*.dist-info

FROM python:3.11-slim-bookworm@sha256:0bee7276f83efd4a1ee05bbbf4281d95ed28e079220a9457f25a93e3f1e3c31b AS runtime

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt/nextwaves \
    RFID_PORTAL_DATA_DIR=/var/lib/nextwaves

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && rm -rf /usr/local/lib/python3.11/site-packages/pip \
      /usr/local/lib/python3.11/site-packages/pip-*.dist-info \
      /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.11 \
    && groupadd --gid 10001 nextwaves \
    && useradd --uid 10001 --gid 10001 --home-dir /nonexistent \
      --shell /usr/sbin/nologin nextwaves \
    && install -d -o 10001 -g 10001 /var/lib/nextwaves /opt/nextwaves

COPY --from=dependencies /opt/venv /opt/venv
COPY --chown=10001:10001 runtime/ /opt/nextwaves/

USER 10001:10001
WORKDIR /opt/nextwaves
EXPOSE 8443 50051
VOLUME ["/var/lib/nextwaves"]
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import ssl,urllib.request; c=ssl._create_unverified_context(); urllib.request.urlopen('https://127.0.0.1:8443/healthz',context=c,timeout=2).read()"
ENTRYPOINT ["python", "-m", "gate_service.main"]
