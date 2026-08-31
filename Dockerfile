FROM node:22-bookworm-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --no-install-project

COPY . ./
RUN uv sync --locked --no-dev

ENV PATH="/app/.venv/bin:${PATH}"
ENV LITREV_DATA_DIR=/data

RUN mkdir /data

EXPOSE 1420 8765

CMD ["npm", "run", "dev:docker"]
