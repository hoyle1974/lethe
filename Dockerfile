FROM python:3.14-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY lethe/ lethe/

ENV PYTHONUNBUFFERED=1

RUN useradd --no-create-home --shell /bin/false appuser
USER appuser

HEALTHCHECK CMD curl -f http://localhost:8080/v1/health || exit 1

CMD ["uvicorn", "lethe.main:app", "--host", "0.0.0.0", "--port", "8080"]
