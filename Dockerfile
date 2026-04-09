FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY lethe/ lethe/

ENV PYTHONUNBUFFERED=1

CMD ["uvicorn", "lethe.main:app", "--host", "0.0.0.0", "--port", "8080"]
