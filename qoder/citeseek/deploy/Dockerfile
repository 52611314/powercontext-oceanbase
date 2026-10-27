FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CITESEK_MODEL_DIR=/app/backend/models/bge-small-zh-v1.5 \
    CITESEK_DATA_DIR=/data \
    CITESEK_WEB_DIR=/app/frontend/dist

ARG PIP_INDEX_URL=https://pypi.org/simple

WORKDIR /app

COPY backend/pyproject.toml /app/backend/pyproject.toml
COPY backend/src /app/backend/src
RUN pip install --no-cache-dir -i "$PIP_INDEX_URL" /app/backend && rm -rf /root/.cache/pip

COPY backend/models /app/backend/models
# Pre-built by `make web`; committed so judges do not need Node to start the stack.
COPY frontend/dist /app/frontend/dist

EXPOSE 8000

CMD ["uvicorn", "citeseek.main:app", "--host", "0.0.0.0", "--port", "8000"]
