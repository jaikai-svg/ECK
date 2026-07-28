FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --gid 10001 eck \
    && useradd --uid 10001 --gid eck --create-home --shell /usr/sbin/nologin eck

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY config ./config

RUN python -m pip install --upgrade pip \
    && python -m pip install .

RUN mkdir -p /app/data /app/workspace \
    && chown -R eck:eck /app/data /app/workspace

USER eck

EXPOSE 8420
VOLUME ["/app/data", "/app/workspace"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8420/health', timeout=3)"

CMD ["eck", "serve", "--host", "0.0.0.0", "--port", "8420"]

