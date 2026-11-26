"""
Servico de GERACAO: busca o contexto na recuperacao,
monta o prompt e chama o LLM.

As duas chamadas tem circuit breaker proprio. Se o LLM falhar, responde com os
trechos recuperados (degradacao elegante) em vez de devolver erro.

Rodar: uvicorn app:app --port 8004
"""
import os

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

import llm
from comum import telemetria
from comum.circuit_breaker import CircuitBreaker, CircuitoAberto

app = FastAPI(title="Geracao - C3.A2")
tracer = telemetria.configurar("geracao", app)

URL_RECUPERACAO = os.getenv("URL_RECUPERACAO", "http://localhost:8003").rstrip("/")
TIMEOUT_RECUPERACAO = float(os.getenv("TIMEOUT_RECUPERACAO", "10"))

disjuntor_llm = CircuitBreaker(
    limite_falhas=int(os.getenv("CB_LIMITE_FALHAS", "3")),
    espera_segundos=float(os.getenv("CB_ESPERA_SEGUNDOS", "20")),
)
disjuntor_recuperacao = CircuitBreaker(
    limite_falhas=int(os.getenv("CB_LIMITE_FALHAS", "3")),
    espera_segundos=float(os.getenv("CB_ESPERA_SEGUNDOS", "20")),
)


class Pergunta(BaseModel):
    pergunta: str = Field(min_length=1, max_length=2000)


def recuperar_contexto(pergunta):
    r = requests.post(f"{URL_RECUPERACAO}/recuperar",
                      json={"texto": pergunta, "top_k": 3}, timeout=TIMEOUT_RECUPERACAO)
    r.raise_for_status()
    return [t["texto"] for t in r.json()["resultados"]]


@app.post("/perguntar")
def perguntar(p: Pergunta):
    try:
        contexto = disjuntor_recuperacao.chamar(recuperar_contexto, p.pergunta)
    except CircuitoAberto:
        raise HTTPException(
            status_code=503,
            detail="Recuperacao temporariamente indisponivel: circuito aberto.",
            headers={"Retry-After": str(int(disjuntor_recuperacao.espera_segundos))},
        ) from None
    except requests.Timeout:
        raise HTTPException(status_code=504,
                            detail="Tempo-limite da recuperacao excedido.") from None
    except (requests.RequestException, ValueError, KeyError, TypeError):
        raise HTTPException(status_code=502,
                            detail="Falha ao recuperar os documentos.") from None

    if not contexto:
        return {"pergunta": p.pergunta,
                "resposta": "Nenhum documento relevante encontrado para a pergunta.",
                "contexto": [], "degradado": False}

    with tracer.start_as_current_span("llm.gerar") as span:
        span.set_attribute("llm.simulado", llm.modo_simulado())
        span.set_attribute("llm.contexto.trechos", len(contexto))
        try:
            resposta = disjuntor_llm.chamar(llm.gerar, p.pergunta, contexto)
        except CircuitoAberto:
            return resposta_degradada(p.pergunta, contexto, "circuito_aberto")
        except requests.Timeout:
            return resposta_degradada(p.pergunta, contexto, "timeout")
        except (requests.RequestException, ValueError, KeyError, IndexError):
            return resposta_degradada(p.pergunta, contexto, "falha_llm")

    return {"pergunta": p.pergunta, "resposta": resposta, "contexto": contexto,
            "degradado": False}


def resposta_degradada(pergunta, contexto, motivo):
    return {"pergunta": pergunta,
            "resposta": "LLM indisponivel no momento. Trechos mais relevantes "
                        "encontrados nos documentos:\n" + "\n".join(contexto),
            "contexto": contexto, "degradado": True, "motivo": motivo}


@app.get("/saude")
def saude():
    return {"status": "ok", "llm_simulado": llm.modo_simulado(),
            "disjuntor_llm": disjuntor_llm.estado,
            "disjuntor_recuperacao": disjuntor_recuperacao.estado}
