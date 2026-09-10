# Atman Python-oracle image. Go core is a later stage (T-643+); this image
# ships the current installable `tickets` package with a version label.
FROM python:3.11.13-slim-bookworm

ARG ATMAN_VERSION=0.2.0
ARG ATMAN_REVISION=unknown

LABEL org.opencontainers.image.title="atman-tickets" \
      org.opencontainers.image.description="Atman ticket-board CLI (Python oracle)" \
      org.opencontainers.image.version="${ATMAN_VERSION}" \
      org.opencontainers.image.revision="${ATMAN_REVISION}" \
      org.opencontainers.image.source="https://github.com/advitiyavashist/atman"

WORKDIR /opt/atman
COPY pyproject.toml README.md ./
COPY src ./src
COPY packaging/constraints.txt ./packaging/constraints.txt

RUN pip install --no-cache-dir --upgrade pip==24.3.1 \
 && pip install --no-cache-dir --constraint packaging/constraints.txt .

USER nobody
ENTRYPOINT ["tickets"]
CMD ["--help"]
