# Build a partir da RAIZ do repositorio (contexto "."), para incluir comum/.
FROM docker.io/library/python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY gateway/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY comum ./comum
COPY proto ./proto
COPY gateway/app.py gateway/metricas.html ./

# Stubs do cliente gRPC (contrato em proto/inferencia.proto).
RUN python -m grpc_tools.protoc -I proto --python_out=. --grpc_python_out=. proto/inferencia.proto

RUN useradd --create-home appuser
USER appuser

EXPOSE 8000
# --no-access-log: o log de acesso do uvicorn grava o IP do cliente (dado pessoal).
# --proxy-headers: atras do proxy HTTPS, o esquema/IP reais vem do X-Forwarded-*.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--proxy-headers", "--forwarded-allow-ips", "*"]
