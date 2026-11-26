"""
Fila de tarefas de inferencia no Redis.

Usada pelo servico de inferencia (enfileira e consulta) e pelo worker
(consome e grava o resultado). Cada mensagem leva o contexto de trace em
"trace" para o worker continuar o mesmo rastreamento.
"""
import json
import os
import uuid

import redis

from comum import telemetria

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
FILA_TAREFAS = "tarefas"
FILA_DESCARTE = "tarefas:descarte"
PREFIXO_RESULTADO = "resultado:"
# Resultados expiram para nao guardar o texto do cliente indefinidamente (LGPD).
RESULTADO_TTL_SEGUNDOS = int(os.getenv("RESULTADO_TTL_SEGUNDOS", "86400"))

_cliente = None


def cliente():
    global _cliente
    if _cliente is None:
        _cliente = redis.from_url(REDIS_URL, decode_responses=True)
    return _cliente


def enfileirar(texto: str) -> str:
    """Coloca uma tarefa na fila e devolve o id para consulta posterior."""
    tarefa_id = str(uuid.uuid4())
    mensagem = {"id": tarefa_id, "texto": texto, "trace": telemetria.injetar()}
    with cliente().pipeline(transaction=True) as transacao:
        transacao.set(PREFIXO_RESULTADO + tarefa_id,
                      json.dumps({"status": "na_fila"}), ex=RESULTADO_TTL_SEGUNDOS)
        transacao.rpush(FILA_TAREFAS, json.dumps(mensagem))
        transacao.execute()
    return tarefa_id


def proxima_tarefa(timeout: int = 5):
    """Bloqueia ate chegar tarefa (ou timeout). Usado pelo worker."""
    item = cliente().blpop(FILA_TAREFAS, timeout=timeout)
    if item is None:
        return None
    return json.loads(item[1])


def guardar_resultado(tarefa_id: str, resultado: dict) -> None:
    cliente().set(PREFIXO_RESULTADO + tarefa_id, json.dumps(resultado),
                  ex=RESULTADO_TTL_SEGUNDOS)


def buscar_resultado(tarefa_id: str):
    bruto = cliente().get(PREFIXO_RESULTADO + tarefa_id)
    return json.loads(bruto) if bruto else None


def tamanho_fila() -> int:
    return cliente().llen(FILA_TAREFAS)
