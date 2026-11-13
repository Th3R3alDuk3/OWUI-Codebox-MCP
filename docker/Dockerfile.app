FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim

# MIME types for output file uploads.
RUN apt-get update \
 && apt-get install --yes --no-install-recommends media-types \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .

EXPOSE 8000

CMD ["uv", "run", "--no-sync", "python", "main.py"]
