"""
Plataforma inteira num unico processo/container (Dockerfile da raiz).

Existe porque o plano Free do Railway aceita no maximo 3 servicos por projeto
(e 0,5 GB de RAM por servico). Os servicos continuam separados e conversando
pelos mesmos protocolos da versao com um container por servico (docker-compose):

  publico  0.0.0.0:$PORT      gateway (JWT, rate limit, resiliencia)
  interno  127.0.0.1:8001     /inferencia  /ingestao  /recuperacao  /geracao  (REST)
  interno  127.0.0.1:50051    inferencia-grpc (Prever / PreverLote)
  threads                     worker de inferencia e worker de indexacao
  externo  Redis              fila de inferencia, resultados, fila de indexacao
                              e indice vetorial

Os servicos internos so escutam em 127.0.0.1: nada alem do gateway fica exposto.
O codigo de cada servico e o mesmo dos containers separados; so o processo e
compartilhado. Rodar: python plataforma.py  (precisa de REDIS_URL e dos segredos).
"""
import importlib.util
import logging
import os
import sys
import threading
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
SERVICOS = RAIZ / "servicos"
INTERNO = "http://127.0.0.1:8001"

# ---------------------------------------------------------------- configuracao
# Definida ANTES de importar os servicos, que leem o ambiente na importacao.
_redis = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
for chave, valor in {
    "URL_INFERENCIA": f"{INTERNO}/inferencia",
    "URL_INGESTAO": f"{INTERNO}/ingestao",
    "URL_RECUPERACAO": f"{INTERNO}/recuperacao",
    "URL_GERACAO": f"{INTERNO}/geracao",
    "GRPC_INFERENCIA": "127.0.0.1:50051",
    "GRPC_PORTA": "50051",
    "INDICE_REDIS_URL": _redis,          # indice vetorial no Redis
    "FILA_INDEXACAO_REDIS_URL": _redis,  # fila de indexacao no Redis (sem RabbitMQ)
    # BLPOP sem timeout: sem trafego periodico, o container pode hibernar no Free.
    "FILA_ESPERA_SEGUNDOS": "0",
}.items():
    os.environ.setdefault(chave, valor)

for pasta in ("gateway", "inferencia", "worker", "ingestion-service",
              "retrieval-service", "generation-service"):
    sys.path.insert(0, str(SERVICOS / pasta))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("plataforma")

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402


def carregar_app(pasta: str, nome: str):
    """Importa servicos/<pasta>/app.py com nome unico (todos se chamam app.py)."""
    spec = importlib.util.spec_from_file_location(nome, SERVICOS / pasta / "app.py")
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[nome] = modulo
    spec.loader.exec_module(modulo)
    return modulo.app


def em_segundo_plano(nome: str, alvo, *args) -> None:
    """Roda um laco (worker, servidor) numa thread e o reinicia se cair."""
    def executar():
        while True:
            try:
                alvo(*args)
            except Exception:
                logger.exception("%s caiu; reiniciando em 3s", nome)
            time.sleep(3)

    threading.Thread(target=executar, name=nome, daemon=True).start()


def main() -> None:
    gateway = carregar_app("gateway", "gateway_app")

    interno = FastAPI(title="Servicos internos (127.0.0.1)")
    interno.mount("/inferencia", carregar_app("inferencia", "inferencia_app"))
    interno.mount("/ingestao", carregar_app("ingestion-service", "ingestao_app"))
    interno.mount("/recuperacao", carregar_app("retrieval-service", "recuperacao_app"))
    interno.mount("/geracao", carregar_app("generation-service", "geracao_app"))

    import servidor_grpc
    import worker
    import worker_indexacao

    em_segundo_plano("inferencia-grpc", servidor_grpc.servir, int(os.environ["GRPC_PORTA"]))
    em_segundo_plano("worker", worker.main)
    em_segundo_plano("worker-indexacao", worker_indexacao.main)
    # Fora da thread principal o uvicorn nao instala tratadores de sinal.
    em_segundo_plano("servicos-internos", uvicorn.Server(uvicorn.Config(
        interno, host="127.0.0.1", port=8001, access_log=False, log_level="warning",
    )).run)

    porta = int(os.environ.get("PORT", "8000"))
    logger.info("gateway publico na porta %d; servicos internos em 127.0.0.1", porta)
    # --no-access-log e --proxy-headers, como no container do gateway.
    uvicorn.run(gateway, host="0.0.0.0", port=porta, access_log=False,
                proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
