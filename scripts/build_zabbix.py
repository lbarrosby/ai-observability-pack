#!/usr/bin/env python3
"""Generate Zabbix 7.0 template exports (YAML) in ../zabbix.

Each template polls the Prometheus endpoint once with an HTTP agent item and
derives every other item with Prometheus pattern preprocessing.
UUIDs are deterministic so regenerating does not create duplicates on import.
"""
import hashlib
import os

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "zabbix")
GROUP = "Templates/Applications"
GROUP_UUID = "a571c0d144b14fd4a87a9d9b2aa9fcd6"
VENDOR = {"name": "ai-observability-pack", "version": "7.0-1"}


def uid(*parts):
    h = bytearray(hashlib.md5("|".join(parts).encode()).digest())
    h[6] = (h[6] & 0x0F) | 0x40
    h[8] = (h[8] & 0x3F) | 0x80
    return h.hex()


# ------------------------------------------------------------------ YAML out
def _scalar(v):
    if isinstance(v, bool):
        return "YES" if v else "NO"
    if isinstance(v, int):
        return f"'{v}'"
    s = str(v)
    if "\n" in s:
        return None
    return "'" + s.replace("'", "''") + "'"


def dump(obj, indent=0):
    pad = " " * indent
    lines = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                if not v:
                    continue
                lines.append(f"{pad}{k}:")
                lines.extend(dump(v, indent + 2))
            else:
                sc = _scalar(v)
                if sc is None:
                    lines.append(f"{pad}{k}: |")
                    lines.extend(f"{pad}  {ln}" for ln in str(v).split("\n"))
                else:
                    lines.append(f"{pad}{k}: {sc}")
    else:
        for item in obj:
            if isinstance(item, (dict, list)):
                sub = dump(item, indent + 2)
                sub[0] = pad + "- " + sub[0].lstrip()
                lines.extend(sub)
            else:
                lines.append(f"{pad}- {_scalar(item)}")
    return lines


# ----------------------------------------------------------------- builders
class Template:
    def __init__(self, name, description, macros, master_key, url_macro, tags):
        self.name = name
        self.description = description
        self.macros = macros
        self.master_key = master_key
        self.items = []
        self.lld = []
        self.tags = tags
        self.items.append({
            "uuid": uid(name, master_key), "name": "Get metrics", "type": "HTTP_AGENT", "key": master_key,
            "delay": "{$" + url_macro.split(".")[0] + ".INTERVAL}", "history": "0", "trends": "0",
            "value_type": "TEXT", "description": "Raw Prometheus exposition. Every other item depends on it.",
            "url": "{$" + url_macro + "}",
            "tags": [{"tag": "component", "value": "raw"}]})

    def dep(self, key, name, pattern, units="", agg="sum", per_second=False, multiplier=None, desc="",
            component="", triggers=None, value_type="FLOAT", on_fail_zero=False):
        pre = [{"type": "PROMETHEUS_PATTERN", "parameters": [pattern, "function", agg]}]
        if on_fail_zero:
            pre[0]["error_handler"] = "CUSTOM_VALUE"
            pre[0]["error_handler_params"] = "0"
        if multiplier:
            pre.append({"type": "MULTIPLIER", "parameters": [str(multiplier)]})
        if per_second:
            pre.append({"type": "CHANGE_PER_SECOND", "parameters": [""]})
        item = {"uuid": uid(self.name, key), "name": name, "type": "DEPENDENT", "key": key, "delay": "0",
                "value_type": value_type, "units": units, "description": desc, "preprocessing": pre,
                "master_item": {"key": self.master_key},
                "tags": [{"tag": "component", "value": component}] if component else []}
        if triggers:
            item["triggers"] = [dict(t, uuid=uid(self.name, key, t["name"])) for t in triggers]
        self.items.append(item)
        return item

    def calc(self, key, name, formula, units="", desc="", component="", triggers=None):
        item = {"uuid": uid(self.name, key), "name": name, "type": "CALCULATED", "key": key,
                "delay": "{$" + self.macros[0]["macro"].strip("{}$").split(".")[0] + ".INTERVAL}",
                "value_type": "FLOAT", "units": units, "params": formula, "description": desc,
                "tags": [{"tag": "component", "value": component}] if component else []}
        if triggers:
            item["triggers"] = [dict(t, uuid=uid(self.name, key, t["name"])) for t in triggers]
        self.items.append(item)
        return item

    def to_export(self):
        tpl = {"uuid": uid(self.name), "template": self.name, "name": self.name, "description": self.description,
               "vendor": VENDOR, "groups": [{"name": GROUP}], "items": self.items,
               "discovery_rules": self.lld, "tags": self.tags, "macros": self.macros}
        return {"zabbix_export": {"version": "7.0",
                                  "template_groups": [{"uuid": GROUP_UUID, "name": GROUP}],
                                  "templates": [tpl]}}


def trig(name, expr, priority="WARNING", desc="", manual_close=False):
    t = {"expression": expr, "name": name, "priority": priority, "description": desc}
    if manual_close:
        t["manual_close"] = True
    return t


def macro(m, v, d):
    return {"macro": "{$" + m + "}", "value": v, "description": d}


# ---------------------------------------------------------------- llama.cpp
def llamacpp():
    T = "llama.cpp server by HTTP"
    t = Template(T, "Monitors llama.cpp llama-server through its Prometheus endpoint. Start the server with "
                    "--metrics. Set {$LLAMACPP.METRICS.URL} on the host.\n\n"
                    "Source: https://github.com/lbarrosby/ai-observability-pack",
                 [macro("LLAMACPP.METRICS.URL", "http://localhost:8080/metrics", "llama-server /metrics URL."),
                  macro("LLAMACPP.INTERVAL", "1m", "Polling interval."),
                  macro("LLAMACPP.DEFERRED.MAX.WARN", "2", "Deferred requests above this for 10m raise a warning."),
                  macro("LLAMACPP.NODATA.TIMEOUT", "5m", "No data period before the unavailable trigger fires.")],
                 "llamacpp.get_metrics", "LLAMACPP.METRICS.URL",
                 [{"tag": "class", "value": "software"}, {"tag": "target", "value": "llama.cpp"}])
    t.dep("llamacpp.requests.processing", "Requests processing", "llamacpp:requests_processing",
          desc="Requests currently using a slot.", component="requests",
          triggers=[trig("llama.cpp: Metrics endpoint is unavailable",
                         f"nodata(/{T}/llamacpp.requests.processing,{{$LLAMACPP.NODATA.TIMEOUT}})=1", "HIGH",
                         "No data from {$LLAMACPP.METRICS.URL}. The server is down or started without --metrics.")])
    t.dep("llamacpp.requests.deferred", "Requests deferred", "llamacpp:requests_deferred",
          desc="Requests waiting for a free slot.", component="requests",
          triggers=[trig("llama.cpp: Requests are being deferred",
                         f"min(/{T}/llamacpp.requests.deferred,10m)>{{$LLAMACPP.DEFERRED.MAX.WARN}}", "WARNING",
                         "Requests wait for a free slot. Raise --parallel or add replicas.")])
    t.dep("llamacpp.prompt_tokens.rate", "Prompt tokens per second", "llamacpp:prompt_tokens_total",
          "tok/s", per_second=True, desc="Prompt tokens evaluated per second (cache hits excluded).",
          component="throughput")
    t.dep("llamacpp.prompt_tokens_cached.rate", "Prompt tokens from cache per second",
          "llamacpp:prompt_tokens_cached_total", "tok/s", per_second=True,
          desc="Prompt tokens reused from the KV cache per second.", component="throughput")
    t.dep("llamacpp.tokens_predicted.rate", "Generated tokens per second", "llamacpp:tokens_predicted_total",
          "tok/s", per_second=True, desc="Tokens generated per second.", component="throughput")
    t.dep("llamacpp.n_decode.rate", "llama_decode calls per second", "llamacpp:n_decode_total", "!calls/s",
          per_second=True, desc="llama_decode() calls per second.", component="throughput")
    t.dep("llamacpp.prompt_speed", "Average prompt throughput", "llamacpp:prompt_tokens_seconds", "tok/s",
          desc="Average prompt throughput reported by the server.", component="speed")
    t.dep("llamacpp.generation_speed", "Average generation throughput", "llamacpp:predicted_tokens_seconds",
          "tok/s", desc="Average generation throughput reported by the server.", component="speed")
    t.dep("llamacpp.busy_slots_per_decode", "Busy slots per decode", "llamacpp:n_busy_slots_per_decode",
          desc="Average number of slots batched per llama_decode() call.", component="slots")
    t.dep("llamacpp.n_tokens_max", "Largest sequence length", "llamacpp:n_tokens_max", agg="max",
          desc="Largest observed sequence length (prompt + generation).", component="slots")
    t.calc("llamacpp.prompt_cache.ratio", "Prompt cache reuse ratio",
           f"100*last(//llamacpp.prompt_tokens_cached.rate)/(last(//llamacpp.prompt_tokens_cached.rate)+last(//llamacpp.prompt_tokens.rate)+0.000001)",
           "%", "Share of prompt tokens served from cache.", "cache")
    return t


# --------------------------------------------------------------------- vLLM
def vllm():
    T = "vLLM by HTTP"
    t = Template(T, "Monitors a vLLM OpenAI-compatible server (V1 engine) through its Prometheus endpoint. "
                    "Set {$VLLM.METRICS.URL} on the host. Models are discovered from the model_name label.\n\n"
                    "Source: https://github.com/lbarrosby/ai-observability-pack",
                 [macro("VLLM.METRICS.URL", "http://localhost:8000/metrics", "vLLM /metrics URL."),
                  macro("VLLM.INTERVAL", "1m", "Polling interval."),
                  macro("VLLM.WAITING.MAX.WARN", "10", "Waiting requests above this for 10m raise a warning."),
                  macro("VLLM.KV_CACHE.MAX.WARN", "95", "KV cache usage (%) above this for 15m raises a warning."),
                  macro("VLLM.PREEMPTIONS.MAX.WARN", "0.1", "Preemptions per second above this for 15m raise a warning."),
                  macro("VLLM.TTFT.MAX.WARN", "2", "Average time to first token (s) above this for 10m raises a warning."),
                  macro("VLLM.NODATA.TIMEOUT", "5m", "No data period before the unavailable trigger fires.")],
                 "vllm.get_metrics", "VLLM.METRICS.URL",
                 [{"tag": "class", "value": "software"}, {"tag": "target", "value": "vllm"}])
    t.dep("vllm.requests.running", "Requests running", "vllm:num_requests_running", component="scheduler",
          desc="Requests in the running batch (all models).",
          triggers=[trig("vLLM: Metrics endpoint is unavailable",
                         f"nodata(/{T}/vllm.requests.running,{{$VLLM.NODATA.TIMEOUT}})=1", "HIGH",
                         "No data from {$VLLM.METRICS.URL}.")])
    t.dep("vllm.requests.waiting", "Requests waiting", "vllm:num_requests_waiting", component="scheduler",
          desc="Requests queued by the scheduler (all models).",
          triggers=[trig("vLLM: Requests are queueing",
                         f"min(/{T}/vllm.requests.waiting,10m)>{{$VLLM.WAITING.MAX.WARN}}", "WARNING",
                         "The engine is saturated: check KV cache usage and max_num_seqs.")])
    t.dep("vllm.kv_cache.usage", "KV cache usage", "vllm:kv_cache_usage_perc", "%", agg="max", multiplier=100,
          component="cache", desc="Highest KV cache usage across engines.",
          triggers=[trig("vLLM: KV cache is nearly full",
                         f"min(/{T}/vllm.kv_cache.usage,15m)>{{$VLLM.KV_CACHE.MAX.WARN}}", "WARNING",
                         "New requests wait and running ones get preempted.")])
    t.dep("vllm.preemptions.rate", "Preemptions per second", "vllm:num_preemptions_total", "!preemptions/s",
          per_second=True, component="cache", desc="Requests preempted because the KV cache ran out.",
          triggers=[trig("vLLM: Requests are being preempted",
                         f"min(/{T}/vllm.preemptions.rate,15m)>{{$VLLM.PREEMPTIONS.MAX.WARN}}", "WARNING",
                         "Sustained preemptions waste GPU time: add capacity or lower max_num_seqs.")])
    t.dep("vllm.prompt_tokens.rate", "Prompt tokens per second", "vllm:prompt_tokens_total", "tok/s",
          per_second=True, component="throughput", desc="Prefill tokens processed per second.")
    t.dep("vllm.generation_tokens.rate", "Generation tokens per second", "vllm:generation_tokens_total", "tok/s",
          per_second=True, component="throughput", desc="Output tokens generated per second.")
    t.dep("vllm.requests.finished.rate", "Finished requests per second", "vllm:request_success_total", "rps",
          per_second=True, component="throughput", desc="Requests finished per second (any reason).")
    t.dep("vllm.requests.aborted.rate", "Aborted requests per second",
          'vllm:request_success_total{finished_reason="abort"}', "rps", per_second=True, component="throughput",
          desc="Requests aborted per second (client disconnects and cancellations).", on_fail_zero=True)
    for key, metric, label in [("ttft", "vllm:time_to_first_token_seconds", "time to first token"),
                               ("e2e", "vllm:e2e_request_latency_seconds", "end-to-end latency"),
                               ("itl", "vllm:inter_token_latency_seconds", "inter-token latency"),
                               ("queue", "vllm:request_queue_time_seconds", "queue time")]:
        t.dep(f"vllm.{key}.sum.rate", f"{label.capitalize()}: sum rate", f"{metric}_sum", "s", per_second=True,
              component="raw", desc=f"Per-second increase of the {label} histogram sum.")
        t.dep(f"vllm.{key}.count.rate", f"{label.capitalize()}: count rate", f"{metric}_count", "", per_second=True,
              component="raw", desc=f"Per-second increase of the {label} histogram count.")
        trg = None
        if key == "ttft":
            trg = [trig("vLLM: Time to first token is high",
                        f"min(/{T}/vllm.ttft.avg,10m)>{{$VLLM.TTFT.MAX.WARN}}", "WARNING",
                        "Average TTFT over the polling interval is above the threshold.")]
        t.calc(f"vllm.{key}.avg", f"Average {label}",
               f"last(//vllm.{key}.sum.rate)/(last(//vllm.{key}.count.rate)+0.000001)", "s",
               f"Average {label} over the last polling interval.", "latency", trg)

    disc = {"uuid": uid(T, "vllm.model.discovery"), "name": "Model discovery", "type": "DEPENDENT",
            "key": "vllm.model.discovery", "delay": "0",
            "description": "Discovers served models from the model_name label.",
            "item_prototypes": [], "trigger_prototypes": [],
            "master_item": {"key": "vllm.get_metrics"},
            "preprocessing": [
                {"type": "PROMETHEUS_TO_JSON", "parameters": ['vllm:num_requests_running{model_name=~".+"}']},
                {"type": "JAVASCRIPT", "parameters": [
                    "var seen = {}, out = [];\n"
                    "JSON.parse(value).forEach(function (m) {\n"
                    "    var k = m.labels && m.labels.model_name;\n"
                    "    if (k && !seen[k]) { seen[k] = true; out.push({'{#MODEL}': k}); }\n"
                    "});\n"
                    "return JSON.stringify(out);"]}]}
    protos = [
        ("running", "Requests running", 'vllm:num_requests_running{{model_name="{#MODEL}"}}', "", "sum", False, None),
        ("waiting", "Requests waiting", 'vllm:num_requests_waiting{{model_name="{#MODEL}"}}', "", "sum", False, None),
        ("kv_cache", "KV cache usage", 'vllm:kv_cache_usage_perc{{model_name="{#MODEL}"}}', "%", "max", False, 100),
        ("generation_tokens.rate", "Generation tokens per second",
         'vllm:generation_tokens_total{{model_name="{#MODEL}"}}', "tok/s", "sum", True, None),
        ("prompt_tokens.rate", "Prompt tokens per second",
         'vllm:prompt_tokens_total{{model_name="{#MODEL}"}}', "tok/s", "sum", True, None),
    ]
    for key, name, pat, units, agg, ps, mult in protos:
        pre = [{"type": "PROMETHEUS_PATTERN", "parameters": [pat.replace("{{", "{").replace("}}", "}"), "function", agg]}]
        if mult:
            pre.append({"type": "MULTIPLIER", "parameters": [str(mult)]})
        if ps:
            pre.append({"type": "CHANGE_PER_SECOND", "parameters": [""]})
        disc["item_prototypes"].append({
            "uuid": uid(T, "proto", key), "name": "Model {#MODEL}: " + name, "type": "DEPENDENT",
            "key": f'vllm.model.{key}["{{#MODEL}}"]', "delay": "0", "value_type": "FLOAT", "units": units,
            "preprocessing": pre, "master_item": {"key": "vllm.get_metrics"},
            "tags": [{"tag": "component", "value": "model"}, {"tag": "model", "value": "{#MODEL}"}]})
    disc["trigger_prototypes"].append({
        "uuid": uid(T, "proto-trigger-waiting"),
        "expression": f'min(/{T}/vllm.model.waiting["{{#MODEL}}"],10m)>{{$VLLM.WAITING.MAX.WARN}}',
        "name": "vLLM: Model {#MODEL}: requests are queueing", "priority": "WARNING",
        "description": "Waiting requests for this model stayed above the threshold for 10 minutes."})
    t.lld.append(disc)
    return t


# --------------------------------------------------------------------- DCGM
def dcgm():
    T = "NVIDIA DCGM exporter by HTTP"
    t = Template(T, "Monitors NVIDIA GPUs through dcgm-exporter (default counters). Set {$DCGM.METRICS.URL} "
                    "on the host. GPUs are discovered from the gpu label.\n\n"
                    "Source: https://github.com/lbarrosby/ai-observability-pack",
                 [macro("DCGM.METRICS.URL", "http://localhost:9400/metrics", "dcgm-exporter /metrics URL."),
                  macro("DCGM.INTERVAL", "1m", "Polling interval."),
                  macro("DCGM.GPU.TEMP.MAX.WARN", "85", "GPU temperature (C) above this for 10m raises a warning."),
                  macro("DCGM.FB.USED.MAX.WARN", "98", "Framebuffer usage (%) above this for 15m raises a warning."),
                  macro("DCGM.NODATA.TIMEOUT", "5m", "No data period before the unavailable trigger fires.")],
                 "dcgm.get_metrics", "DCGM.METRICS.URL",
                 [{"tag": "class", "value": "hardware"}, {"tag": "target", "value": "nvidia-gpu"}])
    t.dep("dcgm.gpu.count", "GPU count", "DCGM_FI_DEV_GPU_UTIL", agg="count", component="inventory",
          desc="Number of GPUs exposed by the exporter.",
          triggers=[trig("DCGM: Metrics endpoint is unavailable",
                         f"nodata(/{T}/dcgm.gpu.count,{{$DCGM.NODATA.TIMEOUT}})=1", "HIGH",
                         "No data from {$DCGM.METRICS.URL}."),
                    trig("DCGM: Number of GPUs decreased",
                         f"last(/{T}/dcgm.gpu.count)<last(/{T}/dcgm.gpu.count,#2)", "HIGH",
                         "A GPU disappeared (fell off the bus, driver reset or exporter restart).",
                         manual_close=True)])
    t.dep("dcgm.power.total", "Total power draw", "DCGM_FI_DEV_POWER_USAGE", "W", component="power",
          desc="Sum of power draw over all GPUs.")
    disc = {"uuid": uid(T, "dcgm.gpu.discovery"), "name": "GPU discovery", "type": "DEPENDENT",
            "key": "dcgm.gpu.discovery", "delay": "0", "description": "Discovers GPUs from DCGM_FI_DEV_GPU_UTIL.",
            "item_prototypes": [], "trigger_prototypes": [], "master_item": {"key": "dcgm.get_metrics"},
            "preprocessing": [{"type": "PROMETHEUS_TO_JSON", "parameters": ["DCGM_FI_DEV_GPU_UTIL"]}],
            "lld_macro_paths": [{"lld_macro": "{#GPU}", "path": "$.labels.gpu"},
                                {"lld_macro": "{#UUID}", "path": "$.labels.UUID"},
                                {"lld_macro": "{#MODEL}", "path": "$.labels.modelName"}]}
    P = [("util", "GPU utilization", "DCGM_FI_DEV_GPU_UTIL", "%", None, False, "utilization"),
         ("mem_copy_util", "Memory copy utilization", "DCGM_FI_DEV_MEM_COPY_UTIL", "%", None, False, "utilization"),
         ("temp", "GPU temperature", "DCGM_FI_DEV_GPU_TEMP", "°C", None, False, "thermal"),
         ("mem_temp", "Memory temperature", "DCGM_FI_DEV_MEMORY_TEMP", "°C", None, False, "thermal"),
         ("power", "Power draw", "DCGM_FI_DEV_POWER_USAGE", "W", None, False, "power"),
         ("sm_clock", "SM clock", "DCGM_FI_DEV_SM_CLOCK", "Hz", 1000000, False, "clock"),
         ("fb_used", "Framebuffer used", "DCGM_FI_DEV_FB_USED", "B", 1048576, False, "memory"),
         ("fb_free", "Framebuffer free", "DCGM_FI_DEV_FB_FREE", "B", 1048576, False, "memory"),
         ("xid", "Last XID error", "DCGM_FI_DEV_XID_ERRORS", "", None, False, "errors"),
         ("pcie_replay.rate", "PCIe replays per second", "DCGM_FI_DEV_PCIE_REPLAY_COUNTER", "!replays/s", None, True, "errors"),
         ("remapped_rows.uncorrectable", "Uncorrectable remapped rows", "DCGM_FI_DEV_UNCORRECTABLE_REMAPPED_ROWS", "", None, False, "errors"),
         ("row_remap_failure", "Row remap failure", "DCGM_FI_DEV_ROW_REMAP_FAILURE", "", None, False, "errors")]
    for key, name, metric, units, mult, ps, comp in P:
        pre = [{"type": "PROMETHEUS_PATTERN", "parameters": [metric + '{gpu="{#GPU}"}', "function", "max"]}]
        if mult:
            pre.append({"type": "MULTIPLIER", "parameters": [str(mult)]})
        if ps:
            pre.append({"type": "CHANGE_PER_SECOND", "parameters": [""]})
        disc["item_prototypes"].append({
            "uuid": uid(T, "proto", key), "name": "GPU {#GPU}: " + name, "type": "DEPENDENT",
            "key": f'dcgm.gpu.{key}["{{#GPU}}"]', "delay": "0", "value_type": "FLOAT", "units": units,
            "preprocessing": pre, "master_item": {"key": "dcgm.get_metrics"},
            "tags": [{"tag": "component", "value": comp}, {"tag": "gpu", "value": "{#GPU}"}]})
    disc["item_prototypes"].append({
        "uuid": uid(T, "proto", "fb_used.pct"), "name": "GPU {#GPU}: Framebuffer used, in %", "type": "CALCULATED",
        "key": 'dcgm.gpu.fb_used.pct["{#GPU}"]', "delay": "{$DCGM.INTERVAL}", "value_type": "FLOAT", "units": "%",
        "params": '100*last(//dcgm.gpu.fb_used["{#GPU}"])/(last(//dcgm.gpu.fb_used["{#GPU}"])+last(//dcgm.gpu.fb_free["{#GPU}"])+1)',
        "tags": [{"tag": "component", "value": "memory"}, {"tag": "gpu", "value": "{#GPU}"}]})
    k = lambda key: f'/{T}/dcgm.gpu.{key}["{{#GPU}}"]'
    fatal = " or ".join(f"last({k('xid')})={c}" for c in (48, 74, 79, 92, 94, 95))
    disc["trigger_prototypes"] += [
        {"uuid": uid(T, "t", "xid"), "expression": f"last({k('xid')})<>0",
         "name": "GPU {#GPU}: XID error {ITEM.LASTVALUE1} reported", "priority": "WARNING", "manual_close": "YES",
         "description": "Check NVIDIA's XID catalog. 13/31/43 are usually application faults.",
         "dependencies": [{"name": "GPU {#GPU}: fatal XID error {ITEM.LASTVALUE1} reported",
                           "expression": fatal}]},
        {"uuid": uid(T, "t", "xid-fatal"), "expression": fatal,
         "name": "GPU {#GPU}: fatal XID error {ITEM.LASTVALUE1} reported", "priority": "HIGH", "manual_close": "YES",
         "description": "48/92/94/95 = ECC, 74 = NVLink, 79 = GPU fell off the bus. Drain the node and reset or replace the GPU."},
        {"uuid": uid(T, "t", "temp"), "expression": f"min({k('temp')},10m)>{{$DCGM.GPU.TEMP.MAX.WARN}}",
         "name": "GPU {#GPU}: temperature is high", "priority": "WARNING",
         "description": "Sustained high temperature leads to thermal throttling."},
        {"uuid": uid(T, "t", "remap"), "expression": f"last({k('row_remap_failure')})>0",
         "name": "GPU {#GPU}: HBM row remapping failed", "priority": "HIGH",
         "description": "The GPU has no spare rows left. Drain the node and open an RMA."},
        {"uuid": uid(T, "t", "fb"), "expression": f'min(/{T}/dcgm.gpu.fb_used.pct["{{#GPU}}"],15m)>{{$DCGM.FB.USED.MAX.WARN}}',
         "name": "GPU {#GPU}: framebuffer is almost full", "priority": "INFO",
         "description": "Expected for vLLM/SGLang (they pre-allocate KV cache); a problem for other workloads."},
    ]
    t.lld.append(disc)
    return t


def readme(t, intro, setup):
    lines = [f"# {t.name}", "", intro, "", "## Requirements", "", "Zabbix 7.0 or newer. Zabbix server or proxy must reach the metrics URL.", "",
             "## Setup", ""] + setup + ["", "## Macros", "", "| Macro | Default | Description |", "|---|---|---|"]
    for m in t.macros:
        lines.append(f"| `{m['macro']}` | `{m['value']}` | {m['description']} |")
    lines += ["", "## Items", "", "| Name | Key | Units |", "|---|---|---|"]
    for i in t.items:
        lines.append(f"| {i['name']} | `{i['key']}` | {i.get('units', '').lstrip('!')} |")
    trig = [(tr["name"], tr["priority"]) for i in t.items for tr in i.get("triggers", [])]
    for d in t.lld:
        lines += ["", f"## Discovery: {d['name']}", "", d["description"], "", "| Item prototype | Key | Units |", "|---|---|---|"]
        for i in d["item_prototypes"]:
            lines.append(f"| {i['name']} | `{i['key']}` | {i.get('units', '').lstrip('!')} |")
        trig += [(tr["name"], tr["priority"]) for tr in d["trigger_prototypes"]]
    lines += ["", "## Triggers", "", "| Name | Severity |", "|---|---|"]
    lines += [f"| {n} | {p.capitalize()} |" for n, p in trig]
    lines += ["", "## Feedback", "", "Issues and pull requests: https://github.com/lbarrosby/ai-observability-pack", ""]
    return "\n".join(lines)


TEMPLATES = [
    ("template_llama_cpp_server_by_http", llamacpp,
     "Monitors [llama.cpp](https://github.com/ggml-org/llama.cpp) `llama-server` through its Prometheus endpoint: slots, deferred requests, token throughput, prompt cache reuse.",
     ["1. Start `llama-server` with `--metrics`.", "2. Import the template and link it to a host.",
      "3. Set `{$LLAMACPP.METRICS.URL}` (default `http://localhost:8080/metrics`). If the server uses `--api-key`, add an `Authorization: Bearer` header to the *Get metrics* item."]),
    ("template_vllm_by_http", vllm,
     "Monitors a [vLLM](https://github.com/vllm-project/vllm) OpenAI-compatible server (V1 engine) through its Prometheus endpoint: scheduler queue, KV cache, preemptions, token throughput and average latencies. Served models are discovered automatically.",
     ["1. vLLM exposes `/metrics` on the API port by default.", "2. Import the template and link it to a host.",
      "3. Set `{$VLLM.METRICS.URL}` (default `http://localhost:8000/metrics`).",
      "4. Latency items are averages over the polling interval (histogram sum rate divided by count rate). Use Prometheus/Grafana for percentiles."]),
    ("template_nvidia_dcgm_exporter_by_http", dcgm,
     "Monitors NVIDIA GPUs through [dcgm-exporter](https://github.com/NVIDIA/dcgm-exporter) default counters: utilization, memory, temperature, power, clocks, XID errors and HBM row remapping. GPUs are discovered automatically.",
     ["1. Run dcgm-exporter (GPU Operator or standalone, port 9400).", "2. Import the template and link it to a host per GPU node.",
      "3. Set `{$DCGM.METRICS.URL}` (default `http://localhost:9400/metrics`)."]),
]

if __name__ == "__main__":
    for folder, fn, intro, setup in TEMPLATES:
        t = fn()
        out_dir = os.path.join(OUT, folder, "7.0")
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, folder + ".yaml"), "w") as f:
            f.write("\n".join(dump(t.to_export())) + "\n")
        with open(os.path.join(out_dir, "README.md"), "w") as f:
            f.write(readme(t, intro, setup))
        print("wrote", folder)
