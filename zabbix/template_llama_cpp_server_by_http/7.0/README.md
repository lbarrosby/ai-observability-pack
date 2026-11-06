# llama.cpp server by HTTP

Monitors [llama.cpp](https://github.com/ggml-org/llama.cpp) `llama-server` through its Prometheus endpoint: slots, deferred requests, token throughput, prompt cache reuse.

## Requirements

Zabbix 7.0 or newer. Zabbix server or proxy must reach the metrics URL.

## Setup

1. Start `llama-server` with `--metrics`.
2. Import the template and link it to a host.
3. Set `{$LLAMACPP.METRICS.URL}` (default `http://localhost:8080/metrics`). If the server uses `--api-key`, add an `Authorization: Bearer` header to the *Get metrics* item.

## Macros

| Macro | Default | Description |
|---|---|---|
| `{$LLAMACPP.METRICS.URL}` | `http://localhost:8080/metrics` | llama-server /metrics URL. |
| `{$LLAMACPP.INTERVAL}` | `1m` | Polling interval. |
| `{$LLAMACPP.DEFERRED.MAX.WARN}` | `2` | Deferred requests above this for 10m raise a warning. |
| `{$LLAMACPP.NODATA.TIMEOUT}` | `5m` | No data period before the unavailable trigger fires. |

## Items

| Name | Key | Units |
|---|---|---|
| Get metrics | `llamacpp.get_metrics` |  |
| Requests processing | `llamacpp.requests.processing` |  |
| Requests deferred | `llamacpp.requests.deferred` |  |
| Prompt tokens per second | `llamacpp.prompt_tokens.rate` | tok/s |
| Prompt tokens from cache per second | `llamacpp.prompt_tokens_cached.rate` | tok/s |
| Generated tokens per second | `llamacpp.tokens_predicted.rate` | tok/s |
| llama_decode calls per second | `llamacpp.n_decode.rate` | calls/s |
| Average prompt throughput | `llamacpp.prompt_speed` | tok/s |
| Average generation throughput | `llamacpp.generation_speed` | tok/s |
| Busy slots per decode | `llamacpp.busy_slots_per_decode` |  |
| Largest sequence length | `llamacpp.n_tokens_max` |  |
| Prompt cache reuse ratio | `llamacpp.prompt_cache.ratio` | % |

## Triggers

| Name | Severity |
|---|---|
| llama.cpp: Metrics endpoint is unavailable | High |
| llama.cpp: Requests are being deferred | Warning |

## Feedback

Issues and pull requests: https://github.com/lbarrosby/ai-observability-pack
