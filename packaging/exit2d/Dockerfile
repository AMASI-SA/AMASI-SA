FROM python:3.11.16-slim-bookworm@sha256:528257d48c1da0dcecc2e725d1ae34498d60c965f1241e39cd6a85a8859bdf84
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt/mezan
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
COPY packaging/exit2d/runtime-requirements.txt /opt/runtime-requirements.txt
RUN python -m pip --isolated install --index-url https://pypi.org/simple --no-cache-dir \
    -r /opt/runtime-requirements.txt && python -m pip check
COPY backend /opt/mezan/backend
RUN test ! -e backend/.env && python -m compileall -q backend
USER 65532:65532
EXPOSE 8000
ENTRYPOINT ["python", "/opt/mezan/backend/independent_runtime.py"]
CMD ["web", "--port", "8000"]
