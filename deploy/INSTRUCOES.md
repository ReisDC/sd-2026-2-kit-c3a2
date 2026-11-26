# Deploy em nuvem (free tier) com HTTPS

A plataforma tem 10 containers (gateway, inferência, worker, Redis, ingestão,
worker de indexação, recuperação, geração, RabbitMQ e Jaeger). PaaS gratuitos como
Render e Railway não rodam workers nem brokers no plano grátis. Por isso o deploy
usa **uma VM gratuita com Docker**: o mesmo `docker-compose.yml` do ambiente local
mais um override de produção (`docker-compose.prod.yml`). Nele, o **Caddy** fica na
frente do gateway e emite o certificado HTTPS (Let's Encrypt) automaticamente.

```
Internet --HTTPS:443--> Caddy --HTTP (rede interna)--> gateway --> demais serviços
```

## 1. Criar a VM (free tier)

Qualquer uma destas serve (mínimo recomendado: 2 GB de RAM):

- **Oracle Cloud Always Free:** VM `VM.Standard.A1.Flex` (ARM) com 2 OCPU / 12 GB e Ubuntu 24.04.
- **Google Cloud (e2-micro):** tem só 1 GB de RAM. Crie um swap de 2 GB antes de subir.
- **AWS (t3.micro, 12 meses grátis):** também tem 1 GB. Use o mesmo swap.

Libere a entrada nas portas **22, 80 e 443** no firewall/security list do provedor.
Não abra as portas 8000, 5672, 6379 nem 16686.

## 2. Instalar o Docker na VM

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER && newgrp docker
```

## 3. Clonar e configurar

```bash
git clone https://github.com/ReisDC/sd-2026-2-kit-c3a2.git
cd sd-2026-2-kit-c3a2
cp .env.example .env
nano .env
```

No `.env`:

- troque `JWT_SEGREDO` (gere com `openssl rand -hex 32`), `GATEWAY_SENHA` e `RABBITMQ_SENHA`;
- defina `DOMINIO`. Sem domínio próprio, use o IP público da VM com o sslip.io,
  por exemplo `DOMINIO=203.0.113.10.sslip.io`. O DNS do sslip.io já resolve para esse IP.

## 4. Subir com HTTPS

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker compose ps
```

Na primeira subida, o Caddy leva alguns segundos para obter o certificado. Para testar:

```bash
curl https://$DOMINIO/saude
curl -I http://$DOMINIO/saude   # 308 -> redireciona para HTTPS
```

## 5. Ver os traces (Jaeger) sem expor a interface

Em produção, o Jaeger escuta só no localhost da VM. Abra um túnel SSH:

```bash
ssh -L 16686:localhost:16686 usuario@IP_DA_VM
# e acesse http://localhost:16686 no seu navegador
```

## 6. Atualizar

```bash
git pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Depois do deploy, registre a URL pública na seção **"URL pública"** do README.
