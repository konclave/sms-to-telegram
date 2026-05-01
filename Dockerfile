# syntax=docker/dockerfile:1.7
FROM python:3.14-slim-bookworm AS app-builder

ENV UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:${PATH}"

RUN apt-get update && \
    apt-get install --no-install-recommends -y git ca-certificates && \
    python -m pip install --no-cache-dir "setuptools>=80" uv && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
COPY sms_forwarder ./sms_forwarder
RUN --mount=type=bind,source=.git,target=/app/.git \
    uv sync --frozen --no-dev --no-install-project && \
    uv pip install --python /app/.venv/bin/python "setuptools==82.0.1" "setuptools-scm[simple]==10.0.5" && \
    uv pip install --python /app/.venv/bin/python --no-deps --no-build-isolation .

FROM python:3.14-slim-bookworm

ENV PIN=0000 \
    LC_ALL=en_US.UTF-8 \
    LANG=en_US.UTF-8 \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:${PATH}"

RUN apt-get update && \
    apt-get install --no-install-recommends -y \
        gammu-smsd \
        gettext \
        locales \
        ca-certificates && \
    localedef -i en_US -c -f UTF-8 -A /usr/share/locale/locale.alias en_US.UTF-8 && \
    python -m pip install --no-cache-dir uv && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=app-builder /app/.venv /app/.venv

COPY gammurc /etc/gammurc
COPY entrypoint.sh /usr/local/bin/entrypoint.sh

RUN mkdir -p \
        /var/log/smsd \
        /var/spool/gammu/inbox \
        /var/spool/gammu/outbox \
        /var/spool/gammu/sent \
        /var/spool/gammu/error \
        /var/spool/sms-forwarder/pending \
        /var/spool/sms-forwarder/processing \
        /var/spool/sms-forwarder/sent \
        /var/spool/sms-forwarder/failed && \
    chmod +x /usr/local/bin/entrypoint.sh

HEALTHCHECK CMD sms-forwarder-healthcheck
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
