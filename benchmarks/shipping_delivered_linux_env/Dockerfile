FROM python:3.11-slim-bookworm
WORKDIR /probe
COPY requirements.txt /probe/requirements.txt
RUN python -m pip install --no-cache-dir -r /probe/requirements.txt
COPY probe.py /probe/probe.py
ENV PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1
USER 65534:65534
ENTRYPOINT ["python", "-I", "-B", "/probe/probe.py"]
