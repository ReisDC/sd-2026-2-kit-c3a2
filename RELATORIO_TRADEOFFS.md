# Relatório de trade-offs

## CAP

Durante uma partição de rede, o sistema precisa escolher entre consistência e disponibilidade.
Cada parte da plataforma escolhe de um jeito:

- **Gateway → serviços:** se a rede até um serviço cair, o gateway não inventa resposta. Ele responde
  503/504 e o circuit breaker passa a recusar na hora. Nesse caminho a plataforma prefere **C**: só
  devolve resultado verdadeiro e abre mão de atender.
- **Inferência assíncrona (Redis + worker):** o `POST /predict` continua aceitando tarefas mesmo com todos
  os workers fora, porque só depende do Redis. A resposta chega depois (consistência eventual): o cliente
  vê `na_fila` até o worker voltar. Para a escrita, esse caminho é **A**.
- **Indexação (RabbitMQ + worker):** mesma ideia. O documento é aceito (202) e entra no índice quando o
  worker consumir. Até lá, `/buscar` devolve o índice antigo: dado desatualizado em vez de erro, ou seja, **A**.
- **Geração (RAG):** se o LLM falhar, a geração responde com os trechos recuperados (degradação elegante).
  Prefere **A** a uma resposta completa.

- **Via gRPC (síncrona):** o `/predict-sync` não passa pela fila. Se o servidor gRPC cair, a rota responde
  503 na hora (circuit breaker), enquanto a via assíncrona continua aceitando tarefas.

Não há réplicas: Redis, RabbitMQ, o armazenamento do índice e o Jaeger são pontos únicos de falha. Replicá-los
(Redis Sentinel, cluster do RabbitMQ, banco vetorial distribuído) traria disponibilidade, mas custaria
mais memória e máquinas do que o free tier oferece.

## Custo

- **PaaS (Railway ou Render)** recebe o mesmo código e os mesmos Dockerfiles, com HTTPS e rede privada
  gerenciados. O custo cresce com o número de serviços: no Railway, os 11 processos consomem o crédito do
  trial e depois o plano Hobby; no Render, só o gateway e o Redis são gratuitos, e cada serviço privado ou
  worker vai para o plano pago. A alternativa seria juntar serviços (ex.: rodar o worker dentro da API),
  mais barata, mas perderíamos o escalonamento e o isolamento de falhas independentes.
- **Perfil Free (o publicado):** o plano Free do Railway permite só 3 serviços por projeto e 0,5 GB de
  RAM por serviço. Por isso a produção roda dois serviços: o Redis e um container com todos os serviços
  (`Dockerfile` da raiz). O código e os protocolos são os mesmos (HTTP, gRPC e filas, via `127.0.0.1`),
  e o consumo fica em ~120 MB. Perdemos o isolamento de falhas e o escalonamento independente em
  produção: se o processo cai, cai tudo junto, e escalar o worker significa escalar a plataforma inteira.
  O RabbitMQ dá lugar a uma fila no Redis (sem confirmação de publicação nem dead letter nativa, mas com
  fila de falhas própria), e o Jaeger dá lugar ao tracing gerenciado do Railway. O compose continua com
  os microsserviços separados, e o perfil `completo` do `.railway/railway.ts` volta a separá-los num
  plano pago.
- **Plano gratuito tem limites:** no Render, o gateway gratuito hiberna após 15 minutos (a primeira
  requisição leva até ~1 min) e o Redis gratuito não persiste, então o índice vetorial precisa ser refeito
  após um reinício.
- **Microsserviços custam memória:** cada serviço Python ocupa cerca de 60–120 MB, e o RabbitMQ sozinho
  ~150 MB. Um monólito gastaria menos, mas perderia o escalonamento independente (ex.: `--scale worker=3`
  sem duplicar o gateway) e o isolamento de falhas.
- **Dois brokers** (Redis para a inferência e RabbitMQ para a indexação) é mais um container para operar.
  Em troca, a indexação ganha confirmação de publicação, `ack` manual e dead letter nativa, e a inferência
  usa o Redis, que já guarda os resultados.
- **LLM real** cobra por token. O modo simulado deixa a plataforma funcionando sem custo e offline.
- **Jaeger em memória:** não precisa de banco nem de armazenamento pago, mas perde os traces ao reiniciar.

## Latência

- **Gateway:** cada salto HTTP interno soma ~1–5 ms na rede do Docker. O rate limit e a validação do JWT
  custam microssegundos.
- **Inferência síncrona (gRPC) x assíncrona (fila):** o `/predict-sync` devolve a classificação numa única
  ida e volta (poucos ms: HTTP até o gateway, gRPC/HTTP2 com protobuf até a inferência). É o caminho mais
  rápido para textos isolados, mas a carga cai direto no servidor gRPC. O `/predict-lote` amortiza o custo
  da chamada entre até 100 textos.
- **Inferência assíncrona:** o `POST /predict` volta em poucos milissegundos porque só enfileira. A
  latência real (fila + modelo, ~1–10 ms por texto) aparece no tempo até o resultado ficar `pronto`, e o
  cliente precisa consultar de novo. Trocamos a latência percebida pelo cliente por vazão e isolamento:
  picos de carga ficam na fila em vez de derrubar a API.
- **RAG:** `/perguntar` passa por gateway → geração → recuperação → ingestão. A recuperação lê o índice
  inteiro e recalcula o vocabulário a cada busca. Isso é aceitável com poucos documentos, mas cresce
  linearmente; com muitos documentos seria preciso um banco vetorial com índice ANN (ex.: Qdrant, pgvector).
  Com LLM real, a chamada externa (centenas de ms a segundos) domina o tempo total.
- **Resiliência tem preço:** com tempo-limite de 5 s, 3 tentativas e esperas de 0,5 s + 1 s, o pior caso
  antes de devolver erro é ~16,5 s. O circuit breaker corta esse custo: aberto, a resposta 503 é imediata.
  A geração tem tempo-limite maior (30 s) porque espera o LLM.
- **Tracing:** os spans são exportados em lote (`BatchSpanProcessor`), fora do caminho da requisição. O
  custo é pequeno e serve para enxergar onde o tempo é gasto.

## Limites conhecidos

- Não foram feitos testes de carga; os números acima são estimativas da arquitetura, não medições.
- O rate limit e as métricas ficam em memória do gateway. Com várias réplicas do gateway, seria preciso
  movê-los para o Redis.
- Uma retentativa do `POST /predict` depois de um tempo-limite pode enfileirar a mesma tarefa duas vezes.
  O efeito é inofensivo (a classificação é idempotente), mas gera trabalho extra. No índice, documentos
  repetidos já são descartados.
