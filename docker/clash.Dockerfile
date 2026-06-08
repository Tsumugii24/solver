FROM metacubex/mihomo:latest

USER root

RUN apk add --no-cache curl ca-certificates 2>/dev/null || true

COPY clash-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
