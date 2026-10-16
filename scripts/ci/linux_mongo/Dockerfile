FROM python:3.12-slim-bookworm
COPY requirements.txt /requirements.txt
RUN python -m pip install --no-cache-dir -r /requirements.txt
WORKDIR /source/backend
ENV PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH=.:tests
