"""
Log de cada requisicao HTTP recebida, igual em todos os servicos.

Registra so metadados: id da requisicao, metodo, rota, tamanho da entrada,
status e tempo. Nunca o corpo (texto do cliente), o token nem o IP (LGPD) -
por isso o log de acesso do uvicorn fica desligado (--no-access-log).
"""
import logging
import time
import uuid

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")


def registrar_requisicoes(app, nome: str) -> logging.Logger:
    logger = logging.getLogger(nome)

    @app.middleware("http")
    async def _log_requisicao(request, call_next):
        requisicao_id = str(uuid.uuid4())
        inicio = time.perf_counter()
        try:
            resposta = await call_next(request)
        except Exception:
            logger.exception("id=%s metodo=%s rota=%s - erro nao tratado",
                             requisicao_id, request.method, request.url.path)
            raise
        logger.info(
            "id=%s metodo=%s rota=%s tamanho_entrada=%sB status=%d tempo_ms=%.1f",
            requisicao_id, request.method, request.url.path,
            request.headers.get("content-length", "0"), resposta.status_code,
            (time.perf_counter() - inicio) * 1000,
        )
        resposta.headers["X-Request-ID"] = requisicao_id
        return resposta

    return logger
