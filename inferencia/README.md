# Serviço de inferência

Uma imagem, dois processos (no compose: `inferencia` e `inferencia-grpc`).

**REST (FastAPI, porta 8001):** assíncrono, enfileira o texto no Redis sem esperar a inferência.

- `POST /predict` → `202 {"id": ..., "status": "na_fila"}`
- `GET /resultado/{id}` → `na_fila`, `pronto` (com `sentimento` e `confianca`) ou `erro`; 404 se o id não existir
- `GET /saude` → estado e tamanho da fila

**gRPC (porta 50051, [servidor_grpc.py](servidor_grpc.py)):** síncrono, contrato em
[proto/inferencia.proto](../proto/inferencia.proto).

- `Prever(PedidoPrever) → RespostaPrever`
- `PreverLote(PedidoLote) → RespostaLote` (até 100 textos)
- Entrada inválida → `INVALID_ARGUMENT`

O modelo ([comum/modelo.py](../comum/modelo.py)) é o mesmo do [worker](../worker/) e é treinado no build
das duas imagens. Por isso as duas vias devolvem o mesmo resultado para a mesma entrada. Os stubs gRPC
também são gerados no build. Em produção, só o gateway chama este serviço.
