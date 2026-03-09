FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg \
    FLITS_DATA_DIR=/data

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY README.md .
COPY flits ./flits
COPY web_static ./web_static

EXPOSE 8123

CMD ["python", "-m", "flits", "--host", "0.0.0.0", "--port", "8123"]
