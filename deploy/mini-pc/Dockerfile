# iRacing Coach service image for the mini PC (architecture.md section 10).
# Built by the home builder from the committed tree (context: repository root,
# filtered by the root .dockerignore). No build arguments: the release tool passes none.

# Stage 1: coach-server. The image's toolchain is the pinned 1.99.0; the
# repository's rust-toolchain.toml is kept out of the context (.dockerignore) so
# rustup does not fetch a second toolchain or the Windows target.
FROM rust:1.99-bookworm@sha256:59037199c44290f2befcdd58dcc540164763fc296950255aaefeef096a1866b0 AS server
WORKDIR /src
COPY .cargo .cargo
COPY Cargo.toml Cargo.lock ./
COPY crates crates
COPY collector collector
COPY server server
# The N100 also runs Home Assistant and Frigate: two jobs at low priority.
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/src/target \
    CARGO_BUILD_JOBS=2 nice -n 10 cargo build --release --locked -p coach-server \
    && install -D -m 0755 target/release/coach-server /out/coach-server

# Stage 2: the web app's static files.
FROM node:24-bookworm-slim@sha256:0e0ff40c39bc087845bfb27465a0df4ea419520094bc35842ff83dd8cbe6f9b6 AS web
WORKDIR /src
RUN npm install --global --no-fund --no-audit pnpm@9.15.4
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY web/package.json web/package.json
RUN --mount=type=cache,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile
COPY web web
RUN pnpm -C web build

# Stage 3: runtime. Python 3.12 (stdlib only, no PyYAML) runs the analysis worker.
FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3
ENV LANG=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    COACH_BIND=0.0.0.0:8080 \
    COACH_DATA_DIR=/data \
    COACH_NAS_DIR=/nas/apps/iracing-coach \
    COACH_WEB_DIR=/app/web \
    COACH_PYTHON=/usr/local/bin/python3.12 \
    COACH_WORKER_SCRIPT=/app/iracing-coach/skills/analyze-iracing-race/scripts/mcp_server.py \
    COACH_CONTRACTS_DIR=/app/contracts
COPY --from=server /out/coach-server /usr/local/bin/coach-server
COPY --from=web /src/web/dist /app/web
COPY iracing-coach /app/iracing-coach
COPY contracts /app/contracts
COPY deploy/mini-pc/nas_probe.py /app/deploy/nas_probe.py
# Precompiled once here: the root filesystem is read-only at run time.
RUN python3.12 -m compileall -q /app/iracing-coach /app/deploy \
    && python3.12 -c "import sys; assert sys.version_info[:2] == (3, 12), sys.version"
WORKDIR /app
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/coach-server"]
