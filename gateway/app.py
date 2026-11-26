"""
API Gateway - porta de entrada unica da plataforma.

JA PRONTO: roteamento para o servico de inferencia e log de requisicoes.
TAREFA 1: autenticacao por JWT.
TAREFA 2: limite de requisicoes por cliente (rate limit).
TAREFA 6: resiliencia (tempo-limite, retentativa com espera progressiva e circuit breaker).

Rodar: uvicorn app:app --port 8000
"""
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import requests
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from circuit_breaker import CircuitBreaker, CircuitoAberto

app = FastAPI(title="Gateway - C3.A2")


def _obrigatoria(nome: str) -> str:
    valor = os.getenv(nome)
    if not valor:
        raise RuntimeError(f"variavel de ambiente {nome} nao definida (veja .env.example)")
    return valor


URL_INFERENCIA = os.getenv("URL_INFERENCIA", "http://localhost:8001")
JWT_SEGREDO = _obrigatoria("JWT_SEGREDO")
JWT_ALGORITMO = "HS256"
JWT_EXPIRACAO_MINUTOS = int(os.getenv("JWT_EXPIRACAO_MINUTOS", "60"))

USUARIO = os.getenv("GATEWAY_USUARIO", "admin")
SENHA = _obrigatoria("GATEWAY_SENHA")
RATE_LIMIT_POR_MINUTO = int(os.getenv("RATE_LIMIT_POR_MINUTO", "30"))

TIMEOUT_INFERENCIA = float(os.getenv("TIMEOUT_INFERENCIA", "5"))
TENTATIVAS_INFERENCIA = int(os.getenv("TENTATIVAS_INFERENCIA", "3"))
ESPERA_BASE_SEGUNDOS = float(os.getenv("ESPERA_BASE_SEGUNDOS", "0.5"))
STATUS_TRANSITORIOS = {502, 503, 504}

disjuntor = CircuitBreaker(
    limite_falhas=int(os.getenv("CB_LIMITE_FALHAS", "5")),
    espera_segundos=float(os.getenv("CB_ESPERA_SEGUNDOS", "30")),
)

# Armazena timestamps das requisicoes por chave de cliente (em memoria)
historico_requisicoes: dict[str, list[float]] = defaultdict(list)
metricas = {"total": 0, "erros": 0, "tempo_total_ms": 0.0}


class Entrada(BaseModel):
    texto: str


class CredenciaisLogin(BaseModel):
    usuario: str
    senha: str


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


def post_com_retentativa(url: str, payload: dict) -> requests.Response:
    """Repete so falhas transitorias, com espera de 0,5s, 1s, 2s..."""
    for tentativa in range(TENTATIVAS_INFERENCIA):
        try:
            r = requests.post(url, json=payload, timeout=TIMEOUT_INFERENCIA)
            if r.status_code >= 500:
                r.raise_for_status()
            return r
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as erro:
            transitorio = not isinstance(erro, requests.HTTPError) or (
                erro.response is not None
                and erro.response.status_code in STATUS_TRANSITORIOS
            )
            if not transitorio or tentativa == TENTATIVAS_INFERENCIA - 1:
                raise
            time.sleep(ESPERA_BASE_SEGUNDOS * 2 ** tentativa)


@app.middleware("http")
async def registrar(request: Request, call_next):
    inicio = time.perf_counter()
    resposta = await call_next(request)
    ms = (time.perf_counter() - inicio) * 1000
    if request.url.path not in {"/metricas", "/metricas/dados"}:
        metricas["total"] += 1
        metricas["erros"] += int(resposta.status_code >= 400)
        metricas["tempo_total_ms"] += ms
    print(f"[gateway] {request.method} {request.url.path} "
          f"-> {resposta.status_code} ({ms:.1f}ms)")
    return resposta


@app.get("/saude")
def saude():
    return {"status": "ok"}


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


@app.post("/login")
def login(credenciais: CredenciaisLogin):
    if credenciais.usuario != USUARIO or credenciais.senha != SENHA:
        raise HTTPException(status_code=401, detail="Credenciais invalidas")
    token = criar_token_jwt(credenciais.usuario)
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": JWT_EXPIRACAO_MINUTOS * 60,
    }


@app.post("/predict")
def predict(entrada: Entrada, _: dict = Depends(cliente_autorizado)):
    try:
        r = disjuntor.chamar(
            post_com_retentativa, f"{URL_INFERENCIA}/predict", entrada.model_dump()
        )
    except CircuitoAberto as e:
        raise HTTPException(status_code=503, detail=str(e))
    except requests.RequestException as e:
        raise HTTPException(
            status_code=503,
            detail=f"servico de inferencia indisponivel: {e}",
        )
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text)
    return r.json()
