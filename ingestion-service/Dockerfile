# Build a partir da RAIZ do repositorio (contexto "."), para incluir comum/.
# Mesma imagem para a API de ingestao e o worker de indexacao (o compose troca o comando).
FROM docker.io/library/python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY ingestion-service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY comum ./comum
COPY ingestion-service/*.py ./
COPY ingestion-service/documentos ./documentos

# /dados recebe o volume do indice vetorial; o dono precisa ser o usuario do app.
RUN useradd --create-home appuser && mkdir -p /dados && chown appuser /dados
USER appuser

EXPOSE 8002
# --no-access-log: o log de acesso do uvicorn grava o IP do cliente (dado pessoal).
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8002", "--no-access-log"]
