FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg \
    FLITS_DATA_DIR=/data

WORKDIR /app

COPY pyproject.toml README.md requirements.txt MANIFEST.in ./
COPY flits ./flits

RUN pip install --no-cache-dir .

EXPOSE 8123

CMD ["flits", "--host", "0.0.0.0", "--port", "8123"]
