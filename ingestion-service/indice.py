"""
Armazenamento vetorial: o texto, a origem e o vetor (embedding) de cada documento.

Dois backends, escolhidos por variavel de ambiente:
  - arquivo JSON (INDICE_ARQUIVO), num volume compartilhado - docker-compose;
  - Redis (INDICE_REDIS_URL) - nuvens em que um volume nao pode ser montado em
    dois servicos (Railway, Render).

O worker de indexacao e o unico escritor; a API de ingestao so le.
"""
import json
import os

from comum.embeddings import Vetorizador

ARQUIVO = os.getenv("INDICE_ARQUIVO", "indice.json")
REDIS_URL = os.getenv("INDICE_REDIS_URL", "")
CHAVE = "indice:vetorial"
CHAVE_VERSAO = "indice:vetorial:versao"

_redis = None


def _cliente():
    global _redis
    if _redis is None:
        import redis

        _redis = redis.from_url(REDIS_URL, decode_responses=True)
    return _redis


def assinatura():
    """Muda sempre que o indice e regravado (None se ainda nao existe).
    Permite a API manter o indice em cache e so recarregar quando precisa."""
    if REDIS_URL:
        return _cliente().get(CHAVE_VERSAO)
    try:
        estado = os.stat(ARQUIVO)
    except FileNotFoundError:
        return None
    return (estado.st_mtime_ns, estado.st_size)


def carregar():
    """Le o indice. Conteudo corrompido gera erro em vez de indice vazio,
    para que uma gravacao seguinte nao apague os documentos ja indexados."""
    if REDIS_URL:
        bruto = _cliente().get(CHAVE)
        return json.loads(bruto) if bruto else []
    if not os.path.exists(ARQUIVO):
        return []
    with open(ARQUIVO, "r", encoding="utf-8") as f:
        return json.load(f)


def salvar(itens):
    """Escrita atomica: no Redis, indice e versao na mesma transacao; em disco,
    grava em arquivo temporario e substitui o original."""
    if REDIS_URL:
        with _cliente().pipeline(transaction=True) as transacao:
            transacao.set(CHAVE, json.dumps(itens, ensure_ascii=False))
            transacao.incr(CHAVE_VERSAO)
            transacao.execute()
        return
    temporario = f"{ARQUIVO}.tmp"
    with open(temporario, "w", encoding="utf-8") as f:
        json.dump(itens, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporario, ARQUIVO)


def adicionar(itens, novos):
    """Acrescenta documentos e recalcula todos os vetores com o vocabulario novo.
    Documentos ja presentes (mesmo texto e origem) sao ignorados, entao
    reprocessar a mesma mensagem nao duplica o indice."""
    existentes = {(i["texto"], i["origem"]) for i in itens}
    todos = [{"texto": i["texto"], "origem": i["origem"]} for i in itens]
    for doc in novos:
        chave = (doc["texto"], doc["origem"])
        if chave not in existentes:
            existentes.add(chave)
            todos.append({"texto": doc["texto"], "origem": doc["origem"]})

    if not todos:
        return []
    vetorizador = Vetorizador()
    vetorizador.ajustar([i["texto"] for i in todos])
    for item in todos:
        item["vetor"] = vetorizador.vetorizar(item["texto"])
    return todos
