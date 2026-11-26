# Build a partir da RAIZ do repositorio (contexto "."), para incluir comum/.
FROM docker.io/library/python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY generation-service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY comum ./comum
COPY generation-service/*.py ./

RUN useradd --create-home appuser
USER appuser

EXPOSE 8004
# --no-access-log: o log de acesso do uvicorn grava o IP do cliente (dado pessoal).
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8004", "--no-access-log"]
