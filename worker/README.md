# Worker de fila

Processo que consome a fila `tarefas` do Redis, roda o classificador de sentimento e grava o resultado
com expiração. O classificador fica em [comum/modelo.py](../comum/modelo.py), é treinado durante o build
e é o mesmo usado pelo servidor gRPC.

- Falha na inferência: reenfileira até `WORKER_MAX_TENTATIVAS` (padrão 3) e depois move para `tarefas:descarte`.
- Cada tarefa gera o span `worker.processar`, filho do trace iniciado no gateway: o contexto viaja dentro da mensagem.
- Escala horizontalmente: `docker compose up -d --scale worker=3`.
