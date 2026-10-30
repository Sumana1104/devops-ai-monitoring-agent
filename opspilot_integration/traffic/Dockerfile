FROM python:3.10-slim
WORKDIR /app
RUN pip install --no-cache-dir 'fastapi>=0.110,<1' 'uvicorn>=0.27,<1'
COPY app.py /app/app.py
USER 10001:10001
EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
