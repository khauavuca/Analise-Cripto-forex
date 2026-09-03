FROM python:3.10-slim

# Instalar dependências de sistema (necessárias para o scikit-learn e pandas em ambientes linux enxutos)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instalar pacotes Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar o restante da aplicação
COPY . .

# Comando padrão
CMD ["python", "worker_service.py"]
