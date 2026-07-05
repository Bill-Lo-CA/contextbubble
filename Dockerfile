FROM debian:bookworm-slim AS whisper-builder

ARG WHISPER_CPP_REF=v1.8.6

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        build-essential \
        ca-certificates \
        cmake \
        git \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --branch "${WHISPER_CPP_REF}" --depth 1 \
        https://github.com/ggml-org/whisper.cpp.git /src/whisper.cpp \
    && cmake -S /src/whisper.cpp -B /src/whisper.cpp/build \
        -DCMAKE_BUILD_TYPE=Release \
        -DBUILD_SHARED_LIBS=OFF \
        -DGGML_CUDA=OFF \
        -DGGML_VULKAN=OFF \
        -DGGML_METAL=OFF \
        -DWHISPER_BUILD_TESTS=OFF \
        -DWHISPER_BUILD_SERVER=OFF \
    && cmake --build /src/whisper.cpp/build --config Release \
        --target whisper-cli --parallel


FROM python:3.12-slim-bookworm AS runtime

ARG YT_DLP_VERSION=2026.7.4

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        ca-certificates \
        curl \
        ffmpeg \
        gosu \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir "yt-dlp==${YT_DLP_VERSION}"

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir --requirement requirements.txt

COPY backend ./backend
COPY --from=whisper-builder /src/whisper.cpp/build/bin/whisper-cli /opt/whisper/bin/whisper-cli
COPY docker/bootstrap-model.sh /usr/local/bin/contextbubble-bootstrap-model
COPY docker/entrypoint.sh /usr/local/bin/contextbubble-entrypoint

RUN groupadd --gid 10001 contextbubble \
    && useradd --uid 10001 --gid contextbubble --no-create-home \
        --shell /usr/sbin/nologin contextbubble \
    && install -d -o contextbubble -g contextbubble -m 0750 \
        /data /data/media /models /tmp/contextbubble \
    && chmod 0755 \
        /opt/whisper/bin/whisper-cli \
        /usr/local/bin/contextbubble-bootstrap-model \
        /usr/local/bin/contextbubble-entrypoint

ENV PYTHONUNBUFFERED=1 \
    TMPDIR=/tmp/contextbubble

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/contextbubble-entrypoint"]
CMD ["python", "backend/server.py"]
