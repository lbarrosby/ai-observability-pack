# vLLM by HTTP

Monitors a [vLLM](https://github.com/vllm-project/vllm) OpenAI-compatible server (V1 engine) through its Prometheus endpoint: scheduler queue, KV cache, preemptions, token throughput and average latencies. Served models are discovered automatically.

## Requirements

Zabbix 7.0 or newer. Zabbix server or proxy must reach the metrics URL.

## Setup

1. vLLM exposes `/metrics` on the API port by default.
2. Import the template and link it to a host.
3. Set `{$VLLM.METRICS.URL}` (default `http://localhost:8000/metrics`).
4. Latency items are averages over the polling interval (histogram sum rate divided by count rate). Use Prometheus/Grafana for percentiles.

## Macros

| Macro | Default | Description |
|---|---|---|
| `{$VLLM.METRICS.URL}` | `http://localhost:8000/metrics` | vLLM /metrics URL. |
| `{$VLLM.INTERVAL}` | `1m` | Polling interval. |
| `{$VLLM.WAITING.MAX.WARN}` | `10` | Waiting requests above this for 10m raise a warning. |
| `{$VLLM.KV_CACHE.MAX.WARN}` | `95` | KV cache usage (%) above this for 15m raises a warning. |
| `{$VLLM.PREEMPTIONS.MAX.WARN}` | `0.1` | Preemptions per second above this for 15m raise a warning. |
| `{$VLLM.TTFT.MAX.WARN}` | `2` | Average time to first token (s) above this for 10m raises a warning. |
| `{$VLLM.NODATA.TIMEOUT}` | `5m` | No data period before the unavailable trigger fires. |

## Items

| Name | Key | Units |
|---|---|---|
| Get metrics | `vllm.get_metrics` |  |
| Requests running | `vllm.requests.running` |  |
| Requests waiting | `vllm.requests.waiting` |  |
| KV cache usage | `vllm.kv_cache.usage` | % |
| Preemptions per second | `vllm.preemptions.rate` | preemptions/s |
| Prompt tokens per second | `vllm.prompt_tokens.rate` | tok/s |
| Generation tokens per second | `vllm.generation_tokens.rate` | tok/s |
| Finished requests per second | `vllm.requests.finished.rate` | rps |
| Aborted requests per second | `vllm.requests.aborted.rate` | rps |
| Time to first token: sum rate | `vllm.ttft.sum.rate` | s |
| Time to first token: count rate | `vllm.ttft.count.rate` |  |
| Average time to first token | `vllm.ttft.avg` | s |
| End-to-end latency: sum rate | `vllm.e2e.sum.rate` | s |
| End-to-end latency: count rate | `vllm.e2e.count.rate` |  |
| Average end-to-end latency | `vllm.e2e.avg` | s |
| Inter-token latency: sum rate | `vllm.itl.sum.rate` | s |
| Inter-token latency: count rate | `vllm.itl.count.rate` |  |
| Average inter-token latency | `vllm.itl.avg` | s |
| Queue time: sum rate | `vllm.queue.sum.rate` | s |
| Queue time: count rate | `vllm.queue.count.rate` |  |
| Average queue time | `vllm.queue.avg` | s |

## Discovery: Model discovery

Discovers served models from the model_name label.

| Item prototype | Key | Units |
|---|---|---|
| Model {#MODEL}: Requests running | `vllm.model.running["{#MODEL}"]` |  |
| Model {#MODEL}: Requests waiting | `vllm.model.waiting["{#MODEL}"]` |  |
| Model {#MODEL}: KV cache usage | `vllm.model.kv_cache["{#MODEL}"]` | % |
| Model {#MODEL}: Generation tokens per second | `vllm.model.generation_tokens.rate["{#MODEL}"]` | tok/s |
| Model {#MODEL}: Prompt tokens per second | `vllm.model.prompt_tokens.rate["{#MODEL}"]` | tok/s |

## Triggers

| Name | Severity |
|---|---|
| vLLM: Metrics endpoint is unavailable | High |
| vLLM: Requests are queueing | Warning |
| vLLM: KV cache is nearly full | Warning |
| vLLM: Requests are being preempted | Warning |
| vLLM: Time to first token is high | Warning |
| vLLM: Model {#MODEL}: requests are queueing | Warning |

## Feedback

Issues and pull requests: https://github.com/lbarrosby/ai-observability-pack
