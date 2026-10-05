FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt /tmp/requirements.txt

RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY . .

EXPOSE 9000

CMD ["opentelemetry-instrument", "uvicorn", "app.main:app", "--workers", "2", "--host", "0.0.0.0", "--port", "9000"]
