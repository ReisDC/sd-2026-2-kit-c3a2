# Deploy em nuvem (free tier) com HTTPS

A plataforma tem 11 processos: gateway, inferência REST, inferência gRPC, worker, ingestão,
worker de indexação, recuperação, geração, Redis, RabbitMQ e Jaeger. As duas opções abaixo
publicam o **mesmo código e os mesmos Dockerfiles** do `docker-compose.yml`. Só o gateway recebe
domínio público, e o **HTTPS é automático** nas duas plataformas (certificado gerenciado por elas).
Os demais serviços conversam pela rede privada da plataforma.

Diferenças em relação ao ambiente local:

- **Índice vetorial no Redis.** Railway e Render não deixam montar o mesmo volume em dois serviços.
  Por isso, na nuvem, a ingestão e o worker de indexação usam `INDICE_REDIS_URL` em vez do arquivo.
- **Mesmo build do compose.** Cada serviço usa o próprio Dockerfile com a raiz do repositório como contexto.

| | Opção B: Railway (recomendada) | Opção A: Render |
|---|---|---|
| Arquitetura completa | sim | sim |
| Custo | crédito do período de teste (trial); depois, plano Hobby | gateway e Redis grátis; demais serviços no plano *starter* (pago) |
| HTTPS | `https://<nome>.up.railway.app` | `https://<nome>.onrender.com` |
| Configuração | `.railway/railway.ts` (infraestrutura como código) | `render.yaml` (Blueprint) |

---

## Opção B: Railway (recomendada), com infraestrutura como código

Toda a infraestrutura do Railway está descrita em [.railway/railway.ts](../.railway/railway.ts), no
formato de [infraestrutura como código](https://docs.railway.com/infrastructure-as-code) do Railway: os
11 serviços, o Redis, o volume do RabbitMQ, o Dockerfile e o comando de cada serviço, healthchecks e as
variáveis que ligam um serviço ao outro pela rede privada. Não é preciso criar serviço por serviço no painel.

> O formato antigo (`railway.json` por serviço) foi descontinuado pelo Railway e não é mais aplicado
> em serviços novos. Por isso o build caía no Railpack ("could not determine how to build the app").

### 1. Pré-requisitos

- Conta no railway.app conectada ao GitHub, com acesso a este repositório.
- [Railway CLI](https://docs.railway.com/cli) 5.42.1 ou mais nova (`railway --version`) e Node.js 22+.
- Na raiz do repositório: `npm install` (instala o pacote `railway`, que o `railway.ts` importa).

### 2. Criar o projeto e aplicar

```bash
railway login
railway init                 # cria um projeto vazio (ou: railway link, para um projeto existente)
railway config plan          # mostra o que será criado; não altera nada
railway config apply         # cria serviços, Redis, volume e variáveis
```

Se você já tinha criado serviços manualmente com os mesmos nomes, apague-os antes ou rode
`railway config pull` para comparar. Um serviço não pode ser gerenciado ao mesmo tempo pelo IaC e pelo painel.

### 3. Segredos (uma vez)

Os segredos não ficam no repositório. No `railway.ts` eles estão marcados com `preserve()`, então o
`apply` mantém o valor definido no painel. Depois do primeiro `apply`, defina em *Variables*:

| Serviço | Variável | Valor |
|---|---|---|
| `gateway` | `JWT_SEGREDO` | `openssl rand -hex 32` |
| `gateway` | `GATEWAY_SENHA` | senha forte do login |
| `rabbitmq` | `RABBITMQ_DEFAULT_PASS` | só letras e números (vai dentro de uma URL) |
| `geracao` | `LLM_API_KEY` | opcional; vazio = modo simulado |

Em seguida faça *Redeploy* do `gateway`, do `rabbitmq`, da `ingestao` e do `worker-indexacao`.

### 4. Domínio público com HTTPS

No serviço `gateway`, abra *Settings → Networking → Generate Domain* e informe a porta **8000**. O
Railway entrega `https://<nome>.up.railway.app` com certificado válido. Os domínios gerados pelo Railway
não ficam no `railway.ts`, por isso esse passo é manual. **Só o gateway** recebe domínio.

### 5. Ver os traces

O Jaeger não tem autenticação. Para capturar evidências, gere um domínio temporário para o serviço
`jaeger` na porta **16686**, consulte os traces e remova o domínio em seguida.

### Observações

- **Rede privada IPv6:** os serviços internos escutam em `::`; o gateway escuta em `0.0.0.0` para o proxy público.
- **Índice vetorial no Redis:** o Railway não deixa montar o mesmo volume em dois serviços, então a
  ingestão e o worker de indexação compartilham o índice pelo Redis (`INDICE_REDIS_URL`).
- **Deploy contínuo:** cada serviço está ligado à branch `main` e só é refeito quando muda a pasta dele
  ou `comum/` (`watchPatterns`).
- **Mudanças na infraestrutura:** edite o `railway.ts` e rode `railway config plan` / `apply` de novo.
  O Railway não lê a pasta `.railway/` sozinho durante os deploys.

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
