# Pinned to 3.12-slim (stable/LTS-track) rather than the 3.14 used in local
# dev, for predictable prebuilt-wheel availability on a minimal base image.
FROM python:3.12-slim

WORKDIR /app

# System deps: none required — neo4j driver and pydantic-core ship prebuilt
# manylinux wheels, so no compiler toolchain is needed here.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Railway injects $PORT at runtime and expects the app to bind 0.0.0.0:$PORT;
# shell form so the variable expands (exec/JSON-array form would not).
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
