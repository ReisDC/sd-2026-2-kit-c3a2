# Build a partir da RAIZ do repositorio (contexto "."), para incluir comum/.
FROM docker.io/library/python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY inferencia/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY comum ./comum
COPY proto ./proto
COPY inferencia/app.py inferencia/servidor_grpc.py ./

# Stubs gRPC gerados a partir do contrato e modelo treinado no build (mesmo do worker).
RUN python -m grpc_tools.protoc -I proto --python_out=. --grpc_python_out=. proto/inferencia.proto \
    && python -m comum.modelo

RUN useradd --create-home appuser
USER appuser

# 8001 = REST (padrao); 50051 = gRPC (servico inferencia-grpc, comando "python servidor_grpc.py")
EXPOSE 8001 50051
# --no-access-log: o log de acesso do uvicorn grava o IP do cliente (dado pessoal).
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001", "--no-access-log"]
