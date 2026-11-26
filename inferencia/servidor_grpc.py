"""
Interface gRPC do servico de inferencia (contrato em proto/inferencia.proto).

Via SINCRONA: chama o modelo direto, sem fila. Usa o mesmo modelo do worker
(comum/modelo.py, treinado no build), entao REST (via fila) e gRPC devolvem o
mesmo resultado para a mesma entrada.

Rodar: python servidor_grpc.py   (porta em GRPC_PORTA, padrao 50051)
"""
import logging
import os
import time
import uuid
from concurrent import futures

import grpc
from opentelemetry import trace
from opentelemetry.instrumentation.grpc import GrpcInstrumentorServer

import inferencia_pb2
import inferencia_pb2_grpc
from comum import telemetria
from comum.modelo import carregar_modelo

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("inferencia-grpc")
tracer = telemetria.configurar("inferencia-grpc")

LIMITE_TEXTO = 5000
LIMITE_LOTE = 100


def _resposta(r: dict) -> inferencia_pb2.RespostaPrever:
    return inferencia_pb2.RespostaPrever(
        texto=r["texto"], sentimento=r["sentimento"], confianca=r["confianca"]
    )


def _validar(texto: str, context) -> None:
    if not texto.strip():
        context.abort(grpc.StatusCode.INVALID_ARGUMENT, "texto vazio")
    if len(texto) > LIMITE_TEXTO:
        context.abort(grpc.StatusCode.INVALID_ARGUMENT,
                      f"texto maior que {LIMITE_TEXTO} caracteres")


class ServicoInferencia(inferencia_pb2_grpc.InferenciaServicer):

    def __init__(self):
        logger.info("carregando modelo...")
        self.modelo = carregar_modelo()
        logger.info("modelo pronto")

    def Prever(self, request, context):
        requisicao_id = str(uuid.uuid4())
        inicio = time.perf_counter()
        _validar(request.texto, context)
        with tracer.start_as_current_span("modelo.prever"):
            r = self.modelo.prever(request.texto)
        logger.info("id=%s metodo=Prever tamanho_entrada=%d status=OK tempo_ms=%.1f",
                    requisicao_id, len(request.texto),
                    (time.perf_counter() - inicio) * 1000)
        return _resposta(r)

    def PreverLote(self, request, context):
        requisicao_id = str(uuid.uuid4())
        inicio = time.perf_counter()
        if not request.textos:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "lote vazio")
        if len(request.textos) > LIMITE_LOTE:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT,
                          f"lote maior que {LIMITE_LOTE} textos")
        for texto in request.textos:
            _validar(texto, context)
        with tracer.start_as_current_span("modelo.prever_lote") as span:
            span.set_attribute("lote.quantidade", len(request.textos))
            respostas = [_resposta(self.modelo.prever(t)) for t in request.textos]
        logger.info(
            "id=%s metodo=PreverLote qtd_textos=%d tamanho_entrada=%d status=OK tempo_ms=%.1f",
            requisicao_id, len(request.textos), sum(len(t) for t in request.textos),
            (time.perf_counter() - inicio) * 1000,
        )
        return inferencia_pb2.RespostaLote(resultados=respostas)


def servir(porta: int) -> None:
    GrpcInstrumentorServer().instrument()
    servidor = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    inferencia_pb2_grpc.add_InferenciaServicer_to_server(ServicoInferencia(), servidor)
    servidor.add_insecure_port(f"[::]:{porta}")
    servidor.start()
    logger.info("gRPC escutando na porta %d", porta)
    servidor.wait_for_termination()


if __name__ == "__main__":
    servir(int(os.getenv("GRPC_PORTA", "50051")))
