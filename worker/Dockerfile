# Build a partir da RAIZ do repositorio (contexto "."), para incluir comum/.
FROM docker.io/library/python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY worker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY comum ./comum
COPY worker/worker.py ./

# Treina o modelo no build: o container sobe pronto e todas as replicas usam o mesmo.
RUN python -m comum.modelo

RUN useradd --create-home appuser
USER appuser

CMD ["python", "worker.py"]
