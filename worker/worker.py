"""
Worker de inferencia: consome a fila Redis, roda o
modelo e grava o resultado. Falhou? Reenfileira ate MAX_TENTATIVAS e depois
manda para a fila de descarte (tarefas:descarte).

Cada tarefa vira um span "worker.processar" filho do trace iniciado no gateway,
reconstruido a partir do contexto que viajou dentro da mensagem.

Rodar: python worker.py
"""
import json
import logging
import os
import time

import redis
from opentelemetry import trace

from comum import fila_tarefas as fila
from comum import telemetria
from comum.modelo import carregar_modelo

MAX_TENTATIVAS = int(os.getenv("WORKER_MAX_TENTATIVAS", "3"))
# Segundos de espera do BLPOP; 0 bloqueia sem acordar periodicamente (deixa o
# container hibernar por inatividade no plano Free do Railway).
ESPERA_FILA = int(os.getenv("FILA_ESPERA_SEGUNDOS", "5"))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("worker")
tracer = telemetria.configurar("worker")


def processar_tarefa(tarefa, modelo):
    tentativa = tarefa.get("tentativas", 0) + 1
    tarefa["tentativas"] = tentativa
    tarefa_id = tarefa["id"]
    tamanho = len(tarefa["texto"])

    with tracer.start_as_current_span(
        "worker.processar",
        context=telemetria.extrair(tarefa.get("trace")),
        kind=trace.SpanKind.CONSUMER,
        attributes={
            "messaging.system": "redis",
            "messaging.destination.name": fila.FILA_TAREFAS,
            "tarefa.id": tarefa_id,
            "tarefa.tentativa": tentativa,
            "entrada.tamanho": tamanho,
        },
    ) as span:
        inicio = time.perf_counter()
        try:
            with tracer.start_as_current_span("modelo.prever"):
                resultado = modelo.prever(tarefa["texto"])
        except Exception as erro:
            span.record_exception(erro)
            span.set_status(trace.Status(trace.StatusCode.ERROR))
            _tratar_falha(tarefa, tentativa, type(erro).__name__)
            return

        tempo_ms = round((time.perf_counter() - inicio) * 1000, 2)
        resultado.pop("texto", None)  # o texto nao precisa voltar ao cliente
        resultado.update(status="pronto", tentativas=tentativa, tempo_ms=tempo_ms)
        fila.guardar_resultado(tarefa_id, resultado)
        span.set_attribute("resultado.sentimento", resultado["sentimento"])
        logger.info("id=%s tamanho_entrada=%d tentativa=%d tempo_ms=%s - concluida",
                    tarefa_id, tamanho, tentativa, tempo_ms)


def _tratar_falha(tarefa, tentativa, tipo_erro):
    tarefa_id = tarefa["id"]
    logger.error("id=%s tentativa=%d - erro: %s", tarefa_id, tentativa, tipo_erro)
    if tentativa < MAX_TENTATIVAS:
        destino = fila.FILA_TAREFAS
        estado = {"status": "na_fila", "tentativas": tentativa}
    else:
        destino = fila.FILA_DESCARTE
        estado = {"status": "erro", "tentativas": tentativa,
                  "erro": f"inferencia falhou apos {tentativa} tentativas"}

    # Grava o estado e move a tarefa na mesma transacao.
    with fila.cliente().pipeline(transaction=True) as transacao:
        transacao.set(fila.PREFIXO_RESULTADO + tarefa_id, json.dumps(estado),
                      ex=fila.RESULTADO_TTL_SEGUNDOS)
        transacao.rpush(destino, json.dumps(tarefa))
        transacao.execute()
    logger.warning("id=%s - %s", tarefa_id,
                   "enviada para descarte" if destino == fila.FILA_DESCARTE
                   else "reenfileirada")


def main():
    logger.info("carregando modelo...")
    modelo = carregar_modelo()
    logger.info("pronto. aguardando tarefas na fila '%s'", fila.FILA_TAREFAS)

    while True:
        try:
            tarefa = fila.proxima_tarefa(timeout=ESPERA_FILA)
        except redis.RedisError as erro:
            logger.warning("redis indisponivel (%s); nova tentativa em 3s",
                           type(erro).__name__)
            time.sleep(3)
            continue
        if tarefa is not None:
            processar_tarefa(tarefa, modelo)


if __name__ == "__main__":
    main()
