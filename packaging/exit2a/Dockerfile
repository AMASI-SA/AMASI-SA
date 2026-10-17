FROM python:3.11.16-slim-bookworm@sha256:528257d48c1da0dcecc2e725d1ae34498d60c965f1241e39cd6a85a8859bdf84
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt/mezan
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
COPY packaging/exit2a/requirements.txt /opt/rehearsal/requirements.txt
RUN python -m pip --isolated install --index-url https://pypi.org/simple --no-cache-dir -r /opt/rehearsal/requirements.txt \
    && python -m pip check
COPY backend /opt/mezan/backend
COPY packaging/exit2a /opt/rehearsal
RUN test -s backend/fonts/Cairo-Bold.ttf \
    && test -s backend/assets/amasi_qr_logo.b64 \
    && test ! -e backend/.env \
    && test ! -e backend/release_identity.json
ENV MEZAN_EXIT2A_REHEARSAL=1 \
    MONGO_URL=mongodb://127.0.0.1:27017 \
    DB_NAME=mezan_exit2a \
    JWT_SECRET=exit2a-public-synthetic-key-not-for-production
USER 65532:65532
ENTRYPOINT ["python", "/opt/rehearsal/entrypoint.py"]
CMD ["web"]
