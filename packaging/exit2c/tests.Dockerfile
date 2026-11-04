FROM mezan-exit2c:candidate
USER root
# Test-only packages, never substituted for runtime application modules.
RUN python -m pip --isolated install --index-url https://pypi.org/simple --no-cache-dir \
    pytest-asyncio==1.4.0 mongomock-motor==0.0.36 mongomock==4.3.0 sentinels==1.1.1 sortedcontainers==2.4.0 \
    && python -m pip check
COPY frontend/src/pages/Login.jsx /opt/mezan/frontend/src/pages/Login.jsx
USER 65532:65532
