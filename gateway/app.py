"""
API Gateway - porta de entrada unica da plataforma.

Rotas publicas:   GET /saude, POST /login, GET /metricas (pagina do painel)
Rotas com JWT:    POST /predict, GET /resultado/{id}      -> inferencia (REST + fila + worker)
                  POST /predict-sync, POST /predict-lote  -> inferencia (gRPC, sincrono)
                  POST /documentos, POST /buscar,
                  POST /perguntar                         -> RAG (ingestao/recuperacao/geracao)
                  GET  /metricas/dados

Seguranca: JWT (TAREFA 1) e rate limit por cliente (TAREFA 2).
Resiliencia (TAREFA 6): tempo-limite, retentativa com espera progressiva e um
circuit breaker por servico de destino.
Observabilidade (TAREFA 7): spans OpenTelemetry, propagados aos servicos.

Rodar: uvicorn app:app --port 8000
"""
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated

import grpc
import jwt
import requests
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from opentelemetry.instrumentation.grpc import GrpcInstrumentorClient
from pydantic import BaseModel, Field

import inferencia_pb2
import inferencia_pb2_grpc
from comum import telemetria
from comum.circuit_breaker import CircuitBreaker, CircuitoAberto
from comum.config import url_servico


def _obrigatoria(nome: str) -> str:
    valor = os.getenv(nome)
    if not valor:
        raise RuntimeError(f"variavel de ambiente {nome} nao definida (veja .env.example)")
    return valor


JWT_SEGREDO = _obrigatoria("JWT_SEGREDO")
JWT_ALGORITMO = "HS256"
JWT_EXPIRACAO_MINUTOS = int(os.getenv("JWT_EXPIRACAO_MINUTOS", "60"))

USUARIO = os.getenv("GATEWAY_USUARIO", "admin")
SENHA = _obrigatoria("GATEWAY_SENHA")
RATE_LIMIT_POR_MINUTO = int(os.getenv("RATE_LIMIT_POR_MINUTO", "30"))

SERVICOS = {
    "inferencia": url_servico("URL_INFERENCIA", "http://localhost:8001"),
    "ingestao": url_servico("URL_INGESTAO", "http://localhost:8002"),
    "recuperacao": url_servico("URL_RECUPERACAO", "http://localhost:8003"),
    "geracao": url_servico("URL_GERACAO", "http://localhost:8004"),
}
TIMEOUT_PADRAO = float(os.getenv("TIMEOUT_SERVICOS", "5"))
# A geracao espera a recuperacao e o LLM: precisa de mais folga.
TIMEOUTS = {"geracao": float(os.getenv("TIMEOUT_GERACAO", "30"))}
TENTATIVAS = int(os.getenv("TENTATIVAS_SERVICOS", "3"))
ESPERA_BASE_SEGUNDOS = float(os.getenv("ESPERA_BASE_SEGUNDOS", "0.5"))
STATUS_TRANSITORIOS = {502, 503, 504}

# Endereco gRPC da inferencia sincrona, no formato host:porta.
GRPC_INFERENCIA = os.getenv("GRPC_INFERENCIA", "localhost:50051")
CODIGOS_GRPC_TRANSITORIOS = {
    grpc.StatusCode.UNAVAILABLE,
    grpc.StatusCode.DEADLINE_EXCEEDED,
    grpc.StatusCode.RESOURCE_EXHAUSTED,
}

disjuntores = {
    nome: CircuitBreaker(
        limite_falhas=int(os.getenv("CB_LIMITE_FALHAS", "5")),
        espera_segundos=float(os.getenv("CB_ESPERA_SEGUNDOS", "30")),
    )
    for nome in [*SERVICOS, "inferencia-grpc"]
}

app = FastAPI(title="Gateway - C3.A2")
telemetria.configurar("gateway", app)
# Instrumentar ANTES de abrir o canal, para propagar o trace nas chamadas gRPC.
GrpcInstrumentorClient().instrument()
stub_inferencia = inferencia_pb2_grpc.InferenciaStub(grpc.insecure_channel(GRPC_INFERENCIA))

# Horarios das requisicoes por cliente (em memoria, so os ultimos 60 s).
historico_requisicoes: dict[str, list[float]] = defaultdict(list)
metricas = {"total": 0, "erros": 0, "tempo_total_ms": 0.0}


class Entrada(BaseModel):
    texto: str = Field(min_length=1, max_length=5000)


TextoValido = Annotated[str, Field(min_length=1, max_length=5000)]


class Lote(BaseModel):
    textos: list[TextoValido] = Field(min_length=1, max_length=100)


class Documento(BaseModel):
    texto: str = Field(min_length=1, max_length=20000)
    origem: str = Field("desconhecida", max_length=200)


class Busca(BaseModel):
    texto: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(3, ge=1, le=20)


class Pergunta(BaseModel):
    pergunta: str = Field(min_length=1, max_length=2000)


class CredenciaisLogin(BaseModel):
    usuario: str
    senha: str


# ---------------------------------------------------------------- seguranca
def criar_token_jwt(usuario: str) -> str:
    agora = datetime.now(timezone.utc)
    payload = {
        "sub": usuario,
        "iat": int(agora.timestamp()),
        "exp": int((agora + timedelta(minutes=JWT_EXPIRACAO_MINUTOS)).timestamp()),
    }
    return jwt.encode(payload, JWT_SEGREDO, algorithm=JWT_ALGORITMO)


def exigir_jwt(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Token JWT ausente ou formato invalido (use Bearer <token>)",
        )
    token = authorization.removeprefix("Bearer ").strip()
    try:
        return jwt.decode(token, JWT_SEGREDO, algorithms=[JWT_ALGORITMO])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token JWT expirado") from None
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token JWT invalido") from None


def cliente_autorizado(request: Request, usuario: dict = Depends(exigir_jwt)) -> dict:
    """JWT valido + rate limit por cliente (janela deslizante de 60 s)."""
    cliente_id = usuario.get("sub") or (request.client.host if request.client else "anonimo")
    agora = time.time()
    recentes = [t for t in historico_requisicoes[cliente_id] if t > agora - 60.0]
    if len(recentes) >= RATE_LIMIT_POR_MINUTO:
        historico_requisicoes[cliente_id] = recentes
        raise HTTPException(
            status_code=429,
            detail=f"Limite de {RATE_LIMIT_POR_MINUTO} requisicoes por minuto excedido",
            headers={"Retry-After": str(int(60 - (agora - recentes[0])) + 1)},
        )
    recentes.append(agora)
    historico_requisicoes[cliente_id] = recentes
    return usuario


# -------------------------------------------------------------- resiliencia
def requisitar_com_retentativa(metodo: str, url: str, timeout: float,
                               payload=None) -> requests.Response:
    """Repete so falhas transitorias, com espera de 0,5s, 1s, 2s..."""
    for tentativa in range(TENTATIVAS):
        try:
            r = requests.request(metodo, url, json=payload, timeout=timeout)
            if r.status_code >= 500:
                r.raise_for_status()
            return r
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as erro:
            transitorio = not isinstance(erro, requests.HTTPError) or (
                erro.response is not None
                and erro.response.status_code in STATUS_TRANSITORIOS
            )
            if not transitorio or tentativa == TENTATIVAS - 1:
                raise
            time.sleep(ESPERA_BASE_SEGUNDOS * 2 ** tentativa)


def encaminhar(servico: str, metodo: str, caminho: str, payload=None) -> JSONResponse:
    """Chama um servico interno protegido pelo circuit breaker dele."""
    url = f"{SERVICOS[servico]}{caminho}"
    timeout = TIMEOUTS.get(servico, TIMEOUT_PADRAO)
    try:
        r = disjuntores[servico].chamar(
            requisitar_com_retentativa, metodo, url, timeout, payload
        )
    except CircuitoAberto as e:
        raise HTTPException(
            status_code=503,
            detail=f"{servico} indisponivel: {e}",
            headers={"Retry-After": str(int(disjuntores[servico].espera_segundos))},
        ) from None
    except requests.Timeout:
        raise HTTPException(status_code=504,
                            detail=f"{servico}: tempo-limite excedido") from None
    except requests.RequestException:
        raise HTTPException(status_code=503, detail=f"{servico} indisponivel") from None

    try:
        corpo = r.json()
    except ValueError:
        corpo = {"detail": r.text}
    return JSONResponse(status_code=r.status_code, content=corpo)


def grpc_com_retentativa(metodo, pedido):
    """Mesma politica do REST: repete so falhas transitorias, com espera progressiva.
    Erros do cliente (ex.: INVALID_ARGUMENT) sao devolvidos, nao levantados, para
    nao contarem como falha do servico no circuit breaker."""
    for tentativa in range(TENTATIVAS):
        try:
            return metodo(pedido, timeout=TIMEOUT_PADRAO)
        except grpc.RpcError as erro:
            if erro.code() not in CODIGOS_GRPC_TRANSITORIOS:
                return erro
            if tentativa == TENTATIVAS - 1:
                raise
            time.sleep(ESPERA_BASE_SEGUNDOS * 2 ** tentativa)


def chamar_grpc(nome_metodo: str, pedido):
    """Chama a inferencia por gRPC protegida pelo circuit breaker dela."""
    disjuntor = disjuntores["inferencia-grpc"]
    try:
        resposta = disjuntor.chamar(
            grpc_com_retentativa, getattr(stub_inferencia, nome_metodo), pedido
        )
    except CircuitoAberto as e:
        raise HTTPException(
            status_code=503,
            detail=f"inferencia-grpc indisponivel: {e}",
            headers={"Retry-After": str(int(disjuntor.espera_segundos))},
        ) from None
    except grpc.RpcError as e:
        if e.code() == grpc.StatusCode.DEADLINE_EXCEEDED:
            raise HTTPException(status_code=504,
                                detail="inferencia-grpc: tempo-limite excedido") from None
        raise HTTPException(status_code=503, detail="inferencia-grpc indisponivel") from None
    if isinstance(resposta, grpc.RpcError):
        status = 400 if resposta.code() == grpc.StatusCode.INVALID_ARGUMENT else 502
        raise HTTPException(status_code=status, detail=resposta.details())
    return resposta


def _sentimento(r) -> dict:
    return {"sentimento": r.sentimento, "confianca": round(r.confianca, 4)}


# -------------------------------------------------------------------- rotas
@app.middleware("http")
async def registrar(request: Request, call_next):
    inicio = time.perf_counter()
    resposta = await call_next(request)
    ms = (time.perf_counter() - inicio) * 1000
    if not request.url.path.startswith("/metricas"):
        metricas["total"] += 1
        metricas["erros"] += int(resposta.status_code >= 400)
        metricas["tempo_total_ms"] += ms
    # So metodo, rota, status e tempo: nada de IP, texto, senha ou token (LGPD).
    print(f"[gateway] {request.method} {request.url.path} "
          f"-> {resposta.status_code} ({ms:.1f}ms)", flush=True)
    return resposta


@app.get("/saude")
def saude():
    return {"status": "ok",
            "disjuntores": {nome: d.estado for nome, d in disjuntores.items()}}


@app.post("/login")
def login(credenciais: CredenciaisLogin):
    if credenciais.usuario != USUARIO or credenciais.senha != SENHA:
        raise HTTPException(status_code=401, detail="Credenciais invalidas")
    return {
        "access_token": criar_token_jwt(credenciais.usuario),
        "token_type": "bearer",
        "expires_in": JWT_EXPIRACAO_MINUTOS * 60,
    }


@app.post("/predict", status_code=202)
def predict(entrada: Entrada, _: dict = Depends(cliente_autorizado)):
    """Enfileira a classificacao de sentimento; consulte em /resultado/{id}."""
    return encaminhar("inferencia", "POST", "/predict", entrada.model_dump())


@app.post("/predict-sync")
def predict_sync(entrada: Entrada, _: dict = Depends(cliente_autorizado)):
    """Classificacao sincrona via gRPC (Prever): mesmo modelo do worker."""
    r = chamar_grpc("Prever", inferencia_pb2.PedidoPrever(texto=entrada.texto))
    return _sentimento(r)


@app.post("/predict-lote")
def predict_lote(lote: Lote, _: dict = Depends(cliente_autorizado)):
    """Classifica varios textos numa unica chamada gRPC (PreverLote)."""
    r = chamar_grpc("PreverLote", inferencia_pb2.PedidoLote(textos=lote.textos))
    return {"resultados": [_sentimento(item) for item in r.resultados]}


@app.get("/resultado/{tarefa_id}")
def resultado(tarefa_id: str, _: dict = Depends(cliente_autorizado)):
    return encaminhar("inferencia", "GET", f"/resultado/{tarefa_id}")


@app.post("/documentos", status_code=202)
def documentos(docs: Annotated[list[Documento], Body(min_length=1, max_length=100)],
               _: dict = Depends(cliente_autorizado)):
    """Indexa documentos no armazenamento vetorial (assincrono, via fila)."""
    return encaminhar("ingestao", "POST", "/indexar", [d.model_dump() for d in docs])


@app.post("/buscar")
def buscar(busca: Busca, _: dict = Depends(cliente_autorizado)):
    """Busca por similaridade no armazenamento vetorial."""
    return encaminhar("recuperacao", "POST", "/recuperar", busca.model_dump())


@app.post("/perguntar")
def perguntar(pergunta: Pergunta, _: dict = Depends(cliente_autorizado)):
    """RAG: recupera o contexto e gera a resposta com o LLM."""
    return encaminhar("geracao", "POST", "/perguntar", pergunta.model_dump())


@app.get("/metricas", response_class=HTMLResponse)
def painel_metricas():
    return Path(__file__).with_name("metricas.html").read_text(encoding="utf-8")


@app.get("/metricas/dados")
def dados_metricas(_: dict = Depends(cliente_autorizado)):
    total = metricas["total"]
    return {
        "total": total,
        "erros": metricas["erros"],
        "tempo_medio_ms": round(metricas["tempo_total_ms"] / total, 2) if total else 0,
    }
