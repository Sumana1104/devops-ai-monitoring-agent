FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt /tmp/requirements.txt

RUN pip install --no-cache-dir -r /tmp/requirements.txt
RUN python -m pip install --no-cache-dir --upgrade "setuptools==82.0.0"

COPY . .

EXPOSE 9000

CMD ["opentelemetry-instrument", "gunicorn", "app.main:app", "--workers", "2", "--bind", "0.0.0.0:9000"]