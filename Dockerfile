FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .

# The image runs as an unprivileged user that must be able to read the sources
# regardless of the file modes in the build context.
RUN chmod -R a+rX /app && useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8501

# config.toml binds Streamlit to localhost for local development; inside the
# container it must listen on all interfaces so the reverse proxy can reach it.
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]
