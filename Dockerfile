FROM python:3.10-slim

WORKDIR /app

# Break cache by copying requirements to a temp path
COPY requirements.txt /tmp/requirements.txt

# Force reinstall every time
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Copy the entire app AFTER installing dependencies
COPY . .

EXPOSE 9000

CMD ["opentelemetry-instrument", "gunicorn", "app.main:app", "--workers", "2", "--bind", "0.0.0.0:9000"]
