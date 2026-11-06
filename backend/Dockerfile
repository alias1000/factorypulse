# Base image can be redirected to an internal registry mirror (air-gapped plants):
#   --build-arg PYTHON_IMAGE=registry.firma.local/python:3.12-slim
ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 MPLCONFIGDIR=/tmp/mpl

# WeasyPrint needs Pango/HarfBuzz; fonts for PDF reports.
# PostgreSQL client tools from the official PGDG repository, pinned to the server major version (16),
# because pg_dump/pg_restore must not be older than the server (and newer dumps may not restore into 16).
ARG PG_MAJOR=16
# PGDG=false installs postgresql-client-${PG_MAJOR} from the distribution's own repositories instead
# (only for base images whose distribution ships that major version, e.g. an Ubuntu 24.04 mirror image).
ARG PGDG=true
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
 && if [ "$PGDG" = "true" ]; then \
      install -d /usr/share/postgresql-common/pgdg \
      && curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc https://www.postgresql.org/media/keys/ACCC4CF8.asc \
      && . /etc/os-release \
      && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
      && apt-get update; \
    fi \
 && apt-get install -y --no-install-recommends postgresql-client-${PG_MAJOR} \
    libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libharfbuzz-subset0 fonts-dejavu-core fonts-inter \
 && rm -rf /var/lib/apt/lists/*
ENV FP_PG_BIN_DIR=/usr/lib/postgresql/${PG_MAJOR}/bin

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini .
COPY alembic ./alembic
COPY app ./app
COPY scripts ./scripts
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN useradd --create-home --uid 10001 factorypulse \
 && mkdir -p /data/evidence/quarantine && chown -R factorypulse:factorypulse /data \
 && chmod +x /usr/local/bin/docker-entrypoint.sh
USER factorypulse

ARG BUILD_SHA=dev
ENV FP_DATA_DIR=/data FP_ENVIRONMENT=production FP_BUILD_SHA=${BUILD_SHA}
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 CMD curl -fsS http://localhost:8000/api/health/ready || exit 1
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
