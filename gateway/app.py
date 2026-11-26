"""
API Gateway - porta de entrada unica da plataforma.

Rotas publicas:   GET /saude, POST /login, GET /metricas (pagina do painel)
Rotas com JWT:    POST /predict, GET /resultado/{id}      -> inferencia (fila + worker)
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

import jwt
import requests
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from comum import telemetria
from comum.circuit_breaker import CircuitBreaker, CircuitoAberto


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
    "inferencia": os.getenv("URL_INFERENCIA", "http://localhost:8001"),
    "ingestao": os.getenv("URL_INGESTAO", "http://localhost:8002"),
    "recuperacao": os.getenv("URL_RECUPERACAO", "http://localhost:8003"),
    "geracao": os.getenv("URL_GERACAO", "http://localhost:8004"),
}
TIMEOUT_PADRAO = float(os.getenv("TIMEOUT_SERVICOS", "5"))
# A geracao espera a recuperacao e o LLM: precisa de mais folga.
TIMEOUTS = {"geracao": float(os.getenv("TIMEOUT_GERACAO", "30"))}
TENTATIVAS = int(os.getenv("TENTATIVAS_SERVICOS", "3"))
ESPERA_BASE_SEGUNDOS = float(os.getenv("ESPERA_BASE_SEGUNDOS", "0.5"))
STATUS_TRANSITORIOS = {502, 503, 504}

disjuntores = {
    nome: CircuitBreaker(
        limite_falhas=int(os.getenv("CB_LIMITE_FALHAS", "5")),
        espera_segundos=float(os.getenv("CB_ESPERA_SEGUNDOS", "30")),
    )
    for nome in SERVICOS
}

app = FastAPI(title="Gateway - C3.A2")
telemetria.configurar("gateway", app)

# Horarios das requisicoes por cliente (em memoria, so os ultimos 60 s).
historico_requisicoes: dict[str, list[float]] = defaultdict(list)
metricas = {"total": 0, "erros": 0, "tempo_total_ms": 0.0}


class Entrada(BaseModel):
    texto: str = Field(min_length=1, max_length=5000)


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
    url = f"{SERVICOS[servico].rstrip('/')}{caminho}"
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
