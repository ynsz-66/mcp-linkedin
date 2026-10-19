FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY server.py ./

RUN useradd --create-home app
USER app
EXPOSE 8080

CMD ["uv", "run", "--no-sync", "server.py"]
