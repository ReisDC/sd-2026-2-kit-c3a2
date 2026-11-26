# Deploy em nuvem (free tier) com HTTPS

A plataforma tem 11 processos no ambiente local: gateway, inferência REST, inferência gRPC, worker, ingestão,
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
| Arquitetura | perfil Free: 2 serviços (plataforma + Redis); perfil completo: os 11 | completa |
| Custo | perfil Free: grátis (US$ 1/mês de crédito); perfil completo: plano Hobby | gateway e Redis grátis; demais serviços no plano *starter* (pago) |
| HTTPS | `https://<nome>.up.railway.app` | `https://<nome>.onrender.com` |
| Configuração | `.railway/railway.ts` (infraestrutura como código) | `render.yaml` (Blueprint) |

---

## Opção B: Railway (recomendada), com infraestrutura como código

Toda a infraestrutura do Railway está descrita em [.railway/railway.ts](../.railway/railway.ts), no
formato de [infraestrutura como código](https://docs.railway.com/infrastructure-as-code) do Railway.
Não é preciso criar serviço por serviço no painel. O arquivo tem dois perfis (constante `PERFIL`):

| Perfil | Plano | O que cria |
|---|---|---|
| `"free"` (padrão) | Free: até 3 serviços por projeto, 0,5 GB de RAM por serviço | `redis` + `plataforma` |
| `"completo"` | Hobby ou superior | os 11 serviços separados, igual ao `docker-compose.yml` |

**Perfil Free:** o serviço `plataforma` (o [Dockerfile](../Dockerfile) da raiz, com [plataforma.py](../plataforma.py)) roda num único container o mesmo
código dos serviços:
- o gateway fica público;
- inferência, ingestão, recuperação e geração ficam num servidor HTTP em `127.0.0.1`;
- o gRPC fica em `127.0.0.1:50051`;
- os dois workers rodam em threads.

As chamadas entre eles continuam sendo HTTP, gRPC e filas, só que dentro do container. O RabbitMQ dá lugar
a uma fila no Redis, que também guarda o índice vetorial. O Jaeger dá lugar ao **tracing nativo do
Railway** (aba *Traces*), que recebe os spans do OpenTelemetry. O container usa cerca de 120 MB de RAM.

> O formato antigo (`railway.json` por serviço) foi descontinuado pelo Railway e não é mais aplicado
> em serviços novos. Por isso o build caía no Railpack ("could not determine how to build the app").

### 1. Pré-requisitos

- Conta no railway.app conectada ao GitHub, com acesso a este repositório.
- [Railway CLI](https://docs.railway.com/cli) 5.63 ou mais nova (`railway --version`) e Node.js 22+.
- Na raiz do repositório: `npm install` (instala o pacote `railway`, que o `railway.ts` importa).

### 2. Criar o projeto e aplicar

```bash
railway login
railway init                 # cria um projeto vazio (ou: railway link, para um projeto existente)
railway config plan          # mostra o que será criado; não altera nada
railway config apply         # cria os serviços e as variáveis
```

O `apply` remove serviços do projeto que não estejam no `railway.ts` (o plano marca como destrutivo).
No plano Free isso é necessário para caber no limite de 3 serviços. Se um `apply` anterior falhou, descarte
as mudanças pendentes no painel (aviso de *staged changes* → *Discard*) antes de aplicar de novo.

### 3. Segredos (uma vez)

Depois do primeiro `apply`, defina em *Variables* do serviço `plataforma` (perfil Free):

| Variável | Valor |
|---|---|
| `JWT_SEGREDO` | `openssl rand -hex 32` |
| `GATEWAY_SENHA` | senha forte do login |
| `LLM_API_KEY` | opcional; vazio = modo simulado |

Em seguida faça *Redeploy* do `plataforma`. No perfil completo, os mesmos segredos ficam no `gateway`
(e na `geracao`), e `RABBITMQ_DEFAULT_PASS` fica no `rabbitmq`, com só letras e números porque vai dentro
de uma URL. Depois faça *Redeploy* do `gateway`, do `rabbitmq`, da `ingestao` e do `worker-indexacao`.

### 4. Domínio público com HTTPS

No serviço público (`plataforma` no perfil Free, `gateway` no completo), abra *Settings → Networking →
Generate Domain*. O Railway entrega `https://<nome>.up.railway.app` com certificado válido. Os domínios
gerados pelo Railway não ficam no `railway.ts`, por isso esse passo é manual. No perfil completo, informe
a porta **8000**; no Free, o container escuta na porta `PORT` que o Railway fornece.

### 5. Ver os traces

- **Perfil Free:** aba *Traces* do projeto. O `tracing: { enabled: true }` já está no `railway.ts`.
- **Perfil completo:** Jaeger. Ele não tem autenticação, então gere um domínio temporário para o serviço
  `jaeger` na porta **16686**, consulte os traces e remova o domínio em seguida.

### Observações

- **Plano Free:** o crédito é de US$ 1/mês, e o serviço hiberna após alguns minutos sem tráfego. A
  primeira requisição depois disso acorda o container. Os workers esperam a fila sem acordar periodicamente
  (`FILA_ESPERA_SEGUNDOS=0`), justamente para permitir a hibernação.
- **Rede privada IPv6 (perfil completo):** os serviços internos escutam em `::`, e o gateway em `0.0.0.0`.
- **Índice vetorial no Redis:** o Railway não deixa montar o mesmo volume em dois serviços.
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
