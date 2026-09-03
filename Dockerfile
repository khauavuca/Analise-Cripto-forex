FROM python:3.14-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# So analisa e registra sinais. Nao envia ordem.
CMD ["python", "worker_service.py"]
