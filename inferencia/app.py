"""
Servico de inferencia.

POST /predict           -> enfileira a tarefa no Redis e devolve o id (202)
GET  /resultado/{id}    -> estado/resultado da tarefa (404 se nao existir)

A inferencia em si e feita pelo worker (worker/worker.py), que consome a fila.
A via sincrona fica no servidor gRPC (servidor_grpc.py).
Rodar: uvicorn app:app --port 8001
"""
import redis
from fastapi import FastAPI, HTTPException
from opentelemetry import trace
from pydantic import BaseModel, Field

from comum import fila_tarefas, telemetria
from comum.logs import registrar_requisicoes

app = FastAPI(title="Servico de Inferencia - C3.A2")
tracer = telemetria.configurar("inferencia", app)
registrar_requisicoes(app, "inferencia")


class Entrada(BaseModel):
    texto: str = Field(min_length=1, max_length=5000)


@app.get("/saude")
def saude():
    try:
        return {"status": "ok", "fila": fila_tarefas.tamanho_fila()}
    except redis.RedisError:
        raise HTTPException(status_code=503, detail="redis indisponivel") from None


@app.post("/predict", status_code=202)
def predict(entrada: Entrada):
    """Enfileira e devolve o id na hora, sem esperar a inferencia."""
    if not entrada.texto.strip():
        raise HTTPException(status_code=400, detail="texto vazio")
    with tracer.start_as_current_span(
        "fila.enfileirar",
        kind=trace.SpanKind.PRODUCER,
        attributes={
            "messaging.system": "redis",
            "messaging.destination.name": fila_tarefas.FILA_TAREFAS,
            "entrada.tamanho": len(entrada.texto),
        },
    ) as span:
        try:
            tarefa_id = fila_tarefas.enfileirar(entrada.texto)
        except redis.RedisError:
            raise HTTPException(status_code=503, detail="fila indisponivel") from None
        span.set_attribute("tarefa.id", tarefa_id)
    return {"id": tarefa_id, "status": "na_fila"}


@app.get("/resultado/{tarefa_id}")
def resultado(tarefa_id: str):
    try:
        res = fila_tarefas.buscar_resultado(tarefa_id)
    except redis.RedisError:
        raise HTTPException(status_code=503, detail="fila indisponivel") from None
    if res is None:
        raise HTTPException(status_code=404, detail="tarefa nao encontrada")
    return {"id": tarefa_id, **res}
