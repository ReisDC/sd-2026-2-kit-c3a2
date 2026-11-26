# Worker de fila

Processo que consome a fila `tarefas` do Redis, roda o classificador de sentimento
([modelo.py](modelo.py), treinado durante o build da imagem) e grava o resultado com expiração.

- Falha na inferência: reenfileira até `WORKER_MAX_TENTATIVAS` (padrão 3) e depois move para `tarefas:descarte`.
- Cada tarefa gera o span `worker.processar`, filho do trace iniciado no gateway: o contexto viaja dentro da mensagem.
- Escala horizontalmente: `docker compose up -d --scale worker=3`.
