FROM python:3.10-slim

WORKDIR /app

# Install available Debian security updates.
RUN apt-get update \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt

# Install app dependencies, then replace setuptools' bundled libraries.
RUN python -m pip install --no-cache-dir -r /tmp/requirements.txt \
    && python -m pip install --no-cache-dir --upgrade "setuptools==80.10.2" \
    && python -m pip check

COPY . .

EXPOSE 9000

CMD ["opentelemetry-instrument", "uvicorn", "app.main:app", "--workers", "2", "--host", "0.0.0.0", "--port", "9000"]
