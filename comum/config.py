"""Leitura de configuracao comum aos servicos."""
import os


def url_servico(variavel: str, padrao: str) -> str:
    """URL de um servico interno, lida do ambiente.

    Aceita tanto "http://host:porta" quanto so "host:porta" (formato que o
    Render entrega em `fromService ... property: hostport`).
    """
    valor = (os.getenv(variavel) or padrao).strip().rstrip("/")
    return valor if "://" in valor else f"http://{valor}"
