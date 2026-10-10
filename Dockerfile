FROM python:3.14-slim
WORKDIR /app
COPY pyproject.toml uv.lock ./
ARG INSTALL_KNOWLEDGE=false
RUN pip install --no-cache-dir uv && if [ "$INSTALL_KNOWLEDGE" = "true" ]; then uv sync --frozen --no-dev --extra knowledge; else uv sync --frozen --no-dev; fi
RUN mkdir /models /uploads && chown 1000:1000 /models /uploads
ENV PATH="/app/.venv/bin:$PATH" PYTHONPATH=/app/backend PYTHONUNBUFFERED=1 HF_HOME=/models
COPY backend ./backend
COPY alembic.ini ./
COPY migrations ./migrations
COPY data ./data
USER 1000
