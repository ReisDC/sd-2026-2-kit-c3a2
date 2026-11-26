"""
Mensageria com RabbitMQ - API de ingestao e worker.

Fluxo: POST /indexar publica os documentos na fila "indexacao"; o worker
consome, vetoriza e grava o indice. Mensagens rejeitadas vao para a fila
"indexacao.falhas" (dead letter) em vez de sumirem.

O contexto de trace vai nos headers AMQP da mensagem.
"""
import json
import os
import time
from urllib.parse import quote

import pika

from comum import telemetria


def _url_broker() -> str:
    """RABBITMQ_URL completa, ou montada a partir de RABBITMQ_HOSTPORT e das
    mesmas credenciais do broker (RABBITMQ_DEFAULT_USER/PASS) - formato do Render."""
    if os.getenv("RABBITMQ_URL"):
        return os.environ["RABBITMQ_URL"]
    usuario = quote(os.getenv("RABBITMQ_DEFAULT_USER", "guest"), safe="")
    senha = quote(os.getenv("RABBITMQ_DEFAULT_PASS", "guest"), safe="")
    hostport = os.getenv("RABBITMQ_HOSTPORT", "localhost:5672")
    return f"amqp://{usuario}:{senha}@{hostport}/%2F"


RABBITMQ_URL = _url_broker()
FILA_INDEXACAO = "indexacao"
FILA_FALHAS = "indexacao.falhas"
# pika repassa erros de socket (ex.: DNS do broker) sem embrulhar em AMQPError.
ERROS_BROKER = (pika.exceptions.AMQPError, OSError)


def conectar():
    parametros = pika.URLParameters(RABBITMQ_URL)
    parametros.socket_timeout = 5
    parametros.blocked_connection_timeout = 10
    return pika.BlockingConnection(parametros)


def declarar_filas(canal):
    """Idempotente: quem chegar primeiro (API ou worker) cria as filas."""
    canal.queue_declare(queue=FILA_FALHAS, durable=True)
    canal.queue_declare(queue=FILA_INDEXACAO, durable=True, arguments={
        "x-dead-letter-exchange": "",
        "x-dead-letter-routing-key": FILA_FALHAS,
    })


def publicar(mensagem, tentativas=3, espera=1):
    """Publica com confirmacao do broker; repete falhas de conexao."""
    for tentativa in range(tentativas):
        try:
            conexao = conectar()
            try:
                canal = conexao.channel()
                canal.confirm_delivery()
                declarar_filas(canal)
                canal.basic_publish(
                    exchange="",
                    routing_key=FILA_INDEXACAO,
                    body=json.dumps(mensagem, ensure_ascii=False).encode("utf-8"),
                    properties=pika.BasicProperties(
                        delivery_mode=2,
                        content_type="application/json",
                        headers=telemetria.injetar(),
                    ),
                    mandatory=True,
                )
            finally:
                if conexao.is_open:
                    conexao.close()
            return
        except ERROS_BROKER:
            if tentativa == tentativas - 1:
                raise
            time.sleep(espera * (2 ** tentativa))
