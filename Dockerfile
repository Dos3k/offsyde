FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    OFFSYDE_DB_URL=sqlite:////data/offsyde.db \
    OFFSYDE_RAW_DIR=/data/raw

WORKDIR /app

# Install dependencies first so this layer is cached until pyproject.toml changes.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[postgres]"

COPY benchmarks ./benchmarks

# Run as an unprivileged user; /data holds the database and the raw download cache.
RUN useradd --create-home app && mkdir /data && chown app /data
USER app
VOLUME /data

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request as u; u.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "offsyde.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
