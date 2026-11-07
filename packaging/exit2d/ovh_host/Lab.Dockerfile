FROM python:3.11-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends nftables iproute2 curl openssl ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /lab
COPY packaging/exit2d/ovh_host /lab
CMD ["sleep", "infinity"]
