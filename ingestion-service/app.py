"""
Servico de INGESTAO: recebe documentos e entrega o
indice vetorial ao servico de recuperacao.

POST /indexar NAO vetoriza na hora: publica na fila "indexacao" (RabbitMQ) e
responde 202. O worker (worker_indexacao.py) consome, gera os embeddings e
grava o indice no volume. GET /indice le o arquivo gravado pelo worker.

Rodar: uvicorn app:app --port 8002
"""
import os
import uuid
from threading import Lock
from typing import Annotated

from fastapi import Body, FastAPI, HTTPException
from opentelemetry import trace

import fila
import indice as armazenamento
from comum import telemetria
from modelos import Documento

app = FastAPI(title="Ingestao - C3.A2")
tracer = telemetria.configurar("ingestao", app)

# Cache do indice em memoria, recarregado quando o worker regrava o arquivo.
_cache = {"assinatura": None, "itens": []}
_lock = Lock()


def indice_atual():
    try:
        estado = os.stat(armazenamento.ARQUIVO)
    except FileNotFoundError:
        return []
    assinatura = (estado.st_mtime_ns, estado.st_size)
    with _lock:
        if _cache["assinatura"] != assinatura:
            _cache["itens"] = armazenamento.carregar()
            _cache["assinatura"] = assinatura
        return _cache["itens"]


@app.post("/indexar", status_code=202)
def indexar(docs: Annotated[list[Documento], Body(min_length=1, max_length=100)]):
    mensagem = {"id": str(uuid.uuid4()), "documentos": [d.model_dump() for d in docs]}
    with tracer.start_as_current_span(
        "fila.publicar",
        kind=trace.SpanKind.PRODUCER,
        attributes={
            "messaging.system": "rabbitmq",
            "messaging.destination.name": fila.FILA_INDEXACAO,
            "mensagem.id": mensagem["id"],
            "documentos.quantidade": len(docs),
        },
    ):
        try:
            fila.publicar(mensagem)
        except fila.ERROS_BROKER:
            raise HTTPException(status_code=503,
                                detail="Fila de indexacao indisponivel.") from None
    return {"status": "enfileirado", "id": mensagem["id"], "documentos": len(docs)}


@app.get("/indice")
def indice():
    """Consumido pelo servico de recuperacao."""
    try:
        itens = indice_atual()
    except (OSError, ValueError):
        raise HTTPException(status_code=503, detail="Indice ilegivel no disco.") from None
    return {"total": len(itens), "itens": itens}


@app.get("/saude")
def saude():
    try:
        documentos = len(indice_atual())
    except (OSError, ValueError):
        documentos = None
    return {"status": "ok", "documentos": documentos}
