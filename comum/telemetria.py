"""
Rastreamento distribuido com OpenTelemetry (TAREFA 7).

Cada servico chama configurar("nome") na subida. Os spans sao exportados por
OTLP/HTTP para o endereco em OTEL_EXPORTER_OTLP_ENDPOINT (o Jaeger no
docker-compose). Sem essa variavel, os spans continuam sendo criados e o
contexto continua sendo propagado, mas nada e exportado.

HTTP: as instrumentacoes do FastAPI e do requests propagam o cabecalho
`traceparent` sozinhas. Filas: injetar()/extrair() levam o mesmo contexto
dentro da mensagem (Redis) ou dos headers (RabbitMQ), ligando
gateway -> servico -> fila -> worker num unico trace.

LGPD: nenhum span recebe o texto enviado pelo cliente; so ids e tamanhos.
"""
import os

from opentelemetry import propagate, trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

_configurado = False


def configurar(nome_servico: str, app=None) -> trace.Tracer:
    """Configura o provedor de traces (uma vez) e instrumenta FastAPI/requests."""
    global _configurado
    if not _configurado:
        nome = os.getenv("OTEL_SERVICE_NAME", nome_servico)
        provedor = TracerProvider(resource=Resource.create({"service.name": nome}))
        if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )

            provedor.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        trace.set_tracer_provider(provedor)

        from opentelemetry.instrumentation.requests import RequestsInstrumentor

        RequestsInstrumentor().instrument()
        _configurado = True

    if app is not None:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app, excluded_urls="saude,metricas")
    return trace.get_tracer(nome_servico)


def injetar() -> dict:
    """Serializa o contexto do span atual para viajar dentro de uma mensagem."""
    portador: dict = {}
    propagate.inject(portador)
    return portador


def extrair(portador: dict | None):
    """Reconstroi o contexto recebido numa mensagem (vazio se nao houver)."""
    return propagate.extract(portador or {})
