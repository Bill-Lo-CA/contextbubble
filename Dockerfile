# Debian and Python base tags are intentionally patch-updatable; byte-identical rebuilds are out of scope.
FROM debian:bookworm-slim AS whisper-builder

ARG WHISPER_CPP_REF=v1.8.6

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        build-essential \
        ca-certificates \
        cmake \
        git \
    && rm -rf /var/lib/apt/lists/*

# amd64 targets the portable x86-64 baseline (SSE2) instead of build-host ISA extensions.
RUN git clone --branch "${WHISPER_CPP_REF}" --depth 1 \
        https://github.com/ggml-org/whisper.cpp.git /src/whisper.cpp \
    && arch="$(dpkg --print-architecture)" \
    && case "${arch}" in \
        arm64) set -- "-DGGML_CPU_ARM_ARCH=armv8-a" ;; \
        amd64) set -- \
            "-DGGML_SSE42=OFF" \
            "-DGGML_AVX=OFF" \
            "-DGGML_AVX2=OFF" \
            "-DGGML_BMI2=OFF" \
            "-DGGML_FMA=OFF" \
            "-DGGML_F16C=OFF" ;; \
        *) echo "unsupported build architecture: ${arch}" >&2; exit 1 ;; \
    esac \
    && cmake -S /src/whisper.cpp -B /src/whisper.cpp/build \
        -DCMAKE_BUILD_TYPE=Release \
        -DBUILD_SHARED_LIBS=OFF \
        -DGGML_NATIVE=OFF \
        -DGGML_CUDA=OFF \
        -DGGML_VULKAN=OFF \
        -DGGML_METAL=OFF \
        -DWHISPER_BUILD_TESTS=OFF \
        -DWHISPER_BUILD_SERVER=OFF \
        "$@" \
    && cmake --build /src/whisper.cpp/build --config Release \
        --target whisper-cli --parallel


FROM python:3.12-slim-bookworm AS runtime

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        ca-certificates \
        curl \
        ffmpeg \
        gosu \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.11.22 /uv /uvx /bin/
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev

COPY backend ./backend
COPY --from=whisper-builder /src/whisper.cpp/build/bin/whisper-cli /opt/whisper/bin/whisper-cli
COPY docker/bootstrap-model.sh /usr/local/bin/contextbubble-bootstrap-model
COPY docker/entrypoint.sh /usr/local/bin/contextbubble-entrypoint

RUN groupadd --gid 10001 contextbubble \
    && useradd --uid 10001 --gid contextbubble --create-home \
        --home-dir /home/contextbubble \
        --shell /usr/sbin/nologin contextbubble \
    && install -d -o contextbubble -g contextbubble -m 0750 \
        /data /data/media /models /tmp/contextbubble \
    && chmod 0755 \
        /opt/whisper/bin/whisper-cli \
        /usr/local/bin/contextbubble-bootstrap-model \
        /usr/local/bin/contextbubble-entrypoint

ENV PYTHONUNBUFFERED=1 \
    HOME=/home/contextbubble \
    PATH=/app/.venv/bin:$PATH \
    TMPDIR=/tmp/contextbubble \
    XDG_CACHE_HOME=/tmp/contextbubble/cache \
    DENO_DIR=/tmp/contextbubble/deno

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/contextbubble-entrypoint"]
CMD ["python", "backend/server.py"]
