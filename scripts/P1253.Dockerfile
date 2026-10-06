FROM python:3.12.15-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /requirements.txt
RUN python -m pip install --no-cache-dir -r /requirements.txt
ENV PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1
