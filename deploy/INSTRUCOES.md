# Deploy em nuvem (free tier) com HTTPS

A plataforma tem 11 processos: gateway, inferência REST, inferência gRPC, worker, ingestão,
worker de indexação, recuperação, geração, Redis, RabbitMQ e Jaeger. As duas opções abaixo
publicam o **mesmo código e os mesmos Dockerfiles** do `docker-compose.yml`. Só o gateway recebe
domínio público, e o **HTTPS é automático** nas duas plataformas (certificado gerenciado por elas).
Os demais serviços conversam pela rede privada da plataforma.

Diferenças em relação ao ambiente local:

- **Índice vetorial no Redis.** Railway e Render não deixam montar o mesmo volume em dois serviços.
  Por isso, na nuvem, a ingestão e o worker de indexação usam `INDICE_REDIS_URL` em vez do arquivo.
- **Uma config por serviço.** O build usa a raiz do repositório como contexto, igual ao compose.

| | Opção B: Railway (recomendada) | Opção A: Render |
|---|---|---|
| Arquitetura completa | sim | sim |
| Custo | crédito do período de teste (trial); depois, plano Hobby | gateway e Redis grátis; demais serviços no plano *starter* (pago) |
| HTTPS | `https://<nome>.up.railway.app` | `https://<nome>.onrender.com` |
| Configuração | `deploy/railway/<servico>.json` | `render.yaml` (Blueprint) |

---

## Opção B: Railway (recomendada)

### 1. Projeto e infraestrutura

1. Crie a conta em railway.app e conecte o GitHub.
2. **New Project → Empty Project.**
3. **+ Create → Database → Redis.** Mantenha o nome do serviço como `Redis`.
4. **+ Create → Docker Image →** `rabbitmq:3.13-alpine`. Renomeie para `rabbitmq` e, em *Variables*, defina:
   - `RABBITMQ_DEFAULT_USER=plataforma`
   - `RABBITMQ_DEFAULT_PASS=<gere uma senha só com letras e números>`

   Em *Settings*, adicione um volume em `/var/lib/rabbitmq`.
5. **+ Create → Docker Image →** `jaegertracing/all-in-one:1.62.0`. Renomeie para `jaeger` e defina `COLLECTOR_OTLP_ENABLED=true`.

### 2. Serviços da aplicação

Para cada linha da tabela: **+ Create → GitHub Repo →** este repositório. Renomeie o serviço
**exatamente** com o nome da tabela. Depois, em *Settings → Config-as-code → Railway Config File*,
informe o arquivo:

| Serviço | Railway Config File |
|---|---|
| `gateway` | `/deploy/railway/gateway.json` |
| `inferencia` | `/deploy/railway/inferencia.json` |
| `inferencia-grpc` | `/deploy/railway/inferencia-grpc.json` |
| `worker` | `/deploy/railway/worker.json` |
| `ingestao` | `/deploy/railway/ingestao.json` |
| `worker-indexacao` | `/deploy/railway/worker-indexacao.json` |
| `recuperacao` | `/deploy/railway/recuperacao.json` |
| `geracao` | `/deploy/railway/geracao.json` |

Cada arquivo define o Dockerfile, o comando de start (os serviços internos escutam em `::`, porque a rede privada do
Railway usa IPv6), o healthcheck e quais pastas disparam um novo deploy.

> **Se o build aparecer como "Railpack could not determine how to build the app"**, o Railway não
> aplicou o arquivo de config. Confira o caminho (começando com `/`) e faça um novo deploy. Como plano B,
> adicione no serviço a variável `RAILWAY_DOCKERFILE_PATH` com o Dockerfile da tabela abaixo e o comando
> de start em *Settings → Deploy → Custom Start Command* (o mesmo `startCommand` do JSON).
>
> | Serviço | `RAILWAY_DOCKERFILE_PATH` |
> |---|---|
> | `gateway` | `gateway/Dockerfile` |
> | `inferencia`, `inferencia-grpc` | `inferencia/Dockerfile` |
> | `worker` | `worker/Dockerfile` |
> | `ingestao`, `worker-indexacao` | `ingestion-service/Dockerfile` |
> | `recuperacao` | `retrieval-service/Dockerfile` |
> | `geracao` | `generation-service/Dockerfile` |

### 3. Variáveis

Em *Project Settings → Shared Variables*, crie (vale para todos os serviços):

```
OTEL_EXPORTER_OTLP_ENDPOINT=http://${{jaeger.RAILWAY_PRIVATE_DOMAIN}}:4318
```

Em cada serviço (*Variables → Raw Editor*):

```bash
# gateway
JWT_SEGREDO=<openssl rand -hex 32>
GATEWAY_USUARIO=admin
GATEWAY_SENHA=<senha forte>
URL_INFERENCIA=http://${{inferencia.RAILWAY_PRIVATE_DOMAIN}}:8001
GRPC_INFERENCIA=${{inferencia-grpc.RAILWAY_PRIVATE_DOMAIN}}:50051
URL_INGESTAO=http://${{ingestao.RAILWAY_PRIVATE_DOMAIN}}:8002
URL_RECUPERACAO=http://${{recuperacao.RAILWAY_PRIVATE_DOMAIN}}:8003
URL_GERACAO=http://${{geracao.RAILWAY_PRIVATE_DOMAIN}}:8004

# inferencia  e  worker
REDIS_URL=${{Redis.REDIS_URL}}

# inferencia-grpc
GRPC_PORTA=50051

# ingestao  e  worker-indexacao
RABBITMQ_URL=amqp://${{rabbitmq.RABBITMQ_DEFAULT_USER}}:${{rabbitmq.RABBITMQ_DEFAULT_PASS}}@${{rabbitmq.RAILWAY_PRIVATE_DOMAIN}}:5672/%2F
INDICE_REDIS_URL=${{Redis.REDIS_URL}}

# recuperacao
URL_INGESTAO=http://${{ingestao.RAILWAY_PRIVATE_DOMAIN}}:8002

# geracao
URL_RECUPERACAO=http://${{recuperacao.RAILWAY_PRIVATE_DOMAIN}}:8003
LLM_API_KEY=            # vazio = modo simulado
```

### 4. Domínio público com HTTPS

No serviço `gateway`, vá em *Settings → Networking → Generate Domain* e informe a porta **8000**.
O Railway entrega `https://<nome>.up.railway.app` com certificado válido. **Só o gateway** recebe domínio.

### 5. Ver os traces

O Jaeger não tem autenticação. Para capturar evidências, gere um domínio temporário para o serviço
`jaeger` na porta **16686**, consulte os traces e remova o domínio em seguida.

---

## Opção A: Render (Blueprint)

1. Crie a conta em render.com e conecte o GitHub.
2. **New → Blueprint →** selecione este repositório. O Render lê o [render.yaml](../render.yaml)
   e cria todos os serviços, o Redis (Key Value) e as variáveis.
3. Ao confirmar, preencha `GATEWAY_SENHA` (e `LLM_API_KEY`, se quiser um LLM real).
   `JWT_SEGREDO` e a senha do RabbitMQ são geradas pelo Render.
4. A URL pública fica em `https://gateway-<id>.onrender.com`, com HTTPS automático.

Limitações do Render:

- **Custo:** no plano gratuito só há *web services* e *Key Value*. Workers e serviços privados
  (inferência, gRPC, ingestão, recuperação, geração e RabbitMQ) exigem o plano *starter*, que é pago.
  Os serviços internos não são criados como *web* porque ficariam públicos e sem JWT.
- **Hibernação:** o gateway gratuito hiberna após 15 min sem tráfego. A primeira requisição leva
  até ~1 min para acordar.
- **Sem persistência:** o Key Value gratuito não persiste em disco. Se reiniciar, o índice vetorial
  volta só com os documentos de exemplo.
- **Sem Jaeger:** para ter traces, crie um *private service* com a imagem
  `jaegertracing/all-in-one:1.62.0` (`COLLECTOR_OTLP_ENABLED=true`) e defina
  `OTEL_EXPORTER_OTLP_ENDPOINT=http://<host do jaeger>:4318` nos serviços.

---

## Depois do deploy

```bash
URL=https://<seu-dominio>
curl $URL/saude
TOKEN=$(curl -s -X POST $URL/login -H "Content-Type: application/json" \
  -d '{"usuario":"admin","senha":"<GATEWAY_SENHA>"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
curl -s -X POST $URL/predict-sync -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{"texto":"servico excelente"}'
curl -I http://${URL#https://}/saude    # deve redirecionar para HTTPS
```

Por fim, registre a URL na seção **"URL pública"** do README.
