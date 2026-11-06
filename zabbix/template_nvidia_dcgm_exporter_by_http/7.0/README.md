# NVIDIA DCGM exporter by HTTP

Monitors NVIDIA GPUs through [dcgm-exporter](https://github.com/NVIDIA/dcgm-exporter) default counters: utilization, memory, temperature, power, clocks, XID errors and HBM row remapping. GPUs are discovered automatically.

## Requirements

Zabbix 7.0 or newer. Zabbix server or proxy must reach the metrics URL.

## Setup

1. Run dcgm-exporter (GPU Operator or standalone, port 9400).
2. Import the template and link it to a host per GPU node.
3. Set `{$DCGM.METRICS.URL}` (default `http://localhost:9400/metrics`).

## Macros

| Macro | Default | Description |
|---|---|---|
| `{$DCGM.METRICS.URL}` | `http://localhost:9400/metrics` | dcgm-exporter /metrics URL. |
| `{$DCGM.INTERVAL}` | `1m` | Polling interval. |
| `{$DCGM.GPU.TEMP.MAX.WARN}` | `85` | GPU temperature (C) above this for 10m raises a warning. |
| `{$DCGM.FB.USED.MAX.WARN}` | `98` | Framebuffer usage (%) above this for 15m raises a warning. |
| `{$DCGM.NODATA.TIMEOUT}` | `5m` | No data period before the unavailable trigger fires. |

## Items

| Name | Key | Units |
|---|---|---|
| Get metrics | `dcgm.get_metrics` |  |
| GPU count | `dcgm.gpu.count` |  |
| Total power draw | `dcgm.power.total` | W |

## Discovery: GPU discovery

Discovers GPUs from DCGM_FI_DEV_GPU_UTIL.

| Item prototype | Key | Units |
|---|---|---|
| GPU {#GPU}: GPU utilization | `dcgm.gpu.util["{#GPU}"]` | % |
| GPU {#GPU}: Memory copy utilization | `dcgm.gpu.mem_copy_util["{#GPU}"]` | % |
| GPU {#GPU}: GPU temperature | `dcgm.gpu.temp["{#GPU}"]` | °C |
| GPU {#GPU}: Memory temperature | `dcgm.gpu.mem_temp["{#GPU}"]` | °C |
| GPU {#GPU}: Power draw | `dcgm.gpu.power["{#GPU}"]` | W |
| GPU {#GPU}: SM clock | `dcgm.gpu.sm_clock["{#GPU}"]` | Hz |
| GPU {#GPU}: Framebuffer used | `dcgm.gpu.fb_used["{#GPU}"]` | B |
| GPU {#GPU}: Framebuffer free | `dcgm.gpu.fb_free["{#GPU}"]` | B |
| GPU {#GPU}: Last XID error | `dcgm.gpu.xid["{#GPU}"]` |  |
| GPU {#GPU}: PCIe replays per second | `dcgm.gpu.pcie_replay.rate["{#GPU}"]` | replays/s |
| GPU {#GPU}: Uncorrectable remapped rows | `dcgm.gpu.remapped_rows.uncorrectable["{#GPU}"]` |  |
| GPU {#GPU}: Row remap failure | `dcgm.gpu.row_remap_failure["{#GPU}"]` |  |
| GPU {#GPU}: Framebuffer used, in % | `dcgm.gpu.fb_used.pct["{#GPU}"]` | % |

## Triggers

| Name | Severity |
|---|---|
| DCGM: Metrics endpoint is unavailable | High |
| DCGM: Number of GPUs decreased | High |
| GPU {#GPU}: XID error {ITEM.LASTVALUE1} reported | Warning |
| GPU {#GPU}: fatal XID error {ITEM.LASTVALUE1} reported | High |
| GPU {#GPU}: temperature is high | Warning |
| GPU {#GPU}: HBM row remapping failed | High |
| GPU {#GPU}: framebuffer is almost full | Info |

## Feedback

Issues and pull requests: https://github.com/lbarrosby/ai-observability-pack
