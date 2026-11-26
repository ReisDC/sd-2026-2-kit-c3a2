/**
 * Infraestrutura como codigo no Railway (https://docs.railway.com/infrastructure-as-code).
 *
 * Descreve os 11 servicos da plataforma no mesmo projeto. Cada servico de aplicacao
 * e buildado a partir deste repositorio com o Dockerfile dele (contexto na raiz,
 * igual ao docker-compose). Aplicar:
 *
 *   railway link            # escolhe o projeto e o ambiente
 *   railway config plan     # mostra o que sera criado/alterado
 *   railway config apply
 *
 * Segredos NAO ficam aqui: os marcados com preserve() sao definidos no painel do
 * Railway (veja deploy/INSTRUCOES.md) e o apply mantem o valor que estiver la.
 *
 * Atencao: referencias entre servicos usam a sintaxe do Railway "${{servico.VAR}}"
 * em strings comuns (aspas), nunca em template literals do TypeScript.
 */
import { defineRailway, github, image, preserve, project, redis, service, volume } from "railway/iac";
import type { VariableValue } from "railway/iac";

const REPOSITORIO = "ReisDC/sd-2026-2-kit-c3a2";
const OTEL = { OTEL_EXPORTER_OTLP_ENDPOINT: "http://${{jaeger.RAILWAY_PRIVATE_DOMAIN}}:4318" };

/** Servico buildado a partir do repositorio com o Dockerfile informado. */
function aplicacao(
  nome: string,
  dockerfile: string,
  pastas: string[],
  inicio: string,
  env: Record<string, VariableValue | string>,
  healthcheck?: string,
) {
  return service(nome, {
    source: github(REPOSITORIO, { branch: "main" }),
    build: {
      builder: "DOCKERFILE",
      dockerfilePath: dockerfile,
      // So redeploya quando o codigo do servico (ou o compartilhado) muda.
      watchPatterns: [...pastas, "comum/**"],
    },
    deploy: {
      startCommand: inicio,
      restartPolicyType: "ON_FAILURE",
      restartPolicyMaxRetries: 10,
      ...(healthcheck ? { healthcheckPath: healthcheck, healthcheckTimeout: 120 } : {}),
    },
    env: { ...OTEL, ...env },
  });
}

export default defineRailway(() => {
  // ------------------------------------------------------------ infraestrutura
  const cache = redis("redis");
  const dadosRabbit = volume("rabbitmq-dados");

  const rabbitmq = service("rabbitmq", {
    source: image("rabbitmq:3.13-alpine"),
    env: {
      RABBITMQ_DEFAULT_USER: "plataforma",
      RABBITMQ_DEFAULT_PASS: preserve(), // defina no painel (so letras e numeros)
    },
    volumeMounts: { "/var/lib/rabbitmq": dadosRabbit },
  });

  const jaeger = service("jaeger", {
    source: image("jaegertracing/all-in-one:1.62.0"),
    env: { COLLECTOR_OTLP_ENABLED: "true" },
  });

  const URL_RABBITMQ =
    "amqp://${{rabbitmq.RABBITMQ_DEFAULT_USER}}:${{rabbitmq.RABBITMQ_DEFAULT_PASS}}" +
    "@${{rabbitmq.RAILWAY_PRIVATE_DOMAIN}}:5672/%2F";

  // ------------------------------------------------ inferencia (REST + fila | gRPC)
  // Servicos internos escutam em "::": a rede privada do Railway usa IPv6.
  const inferencia = aplicacao(
    "inferencia", "inferencia/Dockerfile", ["inferencia/**", "proto/**"],
    "uvicorn app:app --host :: --port 8001 --no-access-log",
    { REDIS_URL: cache.env.REDIS_URL },
    "/saude",
  );
  const inferenciaGrpc = aplicacao(
    "inferencia-grpc", "inferencia/Dockerfile", ["inferencia/**", "proto/**"],
    "python servidor_grpc.py",
    { GRPC_PORTA: "50051" },
  );
  const worker = aplicacao(
    "worker", "worker/Dockerfile", ["worker/**"],
    "python worker.py",
    { REDIS_URL: cache.env.REDIS_URL },
  );

  // ------------------------------------------------------------------- RAG
  // O indice vetorial vai para o Redis: no Railway um volume nao e compartilhado
  // entre a ingestao e o worker de indexacao.
  const ingestao = aplicacao(
    "ingestao", "ingestion-service/Dockerfile", ["ingestion-service/**"],
    "uvicorn app:app --host :: --port 8002 --no-access-log",
    { RABBITMQ_URL: URL_RABBITMQ, INDICE_REDIS_URL: cache.env.REDIS_URL },
    "/saude",
  );
  const workerIndexacao = aplicacao(
    "worker-indexacao", "ingestion-service/Dockerfile", ["ingestion-service/**"],
    "python worker_indexacao.py",
    { RABBITMQ_URL: URL_RABBITMQ, INDICE_REDIS_URL: cache.env.REDIS_URL },
  );
  const recuperacao = aplicacao(
    "recuperacao", "retrieval-service/Dockerfile", ["retrieval-service/**"],
    "uvicorn app:app --host :: --port 8003 --no-access-log",
    { URL_INGESTAO: "http://${{ingestao.RAILWAY_PRIVATE_DOMAIN}}:8002" },
    "/saude",
  );
  const geracao = aplicacao(
    "geracao", "generation-service/Dockerfile", ["generation-service/**"],
    "uvicorn app:app --host :: --port 8004 --no-access-log",
    {
      URL_RECUPERACAO: "http://${{recuperacao.RAILWAY_PRIVATE_DOMAIN}}:8003",
      LLM_API_KEY: preserve(), // vazio = modo simulado
    },
    "/saude",
  );

  // ------------------------------------------------- gateway (unico publico)
  // Escuta em 0.0.0.0 para o proxy publico; o dominio HTTPS e gerado no painel.
  const gateway = aplicacao(
    "gateway", "gateway/Dockerfile", ["gateway/**", "proto/**"],
    "uvicorn app:app --host 0.0.0.0 --port 8000 --no-access-log --proxy-headers --forwarded-allow-ips *",
    {
      JWT_SEGREDO: preserve(),
      GATEWAY_USUARIO: "admin",
      GATEWAY_SENHA: preserve(),
      RATE_LIMIT_POR_MINUTO: "30",
      URL_INFERENCIA: "http://${{inferencia.RAILWAY_PRIVATE_DOMAIN}}:8001",
      GRPC_INFERENCIA: "${{inferencia-grpc.RAILWAY_PRIVATE_DOMAIN}}:50051",
      URL_INGESTAO: "http://${{ingestao.RAILWAY_PRIVATE_DOMAIN}}:8002",
      URL_RECUPERACAO: "http://${{recuperacao.RAILWAY_PRIVATE_DOMAIN}}:8003",
      URL_GERACAO: "http://${{geracao.RAILWAY_PRIVATE_DOMAIN}}:8004",
    },
    "/saude",
  );

  return project("plataforma-ia-c3a2", {
    resources: [
      cache, dadosRabbit, rabbitmq, jaeger,
      inferencia, inferenciaGrpc, worker,
      ingestao, workerIndexacao, recuperacao, geracao,
      gateway,
    ],
  });
});
