# Avaliação: data source plugin próprio para Grafana

Data: 05/11/2026. Escopo: decidir se vale construir um data source plugin (por exemplo para a API OpenAI-compatible ou para custo de tokens) em vez de entregar só dashboards sobre Prometheus.

## Recomendação

**Não construir o plugin agora.** Publicar os dashboards sobre Prometheus primeiro e cobrir custo com recording rules e dashboards. Reavaliar o plugin se aparecer demanda real (issues, estrelas, pedidos) por uma visão unificada de uso e custo de **provedores pagos** (OpenAI, Anthropic, Azure OpenAI, Bedrock) que o Infinity não resolva bem.

## Por que não agora

1. **Os servidores OpenAI-compatible não expõem métricas pela API.** vLLM, SGLang, llama.cpp, TEI, Triton e LiteLLM já publicam `/metrics` em Prometheus. A API OpenAI-compatible (`/v1/models`, `/v1/chat/completions`) não tem endpoint de uso ou latência. Um plugin que fala com ela só teria a lista de modelos e precisaria gerar tráfego sintético para medir alguma coisa, o que é papel de blackbox exporter ou k6, não de data source.
2. **Custo de token self-hosted é conta, não integração.** Custo por milhão de tokens = custo/hora da GPU ÷ tokens/hora. Os tokens já estão no Prometheus (`vllm:generation_tokens_total`, `sglang:generation_tokens_total`, `llamacpp:tokens_predicted_total`) e o custo/hora vem de OpenCost (`node_gpu_hourly_cost`) ou de uma constante por tipo de GPU. Uma recording rule resolve, sem código novo.
3. **Custo no gateway já existe.** O LiteLLM exporta `litellm_spend_metric_total` por modelo, time e chave; o dashboard `litellm.json` deste pacote já mostra gasto por modelo, time e chave virtual.
4. **APIs de uso dos provedores já são alcançáveis sem plugin.** As APIs administrativas de uso e custo (OpenAI organization usage/costs, Anthropic usage and cost report) são REST com JSON. O plugin **Infinity** (`yesoreyeram-infinity-datasource`, mantido pela Grafana Labs) consulta REST/JSON com header de autenticação, faz parsing no backend e suporta alertas. Cobre o caso sem escrever Go.
5. **Custo de manter um plugin é alto para um projeto de uma pessoa.**
   - Precisa de backend em Go (para guardar chave em `secureJsonData` e permitir alertas), frontend em React/TypeScript e acompanhar as mudanças do SDK a cada major do Grafana.
   - Para entrar no catálogo do grafana.com o plugin precisa ser assinado; a assinatura de comunidade passa por revisão da Grafana Labs, que leva semanas e exige manutenção contínua.
   - Cada provedor muda formato e paginação da API de uso; o plugin vira um adaptador permanente.
   - Superfície de segurança: o plugin guarda chaves administrativas de billing.
6. **Distribuição e alcance.** Dashboards no grafana.com são importados com um ID e funcionam em qualquer Grafana com Prometheus. Plugin exige instalação no servidor (bloqueado em muitas empresas e no Grafana Cloud só com plugin assinado).

## Quando um plugin passaria a fazer sentido

Construir só se pelo menos dois destes forem verdade:

- Usuários pedindo uma visão **normalizada multiprovedor** (mesmo schema de modelo, tokens e custo para OpenAI, Anthropic, Azure, Bedrock, Vertex), coisa que no Infinity vira uma query diferente por provedor.
- Necessidade de **paginação/cursor e agregação** que o Infinity não faz bem em escala (meses de dados diários por projeto e chave).
- Demanda por **alertas de orçamento** sobre APIs de provedor com credencial guardada de forma segura e sem copiar JSONPath em cada painel.

Se acontecer, o desenho seria: backend plugin em Go chamado algo como "LLM usage and cost", com um tipo de query por recurso (uso, custo, rate limits), saída em data frames normalizados (`time`, `provider`, `model`, `project`, `input_tokens`, `output_tokens`, `cost_usd`), cache no backend e suporte a alerting.

## O que fazer no lugar (próximos passos de baixo custo)

1. **Recording rules de custo self-hosted** (`alerts/` ou um `rules/cost.yaml` novo):
   - `ai:generation_tokens:rate5m` por `namespace`, `model_name`.
   - `ai:gpu_hourly_cost` por `namespace`/`pod` via OpenCost ou constante por `modelName` do DCGM.
   - `ai:cost_per_million_output_tokens = ai:gpu_hourly_cost / (ai:generation_tokens:rate5m * 3600) * 1e6`.
2. **Dashboard "AI cost"** juntando: custo self-hosted (regras acima), gasto do gateway (LiteLLM) e, opcionalmente, painéis Infinity para as APIs de uso dos provedores, com instruções de configuração no README.
3. **Dashboard de métricas OpenTelemetry GenAI** (`gen_ai.client.token.usage`, `gen_ai.client.operation.duration`, convenções semânticas GenAI do OTel). É o caminho para métricas do lado da aplicação (SDKs instrumentados, OpenLIT, OpenLLMetry) e não precisa de plugin.

## Resumo

| Opção | Valor | Custo | Decisão |
|---|---|---|---|
| Dashboards sobre Prometheus (este pacote) | Alto, cobre os servidores self-hosted e o gateway | Baixo | Fazer agora |
| Recording rules de custo + dashboard AI cost | Alto para FinOps | Baixo | Próximo passo |
| Infinity para APIs de uso dos provedores | Médio | Baixo (config) | Documentar |
| Data source plugin próprio | Médio, nicho | Alto e contínuo | Adiar, reavaliar por demanda |
