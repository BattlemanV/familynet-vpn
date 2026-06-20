FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    wireguard-tools \
    iptables \
    iproute2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY init_config.py .
COPY web/ web/

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV WG_INSIDE_CONTAINER=1
ENV APP_DIR=/data
ENV TOKEN_FILE=/data/api_token

VOLUME ["/data"]

EXPOSE 8000
EXPOSE 51820/udp

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD curl -f http://10.8.0.1:8000/health || exit 1

ENTRYPOINT ["/entrypoint.sh"]
