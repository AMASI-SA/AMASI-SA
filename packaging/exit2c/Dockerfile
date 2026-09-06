FROM python:3.11.16-slim-bookworm@sha256:528257d48c1da0dcecc2e725d1ae34498d60c965f1241e39cd6a85a8859bdf84
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt/mezan
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
COPY packaging/exit2a/requirements.txt /opt/requirements.txt
RUN python -m pip --isolated install --index-url https://pypi.org/simple --no-cache-dir -r /opt/requirements.txt \
    && python -m pip check
COPY backend /opt/mezan/backend
COPY packaging/exit2a /opt/rehearsal
COPY packaging/exit2c /opt/acceptance
RUN test -s backend/fonts/Cairo-Bold.ttf && test -s backend/assets/amasi_qr_logo.b64 \
    && test ! -e backend/.env && test ! -e backend/release_identity.json
# Public synthetic fixture values. This image is for acceptance, not deployment.
ENV APP_ENV=test TEST_RELEASE_STARTUP_KEY=test:exit2c-candidate \
    MEZAN_INDEPENDENT_RUNTIME=1 \
    MONGO_URL=mongodb://127.0.0.1:27017 DB_NAME=mezan_exit2c \
    JWT_SECRET=exit2c-public-synthetic-key-not-for-production \
    ADMIN_PASSWORD=Exit2C-synthetic-owner-password! \
    EMAIL_OTP_SMTP_HOST=127.0.0.1 EMAIL_OTP_SMTP_PORT=8025 \
    EMAIL_OTP_SMTP_STARTTLS=false EMAIL_OTP_FROM_EMAIL=otp@example.test
USER 65532:65532
ENTRYPOINT ["python", "/opt/mezan/backend/independent_runtime.py"]
CMD ["web"]
