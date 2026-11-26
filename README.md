# C3.A2: Plataforma de IA como Serviço

**Sistemas Distribuídos e Computação em Nuvem · FAESA · 2026/2**
Prof. Howard Cruz Roatti · Entrega na Aula 17 (26/11)

Plataforma cloud-native que oferece dois serviços de IA por trás de um único API Gateway:

- **Classificação de sentimento assíncrona:** o texto entra numa fila Redis e um worker roda o modelo.
- **Perguntas e respostas sobre documentos (RAG):** os documentos são indexados de forma assíncrona
  (RabbitMQ) num armazenamento vetorial; a recuperação busca os trechos mais parecidos e a geração
  monta a resposta com um LLM (modo simulado offline quando não há chave de API).

Tudo roda em containers, sobe com um comando, tem rastreamento distribuído (OpenTelemetry + Jaeger),
autenticação JWT, rate limit, tempo-limite, retentativa e circuit breaker.

## URL pública

> **URL:** `https://<preencher-após-o-deploy>` (passo a passo em [deploy/INSTRUCOES.md](deploy/INSTRUCOES.md))

---

## Arquitetura

```
                               HTTPS (produção: Caddy + Let's Encrypt)
                                              │
                                     ┌────────▼────────┐
                 cliente ──────────► │     gateway     │  JWT · rate limit · métricas
                                     │      :8000      │  timeout · retentativa · circuit breaker
                                     └─┬──────┬──────┬─┘
                    ┌──────────────────┘      │      └───────────────────┐
                    │ /predict, /resultado    │ /documentos             │ /perguntar, /buscar
           ┌────────▼────────┐       ┌────────▼────────┐        ┌───────▼────────┐
           │   inferencia    │       │    ingestao     │        │    geracao     │──► LLM (ou simulado)
           │      :8001      │       │      :8002      │        │     :8004      │
           └────────┬────────┘       └──┬──────────▲───┘        └───────┬────────┘
             enfileira│                 │publica   │GET /indice         │
           ┌────────▼────────┐  ┌───────▼───────┐  │            ┌───────▼────────┐
           │  Redis (fila +  │  │   RabbitMQ    │  └────────────┤  recuperacao   │
           │   resultados)   │  │  (indexacao)  │               │     :8003      │
           └────────▲────────┘  └───────┬───────┘               └────────────────┘
             consome│                   │consome
           ┌────────┴────────┐  ┌───────▼─────────┐     ┌──────────────────────────┐
           │     worker      │  │ worker-indexacao├────►│ armazenamento vetorial   │
           │ (modelo de IA)  │  │  (embeddings)   │     │ (volume indice-vetorial) │
           └─────────────────┘  └─────────────────┘     └──────────────────────────┘

        Todos os serviços enviam spans ao Jaeger (OTLP :4318) · interface em :16686
```

| Serviço | Diretório | Papel |
|---|---|---|
| `gateway` | [gateway/](gateway/) | Porta de entrada única. Login JWT, rate limit por cliente, roteamento, painel de métricas. Um circuit breaker por serviço de destino, com tempo-limite e retentativa com espera progressiva. |
| `inferencia` | [inferencia/](inferencia/) | `POST /predict` enfileira a tarefa no Redis e devolve o `id` na hora (202). `GET /resultado/{id}` devolve o estado. |
| `worker` | [worker/](worker/) | Consome a fila Redis, roda o classificador de sentimento e grava o resultado. Falhou: reenfileira até 3 vezes e depois manda para `tarefas:descarte`. Escala horizontalmente. |
| `ingestao` | [ingestion-service/](ingestion-service/) | `POST /indexar` publica os documentos no RabbitMQ (202). `GET /indice` entrega o índice vetorial. |
| `worker-indexacao` | [ingestion-service/](ingestion-service/) | Consome o RabbitMQ, gera os embeddings (TF-IDF) e grava o **armazenamento vetorial** no volume. Mensagens inválidas vão para a dead letter `indexacao.falhas`. Indexa os textos de `documentos/` na primeira subida. |
| `recuperacao` | [retrieval-service/](retrieval-service/) | Busca no índice os trechos mais parecidos com a pergunta (similaridade do cosseno). |
| `geracao` | [generation-service/](generation-service/) | Recupera o contexto e chama o LLM. Tem circuit breaker próprio e degradação elegante: se o LLM falhar, devolve os trechos encontrados. |
| `redis`, `rabbitmq`, `jaeger` | imagens oficiais | Fila de inferência, fila de indexação e coletor/visualizador de traces. |

O código compartilhado fica em [comum/](comum/): telemetria, circuit breaker, embeddings e fila Redis.
Cada serviço tem o seu Dockerfile; o build usa a raiz do repositório como contexto para copiar `comum/`.

### Comunicação

- **REST síncrono:** cliente → gateway → serviços; geração → recuperação → ingestão.
- **Mensageria assíncrona:** inferência → Redis → worker; ingestão → RabbitMQ → worker de indexação.

### Resiliência

| Onde | Mecanismo |
|---|---|
| Gateway → serviços | Tempo-limite (`TIMEOUT_SERVICOS`; geração com `TIMEOUT_GERACAO`), até 3 tentativas com espera de 0,5 s, 1 s e 2 s só em falhas transitórias (timeout, conexão, 502/503/504) e **um circuit breaker por serviço** (abre após 5 falhas e testa de novo após 30 s). O estado fica em `GET /saude`. |
| Geração → recuperação e LLM | Dois circuit breakers, tempo-limite, retentativa e resposta degradada. |
| Worker de inferência | 3 tentativas por tarefa e fila de descarte (`tarefas:descarte`). |
| Ingestão | Publicação com confirmação do broker e retentativa; o worker só confirma (`ack`) depois de gravar; dead letter queue. |
| Containers | `restart: unless-stopped` e `healthcheck` em Redis e RabbitMQ. |

### Observabilidade (rastreamento distribuído)

Todos os serviços usam OpenTelemetry e exportam os spans para o Jaeger. O contexto (`traceparent`)
atravessa as chamadas HTTP automaticamente e **também atravessa as filas**: vai dentro da mensagem no
Redis e nos headers AMQP do RabbitMQ. Por isso um único trace mostra:

```
gateway POST /predict → inferencia POST /predict → fila.enfileirar (PRODUCER)
                                                    └→ worker.processar (CONSUMER) → modelo.prever
gateway POST /documentos → ingestao POST /indexar → fila.publicar → worker.indexar → indice.vetorizar_e_gravar
gateway POST /perguntar → geracao → recuperacao → ingestao GET /indice ; llm.gerar
```

Abra **http://localhost:16686**, escolha o serviço `gateway` e clique em *Find Traces*.

---

## Como executar do zero

Pré-requisito: Docker com o plugin Compose (Docker Desktop no Windows/macOS).

```bash
git clone https://github.com/ReisDC/sd-2026-2-kit-c3a2.git
cd sd-2026-2-kit-c3a2
cp .env.example .env              # uma vez; troque os segredos antes de publicar
docker compose up --build         # o comando único: sobe os 10 containers
```

A primeira subida baixa as imagens e instala as dependências (alguns minutos).

### Testando pelo gateway

```bash
# 1. Login (usuário/senha do .env) e token
TOKEN=$(curl -s -X POST http://localhost:8000/login -H "Content-Type: application/json" \
  -d '{"usuario":"admin","senha":"troque-esta-senha"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

# 2. Sentimento (assíncrono): enfileira e depois consulta
curl -s -X POST http://localhost:8000/predict -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{"texto":"servico excelente, recomendo"}'
#   -> {"id":"<id>","status":"na_fila"}
curl -s http://localhost:8000/resultado/<id> -H "Authorization: Bearer $TOKEN"
#   -> {"id":"<id>","sentimento":"positivo","confianca":0.6,"status":"pronto",...}

# 3. RAG: indexar um documento, buscar e perguntar
curl -s -X POST http://localhost:8000/documentos -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '[{"texto":"O circuit breaker evita chamar um servico que esta falhando.","origem":"notas"}]'
curl -s -X POST http://localhost:8000/buscar -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{"texto":"o que e o teorema CAP?","top_k":2}'
curl -s -X POST http://localhost:8000/perguntar -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{"pergunta":"o que e o teorema CAP?"}'

# 4. Sem token -> 401
curl -i -X POST http://localhost:8000/predict -H "Content-Type: application/json" -d '{"texto":"oi"}'
```

A documentação interativa fica em **http://localhost:8000/docs**.

### Rotas do gateway

| Método e rota | JWT | Destino |
|---|---|---|
| `GET /saude` | não | gateway (estado dos circuit breakers) |
| `POST /login` | não | emite o token (`expires_in` = `JWT_EXPIRACAO_MINUTOS`) |
| `POST /predict` | sim | inferência → fila Redis (202 + `id`) |
| `GET /resultado/{id}` | sim | inferência (`na_fila`, `pronto` ou `erro`) |
| `POST /documentos` | sim | ingestão → RabbitMQ (202) |
| `POST /buscar` | sim | recuperação (busca vetorial) |
| `POST /perguntar` | sim | geração (RAG) |
| `GET /metricas` | não* | página do painel; os dados vêm de `GET /metricas/dados`, que exige JWT |

Toda rota com JWT conta no rate limit (`RATE_LIMIT_POR_MINUTO` por usuário, janela de 60 s; acima disso responde 429).

### Experimentos rápidos

```bash
docker compose up -d --scale worker=3          # mais workers consumindo a mesma fila
docker compose stop recuperacao                # /buscar: 503 -> após 5 falhas, circuito aberto
docker compose start recuperacao               # após 30 s o circuito testa e fecha
docker compose logs -f worker gateway          # logs (sem dados pessoais)
```

### Painel de métricas

Abra `http://localhost:8000/metricas` e entre com o usuário e a senha do gateway. O painel mostra o total
de requisições, os erros (4xx/5xx) e o tempo médio. As consultas do próprio painel não entram na contagem,
e os números zeram quando o gateway reinicia.

### Configuração

Toda a configuração vem de variáveis de ambiente, documentadas em [.env.example](.env.example). O código
não tem segredos fixos: sem `JWT_SEGREDO` ou `GATEWAY_SENHA`, o gateway nem sobe.

### Deploy e HTTPS

Veja [deploy/INSTRUCOES.md](deploy/INSTRUCOES.md). Numa VM gratuita, o mesmo compose sobe com o override
[docker-compose.prod.yml](docker-compose.prod.yml), que coloca o Caddy na frente do gateway com certificado
Let's Encrypt automático. Só as portas 80 e 443 ficam abertas.

### CI/CD

O workflow [.github/workflows/publicar-imagens.yml](.github/workflows/publicar-imagens.yml) roda a cada push na
`main`: valida o compose e a sintaxe do Python e publica a imagem de cada serviço no GHCR
(`ghcr.io/<repo>/<servico>:latest` e `:<sha>`).

---

## Estrutura do projeto

```
├── gateway/                 # API Gateway (JWT, rate limit, resiliência, métricas)
├── inferencia/              # API de inferência (enfileira no Redis)
├── worker/                  # worker de inferência + modelo de sentimento
├── ingestion-service/       # API de ingestão + worker de indexação + documentos de exemplo
├── retrieval-service/       # busca vetorial
├── generation-service/      # geração (RAG) + cliente de LLM
├── comum/                   # telemetria, circuit breaker, embeddings, fila Redis
├── deploy/                  # instruções de deploy e Caddyfile (HTTPS)
├── docker-compose.yml       # sistema inteiro com um comando
├── docker-compose.prod.yml  # override de produção (Caddy + HTTPS)
├── RELATORIO_TRADEOFFS.md   # CAP, custo e latência
└── TAREFAS.md               # checklist do trabalho
```

Relatório de CAP, custo e latência: [RELATORIO_TRADEOFFS.md](RELATORIO_TRADEOFFS.md).

---

## Conformidade com a LGPD

| Dado | Onde aparece | Finalidade | Retenção |
|---|---|---|---|
| Usuário e senha | `POST /login` | Autenticar o cliente antes de emitir o token | Não são armazenados; a senha só é comparada com a configurada em variável de ambiente |
| Token JWT (`sub`, `iat`, `exp`) | Cabeçalho `Authorization` | Autorizar as chamadas às rotas protegidas | Não é guardado no servidor; expira em `JWT_EXPIRACAO_MINUTOS` (60 min por padrão) |
| Identificador do cliente (usuário do token) | Rate limit do gateway | Limitar requisições por minuto e proteger o serviço contra abuso | Só em memória, com os horários dos últimos 60 s; perdido ao reiniciar o gateway |
| Texto enviado para classificação | `POST /predict`, fila Redis e worker | Executar a inferência de sentimento solicitada | Fica no Redis só até o worker consumir a tarefa (ou na fila de descarte, se falhar). Localmente o Redis roda sem persistência em disco; na nuvem, o Redis gerenciado pode gravar snapshots |
| Resultado (sentimento e confiança) | Redis e `GET /resultado/{id}` | Devolver a resposta ao cliente | Expira automaticamente em `RESULTADO_TTL_SEGUNDOS` (24 h por padrão); o texto não é devolvido nem guardado junto |
| Texto enviado ao gRPC | `POST /predict-sync`, `POST /predict-lote` | Classificar na hora | Não é armazenado: só fica na memória durante a chamada |
| Documentos indexados (texto e origem) | `POST /documentos`, RabbitMQ e índice vetorial | Formar a base consultada pelo RAG | **Persistem** até serem removidos pelo responsável (volume `indice-vetorial` localmente; chave `indice:vetorial` no Redis na nuvem). Não envie documentos com dados pessoais |
| Perguntas ao RAG | `POST /buscar`, `POST /perguntar` | Recuperar trechos e gerar a resposta | Não são armazenadas. **Com `LLM_API_KEY` definida, a pergunta e os trechos são enviados ao provedor do LLM** (operador terceiro); no modo simulado nada sai do servidor |
| Traces (Jaeger) | Spans de todos os serviços | Diagnóstico de latência e falhas | Só em memória do Jaeger (perdidos ao reiniciar). Guardam rota, status, tempos, ids de tarefa e tamanhos; nunca o texto, a senha ou o token |
| Logs | Saída dos containers | Diagnóstico | Registram só método, rota, status, tempo, id da tarefa e tamanho da entrada. O log de acesso do uvicorn está desligado porque gravaria o IP |

**Base legal:** execução do serviço solicitado pelo próprio titular (art. 7º, V). Nenhum dado é usado para
treinar o modelo nem para outra finalidade. O único compartilhamento possível é com o provedor do LLM,
quando configurado.

**Minimização:** o serviço não pede nome, CPF, e-mail nem qualquer outro dado pessoal. O texto é o único
conteúdo livre; recomenda-se não enviar dados pessoais nele.

**Segurança (art. 46):** rotas protegidas por JWT, segredos fora do código (variáveis de ambiente), limite
de requisições por cliente, HTTPS no serviço publicado e serviços internos, filas e banco acessíveis apenas
pela rede privada do Docker. Os containers rodam com usuário sem privilégios.

**Direitos do titular (art. 18):** como não há cadastro, o titular pode pedir a exclusão de uma tarefa
informando o id recebido; o responsável apaga a chave `resultado:<id>` do Redis (que de todo modo expira
sozinha). Documentos indexados podem ser removidos apagando o volume `indice-vetorial` (ou a chave
`indice:vetorial` do Redis, na nuvem).

---

## Dúvidas frequentes

**Preciso de internet ou GPU?** Não. O modelo de sentimento é treinado localmente durante o build, os
embeddings usam TF-IDF e o LLM tem modo simulado sem `LLM_API_KEY`.

**Como uso um LLM de verdade?** Defina `LLM_API_KEY` (e, se quiser, `LLM_API_URL` e `LLM_MODELO`) no `.env`.
A API precisa seguir o formato chat/completions.
