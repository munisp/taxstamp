# syntax=docker/dockerfile:1.7
FROM python:3.12.8-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

FROM base AS build
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --no-deps .

FROM base AS runtime
RUN groupadd --system --gid 10001 taxstamp \
 && useradd --system --uid 10001 --gid taxstamp --home /app taxstamp
COPY --from=build /opt/venv /opt/venv
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini
ENV PATH="/opt/venv/bin:$PATH"
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).status == 200 else 1)"
ENTRYPOINT ["uvicorn", "taxstamp.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
