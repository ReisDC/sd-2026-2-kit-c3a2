# Relatório de trade-offs

**CAP**

CAP relaciona consistência e disponibilidade durante uma partição de rede.
O projeto não usa dados replicados, então não recebe aqui uma classificação CP/AP.
Se a rede falhar entre gateway e inferência, a requisição retorna erro 503.
Cada serviço é um ponto único de falha; não há redundância para manter o atendimento.

**Custo**

O uso de containers permite rodar os serviços na mesma máquina para a demonstração.
Separá-los facilita manutenção, mas aumenta o consumo de memória e as chamadas de
rede. Um plano gratuito de nuvem pode reduzir o custo da entrega, dependendo dos
limites do provedor. O deploy ainda está pendente.

**Latência**

O gateway precisa esperar a resposta da inferência. O timeout padrão é de 5 segundos
por tentativa, com até 3 tentativas e espera progressiva entre elas. Isso ajuda em
falhas temporárias, mas faz o cliente esperar mais. O circuit breaker recusa novas
chamadas temporariamente quando o serviço acumula falhas.

**Limites atuais**

Ainda falta integrar a inferência e completar os outros serviços. Não foram feitos
testes de carga; portanto, não há medidas de capacidade ou latência real do sistema.
