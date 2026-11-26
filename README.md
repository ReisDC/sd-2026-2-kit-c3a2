# Kit de Partida — C3.A2: Plataforma de IA como Serviço (projeto integrador)

**Sistemas Distribuídos e Computação em Nuvem · FAESA · 2026/2**
Prof. Howard Cruz Roatti · Lançado na Aula 13 (05/11) · Entrega na Aula 17 (26/11)

---

## O que é isto

O projeto que fecha o semestre. Você pega tudo o que construiu e transforma numa
plataforma de verdade: **conteinerizada, publicada na internet, observável e protegida**.
Ao final você terá um sistema distribuído completo e público — material de portfólio
para entrevista de emprego.

> **Sobre a IA neste trabalho:** todo contato com inteligência artificial aqui é
> **chamada de biblioteca ou de API**. Você **não vai treinar modelos** nem precisar de
> matemática de aprendizado de máquina. O modelo já vem pronto e configurado.
> A sua nota vem da **engenharia distribuída**: arquitetura, comunicação, resiliência e
> execução reproduzível — a sofisticação do modelo **não pontua**.

---

## Como começar

```bash
# 1. Clone o kit e entre na pasta
git clone https://github.com/howardroatti/sd-2026-2-kit-c3a2.git
cd sd-2026-2-kit-c3a2

# 2. Crie e ative o ambiente virtual
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

# 3. Instale as dependências
pip install -r requirements.txt
```

```bash
# 4. Copie o arquivo de ambiente
cp .env.example .env

# 5. Suba a stack inteira com UM comando
docker compose up --build

# 6. Teste pelo gateway (nunca pelos serviços diretamente)
curl http://localhost:8000/saude
curl -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" -d '{"texto":"servico excelente"}'

# 7. Publique na nuvem seguindo deploy/INSTRUCOES.md
```

---

## Estrutura do projeto

```
sd-2026-2-kit-c3a2/
├── gateway/
│   ├── app.py              # TAREFAS 1 e 2 (JWT e rate limit)
│   └── Dockerfile          # PRONTO - use de modelo
├── inferencia/             # traga o seu C1.A2 para cá
├── worker/                 # traga o seu worker para cá
├── deploy/INSTRUCOES.md    # passo a passo do free tier
├── Dockerfile.exemplo      # modelo para os demais serviços
├── docker-compose.yml      # TAREFA 4
└── TAREFAS.md              # <- comece por aqui
```

---

## O que você precisa fazer

Abra o arquivo **`TAREFAS.md`**: ele lista o núcleo obrigatório item a item, indicando
o arquivo e a aula de referência de cada um.

---

## Como você será avaliado

| Critério | Pontos |
|---|---|
| Arquitetura e decomposição em serviços | 1,5 |
| Comunicação funcionando (REST / gRPC / mensageria) | 1,5 |
| Resiliência e tratamento de falhas | 1,0 |
| Execução reproduzível (README, container, deploy) | 1,0 |
| **Sofisticação do modelo de IA** | **não pontua** |
| **Total** | **5,0** |

**Entrega:** no seu repositório do GitHub, **sem apresentação oral**. Grupos livres.

Relatório de CAP, custo e latência: [RELATORIO_TRADEOFFS.md](RELATORIO_TRADEOFFS.md).
O workflow `publicar-gateway.yml` publica a imagem do gateway no GHCR a cada push
na `main`. Ele publica o container; o deploy da aplicação continua pendente.

---

## Aulas de referência

- **Aula 13** — Nuvem, containers e o primeiro deploy
- **Aula 14** — Orquestração, serverless e borda
- **Aula 15** — Observabilidade e rastreamento distribuído
- **Aula 16** — Segurança, LGPD e revisão para o ENADE

---

## Dúvidas frequentes

**Preciso saber machine learning?** Não. O modelo já está pronto e você só chama uma função.

**E se eu não tiver internet no laboratório?** Tudo neste kit funciona offline. O modelo é
treinado localmente e o cliente de LLM tem modo simulado.

**Posso trocar a linguagem?** O kit é em Python porque é o ecossistema usado nas aulas.
Se quiser usar outra linguagem, converse com o professor antes.

**Posso usar IA para me ajudar a programar?** Sim. Este é um trabalho prático feito fora de
sala, e usar ferramentas de IA é realista. O que se avalia é o **sistema funcionando** e as
**decisões de arquitetura** — que você precisa saber explicar.

---

## Conformidade com a LGPD

A plataforma recebe um texto, classifica o sentimento e devolve o resultado. Abaixo estão
os dados tratados, a finalidade de cada um e por quanto tempo ficam guardados.

| Dado | Onde aparece | Finalidade | Retenção |
|---|---|---|---|
| Usuário e senha | `POST /login` | Autenticar o cliente antes de emitir o token | Não são armazenados; a senha só é comparada com a configurada em variável de ambiente |
| Token JWT (`sub`, `iat`, `exp`) | Cabeçalho `Authorization` | Autorizar as chamadas às rotas protegidas | Não é guardado no servidor; expira em `JWT_EXPIRACAO_MINUTOS` (60 min por padrão) |
| Identificador do cliente (usuário do token ou IP) | Rate limit do gateway | Limitar requisições por minuto e proteger o serviço contra abuso | Só em memória, com horários dos últimos 60 s; perdido ao reiniciar o gateway |
| Texto enviado | `POST /predict`, fila e worker | Executar a inferência de sentimento solicitada pelo cliente | Fica no Redis com o resultado até o container ser recriado; não há volume persistente |
| Resultado (sentimento e confiança) | Redis e `GET /resultado/{id}` | Devolver a resposta ao cliente | Mesmo prazo do texto |
| Logs | Saída dos containers | Diagnóstico e observabilidade | Registram só método, rota, status, tempo, id da tarefa e tamanho da entrada; nunca o texto, a senha ou o token |

**Base legal:** execução do serviço solicitado pelo próprio titular (art. 7º, V). Nenhum dado é
usado para treinar o modelo, compartilhado com terceiros ou usado para outra finalidade.

**Minimização:** o serviço não pede nome, CPF, e-mail nem qualquer outro dado pessoal. O texto
é o único conteúdo livre; recomenda-se não enviar dados pessoais nele.

**Segurança (art. 46):** rotas protegidas por JWT, segredo e credenciais fora do código
(variáveis de ambiente), limite de requisições por cliente, HTTPS no serviço publicado e
comunicação interna apenas na rede privada do Docker.

**Direitos do titular (art. 18):** como não há cadastro, o titular pode pedir a exclusão de uma
tarefa informando o id recebido; o responsável apaga a chave `resultado:<id>` do Redis.
