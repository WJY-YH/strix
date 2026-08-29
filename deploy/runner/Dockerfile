FROM golang:1.25-bookworm AS go-toolchain

FROM python:3.12-slim

COPY --from=go-toolchain /usr/local/go /usr/local/go
ENV PATH="/usr/local/go/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl docker.io git \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin strix

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir . \
    && mkdir -p /data \
    && chown -R strix:strix /app /data

USER strix
CMD ["python", "-m", "deploy.runner.main"]
