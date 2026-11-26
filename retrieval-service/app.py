"""
Servico de RECUPERACAO: recebe uma pergunta e busca
no indice vetorial os trechos mais parecidos (similaridade do cosseno).

Rodar: uvicorn app:app --port 8003
"""
import os

import numpy as np
import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sklearn.metrics.pairwise import cosine_similarity

from comum import telemetria
from comum.embeddings import Vetorizador

app = FastAPI(title="Recuperacao - C3.A2")
tracer = telemetria.configurar("recuperacao", app)

URL_INGESTAO = os.getenv("URL_INGESTAO", "http://localhost:8002").rstrip("/")
TIMEOUT_INGESTAO = float(os.getenv("TIMEOUT_INGESTAO", "10"))


class Pergunta(BaseModel):
    texto: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(3, ge=1, le=20)


@app.post("/recuperar")
def recuperar(pergunta: Pergunta):
    """Busca os documentos mais relevantes para a pergunta."""
    try:
        resposta = requests.get(f"{URL_INGESTAO}/indice", timeout=TIMEOUT_INGESTAO)
        resposta.raise_for_status()
        itens = resposta.json().get("itens", [])
    except requests.Timeout:
        raise HTTPException(status_code=504,
                            detail="Tempo-limite da ingestao excedido.") from None
    except (requests.RequestException, ValueError):
        raise HTTPException(status_code=502,
                            detail="Falha ao consultar o indice da ingestao.") from None

    if not itens:
        return {"pergunta": pergunta.texto, "resultados": []}

    with tracer.start_as_current_span("indice.similaridade") as span:
        span.set_attribute("indice.total", len(itens))
        textos = [item["texto"] for item in itens]
        origens = [item["origem"] for item in itens]
        matriz = np.array([item["vetor"] for item in itens])

        # Instancia local: um vetorizador global seria reajustado por
        # requisicoes concorrentes. Mesmo vocabulario usado na indexacao.
        vetorizador = Vetorizador()
        vetorizador.ajustar(textos)
        vetor_pergunta = np.array(vetorizador.vetorizar(pergunta.texto)).reshape(1, -1)

        if matriz.size == 0 or vetor_pergunta.size == 0:
            return {"pergunta": pergunta.texto, "resultados": []}

        similaridades = cosine_similarity(matriz, vetor_pergunta).flatten()
        ordem = np.argsort(similaridades)[::-1]

    # Documentos sem nenhuma similaridade nao fundamentam a resposta.
    resultados = []
    for idx in ordem[:pergunta.top_k]:
        if similaridades[idx] <= 0:
            break
        resultados.append({"texto": textos[idx], "origem": origens[idx],
                           "pontuacao": round(float(similaridades[idx]), 4)})
    return {"pergunta": pergunta.texto, "resultados": resultados}


@app.get("/saude")
def saude():
    return {"status": "ok"}
