FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt /tmp/requirements.txt

RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY . .

EXPOSE 9000

CMD ["opentelemetry-instrument", "gunicorn", "app.main:app", "--workers", "2", "--bind", "0.0.0.0:9000"]