from pydantic import BaseModel, Field


class Documento(BaseModel):
    texto: str = Field(min_length=1, max_length=20000)
    origem: str = Field("desconhecida", max_length=200)
