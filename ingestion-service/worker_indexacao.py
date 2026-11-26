"""
Worker de INDEXACAO: consome a fila "indexacao",
vetoriza os documentos e grava o indice (armazenamento vetorial) no volume.

Na primeira subida, se o indice estiver vazio, indexa os textos de exemplo de
documentos/ para a plataforma ja responder perguntas.

Rode UMA instancia: o worker e o unico escritor do indice e processa uma
mensagem por vez (prefetch_count=1 no RabbitMQ; BLPOP no Redis).

Rodar: python worker_indexacao.py
"""
import json
import logging
import os
import time
from pathlib import Path

import pika
from opentelemetry import trace
from pydantic import ValidationError

import fila
import indice
from comum import telemetria
from modelos import Documento

PASTA_DOCUMENTOS = Path(os.getenv("PASTA_DOCUMENTOS", Path(__file__).with_name("documentos")))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("worker-indexacao")
tracer = telemetria.configurar("worker-indexacao")


def indexar_documentos_iniciais():
    if indice.carregar() or not PASTA_DOCUMENTOS.is_dir():
        return
    docs = [{"texto": p.read_text(encoding="utf-8").strip(), "origem": p.name}
            for p in sorted(PASTA_DOCUMENTOS.glob("*.txt"))]
    if docs:
        indice.salvar(indice.adicionar([], docs))
        logger.info("indice inicial criado com %d documentos de exemplo", len(docs))


def tratar_mensagem(corpo, headers) -> bool:
    """Vetoriza e grava os documentos da mensagem. Devolve False se a mensagem
    for invalida (vai para a fila de falhas); erros de gravacao sobem."""
    with tracer.start_as_current_span(
        "worker.indexar",
        context=telemetria.extrair(headers),
        kind=trace.SpanKind.CONSUMER,
        attributes={"messaging.system": "redis" if fila.usa_redis() else "rabbitmq",
                    "messaging.destination.name": fila.FILA_INDEXACAO},
    ) as span:
        try:
            dados = json.loads(corpo) if isinstance(corpo, (str, bytes)) else corpo
            docs = [Documento(**d).model_dump() for d in dados["documentos"]]
        except (ValueError, KeyError, TypeError, ValidationError) as erro:
            # Mensagem malformada nunca vai dar certo: vai para a fila de falhas.
            # Loga so o tipo do erro: a mensagem pode conter o texto enviado.
            logger.warning("mensagem invalida descartada: %s", type(erro).__name__)
            span.set_status(trace.Status(trace.StatusCode.ERROR, "mensagem invalida"))
            return False

        span.set_attribute("mensagem.id", str(dados.get("id")))
        with tracer.start_as_current_span("indice.vetorizar_e_gravar"):
            itens = indice.adicionar(indice.carregar(), docs)
            indice.salvar(itens)
        span.set_attribute("indice.total", len(itens))
        logger.info("mensagem %s processada: %d documentos no indice",
                    dados.get("id"), len(itens))
        return True


def processar(canal, metodo, propriedades, corpo):
    """Callback do RabbitMQ: ack so depois de gravar (se cair antes, reentrega)."""
    if tratar_mensagem(corpo.decode("utf-8"), propriedades.headers):
        canal.basic_ack(delivery_tag=metodo.delivery_tag)
    else:
        canal.basic_nack(delivery_tag=metodo.delivery_tag, requeue=False)


def consumir_redis():
    """Mesmo fluxo com a fila numa lista do Redis (perfil enxuto)."""
    espera = int(os.getenv("FILA_ESPERA_SEGUNDOS", "5"))
    logger.info("aguardando mensagens em '%s' (redis)", fila.FILA_INDEXACAO)
    while True:
        try:
            item = fila.cliente_redis().blpop(fila.FILA_INDEXACAO, timeout=espera)
        except Exception as erro:
            logger.warning("redis indisponivel (%s); nova tentativa em 3s", type(erro).__name__)
            time.sleep(3)
            continue
        if item is None:
            continue
        bruto = item[1]
        try:
            envelope = json.loads(bruto)
            ok = tratar_mensagem(envelope.get("corpo"), envelope.get("headers"))
        except Exception as erro:
            # Falha ao gravar: devolve a mensagem para o inicio da fila.
            logger.warning("falha ao indexar (%s); mensagem devolvida", type(erro).__name__)
            fila.cliente_redis().lpush(fila.FILA_INDEXACAO, bruto)
            time.sleep(3)
            continue
        if not ok:
            fila.cliente_redis().rpush(fila.FILA_FALHAS, bruto)


def main():
    indexar_documentos_iniciais()
    if fila.usa_redis():
        consumir_redis()
        return
    while True:
        conexao = None
        try:
            conexao = fila.conectar()
            canal = conexao.channel()
            fila.declarar_filas(canal)
            canal.basic_qos(prefetch_count=1)
            canal.basic_consume(queue=fila.FILA_INDEXACAO, on_message_callback=processar)
            logger.info("aguardando mensagens em '%s'", fila.FILA_INDEXACAO)
            canal.start_consuming()
        except Exception as erro:
            # Broker fora do ar ou erro ao gravar o indice. No segundo caso a
            # mensagem ficou sem ack e sera reentregue apos a reconexao.
            logger.warning("falha (%s); reconectando em 3s", type(erro).__name__)
        finally:
            if conexao is not None and conexao.is_open:
                try:
                    conexao.close()
                except pika.exceptions.AMQPError:
                    pass
        time.sleep(3)


if __name__ == "__main__":
    main()
