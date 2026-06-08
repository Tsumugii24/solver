# TexasSolver pipeline image: pre-built console_solver + Python deps.
#
# Build once (slow), then on new servers:
#   docker pull ghcr.io/<user>/solver-pipeline:latest
#   docker compose run --rm pipeline python run_pipeline.py ...

ARG UBUNTU_CODENAME=noble

FROM ubuntu:24.04 AS builder
ARG UBUNTU_CODENAME
ENV DEBIAN_FRONTEND=noninteractive
ENV UBUNTU_CODENAME=${UBUNTU_CODENAME}

WORKDIR /app

COPY docker/install-build-deps.sh /tmp/install-build-deps.sh
RUN chmod +x /tmp/install-build-deps.sh && /tmp/install-build-deps.sh

COPY . /app

RUN chmod +x compile.sh && ./compile.sh --skip-deps

RUN test -x install/console_solver


FROM ubuntu:24.04 AS runtime
ARG UBUNTU_CODENAME
ENV DEBIAN_FRONTEND=noninteractive
ENV UBUNTU_CODENAME=${UBUNTU_CODENAME}
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PIP_NO_CACHE_DIR=1
ENV PIP_BREAK_SYSTEM_PACKAGES=1

WORKDIR /app

COPY docker/install-runtime-deps.sh /tmp/install-runtime-deps.sh
RUN chmod +x /tmp/install-runtime-deps.sh && /tmp/install-runtime-deps.sh

COPY requirements.txt /app/requirements.txt
RUN python3 -m pip install --upgrade pip && \
    python3 -m pip install -r requirements.txt

COPY --from=builder /app/install/console_solver /app/install/console_solver
COPY . /app

RUN chmod +x docker/entrypoint.sh

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["python3", "run_pipeline.py", "--help"]
