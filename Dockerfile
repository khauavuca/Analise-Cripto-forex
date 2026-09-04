FROM python:3.14-slim

# UTC no container. Todo o sistema grava e compara velas em UTC; um container
# em horario local produziria rotulos deslocados e quebraria o alinhamento das
# velas de 4h - o mesmo problema que `origin="epoch"` resolve na agregacao.
ENV TZ=UTC \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Processo que so le mercado publico nao tem motivo para rodar como root.
RUN useradd --create-home --uid 10001 coletor \
    && mkdir -p /app/dados /app/logs \
    && chown -R coletor:coletor /app
USER coletor

# O banco fica no volume, nao na camada da imagem: container recriado nao pode
# levar semanas de coleta junto.
ENV BANCO_DADOS=/app/dados/dados_mercado.db

# Nenhuma ordem e enviada. O servico so le mercado publico e grava observacoes.
CMD ["python", "cli.py", "monitorar", "--minutos", "0"]
