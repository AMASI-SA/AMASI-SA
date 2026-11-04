# Synthetic-hosting image only. Production continues to require Release Guard V5.
FROM node:22.23.2-bookworm-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/yarn.lock ./
COPY frontend/vendor ./vendor
RUN corepack enable && corepack prepare yarn@1.22.22 --activate && yarn install --frozen-lockfile --non-interactive
COPY frontend/ ./
# Explicit same-origin API. Never dispatch the Emergent release adapter here.
RUN REACT_APP_BACKEND_URL='' NODE_ENV=production node node_modules/vite/bin/vite.js build --mode production

FROM python:3.11.16-slim-bookworm@sha256:528257d48c1da0dcecc2e725d1ae34498d60c965f1241e39cd6a85a8859bdf84
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt/mezan
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core nginx-light \
    && rm -rf /var/lib/apt/lists/* /etc/nginx/sites-enabled/default
COPY packaging/exit2d/runtime-requirements.txt /opt/runtime-requirements.txt
RUN python -m pip --isolated install --index-url https://pypi.org/simple --no-cache-dir -r /opt/runtime-requirements.txt && python -m pip check
COPY backend/ backend/
COPY packaging/exit2d/render/ /opt/render/
COPY --from=frontend /build/frontend/build /opt/frontend/
ARG RENDER_GIT_COMMIT
RUN test ! -e backend/.env && test ! -e backend/release_identity.json \
    && python -c "import sys; sys.path.insert(0,'backend'); from independent_staging import write_manifest; write_manifest(sys.argv[1])" "$RENDER_GIT_COMMIT" \
    && python -c "import json,sys; open('/opt/frontend/build-meta.json','w').write(json.dumps({'kind':'synthetic-hosting-only-v1','source_sha':sys.argv[1]}))" "$RENDER_GIT_COMMIT" \
    && chmod -R a+rX /opt/mezan /opt/frontend /opt/render
USER 65532:65532
EXPOSE 10000
ENTRYPOINT ["python", "/opt/render/start.py"]
CMD ["web"]
