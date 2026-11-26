# Serviço de inferência

API REST (FastAPI, porta 8001) que recebe o texto e o enfileira no Redis sem esperar a inferência.

- `POST /predict` → `202 {"id": ..., "status": "na_fila"}`
- `GET /resultado/{id}` → `na_fila`, `pronto` (com `sentimento` e `confianca`) ou `erro`; 404 se o id não existir
- `GET /saude` → estado e tamanho da fila

Quem roda o modelo é o [worker](../worker/). A fila fica em [comum/fila_tarefas.py](../comum/fila_tarefas.py).
Na produção, só o gateway chama este serviço.
