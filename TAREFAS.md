# TAREFAS - Trabalho C3.A2 (Plataforma de IA como Servico)

Este e o projeto integrador. Reaproveite o que voce fez em C1.A2 e C2.A2.

## Nucleo obrigatorio

- [X] **1. Autenticacao JWT no gateway** - `gateway/app.py`
      Rota de login que emite o token e protecao das demais rotas (401 sem token). (Aula 16)

- [X] **2. Rate limit no gateway** - `gateway/app.py`
      Limitar requisicoes por cliente por minuto. (Aula 8)

- [X] **3. Dockerfile de cada servico**
      Um Dockerfile por servico. Use `Dockerfile.exemplo` como ponto de partida. (Aula 13)
      gateway, inferencia, worker, ingestion-service (API + worker de indexacao),
      retrieval-service e generation-service. Build com contexto na raiz (inclui `comum/`).

- [X] **4. docker-compose completo**
      O sistema inteiro deve subir com UM comando. (Aula 14)
      `docker compose up --build`: 11 containers (servicos + Redis, RabbitMQ e Jaeger).

- [X] **5. Deploy em nuvem (free tier)**
      Publicar e informar a URL publica no README. (Aula 13)
      https://plataforma-production-333fa9a0.up.railway.app (Railway, plano Free,
      IaC em `.railway/railway.ts`). Alternativa: Render (`render.yaml`).

- [X] **6. Resiliencia**
      Tempo-limite, retentativa com espera progressiva e circuit breaker. (Aula 11)

- [X] **7. Observabilidade**
      Rastreamento distribuido cobrindo gateway -> fila -> worker. (Aula 15)
      OpenTelemetry em todos os servicos + Jaeger (:16686); contexto propagado
      pela fila Redis e pelos headers do RabbitMQ (`comum/telemetria.py`).
      Em producao, a aba Traces do Railway mostra edge -> gateway -> fila -> worker.

- [X] **8. HTTPS** no servico publicado. (Aula 16)
      Certificado Let's Encrypt emitido pelo Railway; HTTP redireciona (301) para HTTPS.

- [X] **9. Secao de conformidade no README**
      Quais dados sao tratados, com qual finalidade e por quanto tempo. (LGPD, Aula 16)

- [X] **10. Relatorio de trade-offs**
      CAP, custo e latencia do seu sistema.
      Veja `RELATORIO_TRADEOFFS.md`.

## Extensoes opcionais

- [ ] Autoescalonamento de workers conforme o tamanho da fila
- [ ] Componente serverless ou na borda
- [X] CI/CD publicando automaticamente a cada commit
      Valida o compose e publica a imagem de cada servico no GHCR a cada push na main:
      `.github/workflows/publicar-imagens.yml`.
- [X] Painel de metricas
      `/metricas`: total de requisicoes, erros e tempo medio; dados protegidos por JWT.
- [ ] Manifests de Kubernetes

## Antes de entregar

- [ ] Clone em OUTRA maquina e rode o comando unico - funciona?
- [X] A URL publica esta no ar?
- [X] Toda configuracao esta em variavel de ambiente (nada fixo no codigo)?
- [X] Os logs estao livres de dados pessoais?
