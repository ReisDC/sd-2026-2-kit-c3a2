# Imagem unica da plataforma: todos os servicos num container (plataforma.py).
# E a usada no deploy enxuto (Railway Free). Para a versao com um container por
# servico, use o docker-compose.yml (cada servico tem o proprio Dockerfile).
FROM docker.io/library/python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY comum ./comum
COPY proto ./proto
COPY gateway ./servicos/gateway
COPY inferencia ./servicos/inferencia
COPY worker ./servicos/worker
COPY ingestion-service ./servicos/ingestion-service
COPY retrieval-service ./servicos/retrieval-service
COPY generation-service ./servicos/generation-service
COPY plataforma.py .

# Stubs gRPC e modelo treinado no build (os mesmos dos containers separados).
RUN python -m grpc_tools.protoc -I proto --python_out=. --grpc_python_out=. proto/inferencia.proto \
    && python -m comum.modelo

RUN useradd --create-home appuser
USER appuser

EXPOSE 8000
CMD ["python", "plataforma.py"]
