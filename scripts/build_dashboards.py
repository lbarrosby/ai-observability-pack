#!/usr/bin/env python3
"""Generate every dashboard in ../dashboards from the definitions below.

Run: python3 scripts/build_dashboards.py
"""
import json
import os

from lib import Dashboard, link_to, pct, q, query_var

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dashboards")
R = "[$__rate_interval]"
TOKS = "suffix: tok/s"
P3 = ("0.5", "0.95", "0.99")
LINK = [link_to("ai-observability")]


def m(metric, sel, extra=""):
    inner = ", ".join(x for x in (sel, extra) if x)
    return metric + "{" + inner + "}"


def rate(metric, sel, extra=""):
    return "rate(" + m(metric, sel, extra) + R + ")"


def ns_pod_vars(metric):
    return [
        query_var("namespace", "Namespace", f"label_values({metric}, namespace)"),
        query_var("pod", "Pod", f'label_values({metric}{{namespace=~"$namespace"}}, pod)'),
    ]


# ---------------------------------------------------------------- vLLM engine
def vllm():
    S = 'namespace=~"$namespace", pod=~"$pod", model_name=~"$model_name"'
    v = ns_pod_vars("vllm:num_requests_running") + [
        query_var("model_name", "Model",
                  'label_values(vllm:num_requests_running{namespace=~"$namespace", pod=~"$pod"}, model_name)',
                  all_value=".+")]
    KV = f"({m('vllm:kv_cache_usage_perc', S)} or {m('vllm:gpu_cache_usage_perc', S)})"
    d = Dashboard("ai-obs-vllm", "vLLM",
                  "vLLM inference engine (V1 engine metrics, /metrics on the OpenAI-compatible server): "
                  "latency (TTFT, ITL, E2E), token throughput, scheduler queue, KV cache and prefix cache.",
                  ["vllm", "llm", "inference"], v, LINK)
    d.row("Overview")
    d.stat("Running requests", f"sum({m('vllm:num_requests_running', S)})", "short",
           "Requests currently being processed by the engine (in the running batch).")
    d.stat("Waiting requests", f"sum({m('vllm:num_requests_waiting', S)})", "short",
           "Requests queued by the scheduler and not yet admitted. A value that keeps growing means the "
           "engine is saturated (KV cache or max_num_seqs).", thresholds=[(5, "yellow"), (20, "red")])
    d.stat("KV cache usage (max)", f"max({KV})", "percentunit",
           "Highest KV cache usage across the selected engines. 1.0 means full: new requests wait and "
           "running ones may be preempted.", thresholds=[(0.8, "yellow"), (0.95, "red")], min_=0, max_=1)
    d.stat("Generation throughput", f"sum({rate('vllm:generation_tokens_total', S)})", TOKS,
           "Output tokens generated per second across the selection.")
    d.stat("Finished requests", f"sum({rate('vllm:request_success_total', S)})", "reqps",
           "Requests completed per second (any finish reason: stop, length, abort).")
    d.stat("TTFT p95", q("0.95", "vllm:time_to_first_token_seconds", "le", S), "s",
           "95th percentile time to first token over the selection.", thresholds=[(1, "yellow"), (3, "red")])

    d.row("Latency")
    d.ts("Time to first token (TTFT)", pct(P3, "vllm:time_to_first_token_seconds", S), "s",
         "Time from request arrival to the first output token. Dominated by queueing and prefill.")
    d.ts("Inter-token latency (ITL)", pct(P3, "vllm:inter_token_latency_seconds", S), "s",
         "Time between consecutive output tokens of a request (decode speed as seen by a streaming client).")
    d.ts("End-to-end request latency", pct(P3, "vllm:e2e_request_latency_seconds", S), "s",
         "Total request time inside vLLM, from arrival to the last token.")
    d.ts("Where the time goes (p95)", [
        (q("0.95", "vllm:request_queue_time_seconds", "le", S), "queue"),
        (q("0.95", "vllm:request_prefill_time_seconds", "le", S), "prefill"),
        (q("0.95", "vllm:request_decode_time_seconds", "le", S), "decode")], "s",
         "p95 of each request phase. A high queue time with low prefill/decode means the engine is "
         "saturated, not slow.")

    d.row("Throughput and tokens")
    d.ts("Token throughput by model", [
        (f"sum by (model_name) ({rate('vllm:prompt_tokens_total', S)})", "prompt {{model_name}}"),
        (f"sum by (model_name) ({rate('vllm:generation_tokens_total', S)})", "generation {{model_name}}")],
         TOKS, "Prompt (prefill) and generation (decode) tokens processed per second.")
    d.ts("Finished requests by reason",
         f"sum by (finished_reason) ({rate('vllm:request_success_total', S)})", "reqps",
         "stop = model ended naturally, length = hit max_tokens, abort = client disconnect or cancellation. "
         "A rising abort share usually means clients are timing out.", stack=True)
    d.ts("Prompt length", pct(("0.5", "0.95"), "vllm:request_prompt_tokens", S), "short",
         "Distribution of prompt sizes in tokens. Long prompts drive prefill time and KV usage.")
    d.ts("Generation length", pct(("0.5", "0.95"), "vllm:request_generation_tokens", S), "short",
         "Distribution of generated tokens per request.")

    d.row("Scheduler and cache")
    d.ts("Running vs waiting by pod", [
        (f"sum by (pod) ({m('vllm:num_requests_running', S)})", "running {{pod}}"),
        (f"sum by (pod) ({m('vllm:num_requests_waiting', S)})", "waiting {{pod}}")], "short",
         "Batch size and scheduler backlog per engine pod.")
    d.ts("KV cache usage by pod", f"max by (pod) ({KV})", "percentunit",
         "Fraction of KV cache blocks in use (vllm:kv_cache_usage_perc, or vllm:gpu_cache_usage_perc on older "
         "releases). Sustained values near 1.0 cause preemptions.", min_=0, max_=1,
         thresholds=[(0.8, "yellow"), (0.95, "red")], show_thresholds=True)
    d.ts("Prefix cache hit rate",
         f"sum by (model_name) ({rate('vllm:prefix_cache_hits_total', S)} or {rate('vllm:gpu_prefix_cache_hits_total', S)}) / "
         f"sum by (model_name) ({rate('vllm:prefix_cache_queries_total', S)} or {rate('vllm:gpu_prefix_cache_queries_total', S)})",
         "percentunit",
         "Share of prompt tokens served from the prefix cache (automatic prefix caching). Falls back to the "
         "older vllm:gpu_prefix_cache_* names. Higher is cheaper.",
         min_=0, max_=1)
    d.ts("Preemptions", f"sum by (pod) ({rate('vllm:num_preemptions_total', S)})", "ops",
         "Requests preempted per second because the KV cache ran out. Any sustained value is a capacity "
         "problem: lower max_num_seqs, raise gpu_memory_utilization or add replicas.")
    return d


# --------------------------------------------------- vLLM production-stack router
def vllm_router():
    S = 'namespace=~"$namespace", pod=~"$pod", server=~"$server"'
    SM = 'namespace=~"$namespace", pod=~"$pod", model=~"$model"'
    v = ns_pod_vars("vllm:current_qps") + [
        query_var("server", "Backend", 'label_values(vllm:current_qps{namespace=~"$namespace", pod=~"$pod"}, server)',
                  all_value=".+"),
        query_var("model", "Model",
                  'label_values(vllm:num_incoming_requests_total{namespace=~"$namespace", pod=~"$pod"}, model)',
                  all_value=".+")]
    d = Dashboard("ai-obs-vllm-router", "vLLM production-stack router",
                  "vLLM production-stack router (vllm_router): traffic, routing, per-backend load and "
                  "router-observed latency. The router reuses the vllm: prefix, so every query filters on the "
                  "router-only server or model label.", ["vllm", "llm", "router"], v, LINK)
    d.row("Overview")
    d.stat("Healthy backends", f"sum({m('vllm:healthy_pods_total', S)})", "short",
           "Number of vLLM engine pods the router currently considers healthy.",
           thresholds=None, color_mode="value")
    d.stat("Incoming requests", f"sum({rate('vllm:num_incoming_requests_total', SM)})", "reqps",
           "Valid requests received by the router per second (also counts requests with no backend available).")
    d.stat("Error ratio",
           f"sum({rate('vllm:request_errors_total', SM)}) / sum({rate('vllm:num_incoming_requests_total', SM)})",
           "percentunit", "Router request errors divided by incoming requests.",
           thresholds=[(0.01, "yellow"), (0.05, "red")])
    d.stat("Router latency p95",
           q("0.95", "vllm:request_latency_seconds", "le", SM + ', status="success"'), "s",
           "95th percentile end-to-end latency observed at the router for successful requests.")
    d.stat("Router CPU", f"max({m('router_cpu_usage_percent', 'namespace=~\"$namespace\", pod=~\"$pod\"')})",
           "percent", "Router process CPU usage.", thresholds=[(70, "yellow"), (90, "red")])
    d.stat("Router memory", f"max({m('router_memory_usage_percent', 'namespace=~\"$namespace\", pod=~\"$pod\"')})",
           "percent", "Router host memory usage.", thresholds=[(80, "yellow"), (90, "red")])

    d.row("Traffic and errors")
    d.ts("Incoming requests by model", f"sum by (model) ({rate('vllm:num_incoming_requests_total', SM)})",
         "reqps", "Requests per second per requested model name.")
    d.ts("QPS per backend", f"sum by (server) ({m('vllm:current_qps', S)})", "reqps",
         "Current QPS routed to each backend as tracked by the router. Uneven lines mean unbalanced routing.")
    d.ts("Errors by type and backend",
         f"sum by (error_type, server) ({rate('vllm:request_errors_total', SM)})", "reqps",
         "Request errors seen by the router, by exception type (e.g. ClientConnectorError = backend unreachable).")
    d.ts("Token throughput per backend", [
        (f"sum by (server) ({rate('vllm:input_tokens_total', SM)})", "input {{server}}"),
        (f"sum by (server) ({rate('vllm:output_tokens_total', SM)})", "output {{server}}")], TOKS,
         "Input and output tokens per second proxied to each backend.")

    d.row("Latency")
    d.ts("Router latency by model", pct(P3, "vllm:request_latency_seconds", SM + ', status="success"', by="model",
                                        extra_legend="{{model}}"), "s",
         "End-to-end latency measured at the router (includes network to the backend).")
    d.ts("Average latency per backend", f"max by (server) ({m('vllm:avg_latency', S)}) >= 0", "s",
         "Sliding-window average end-to-end latency per backend, as computed by the router (-1 = no data, hidden).")
    d.ts("Average inter-token latency per backend", f"max by (server) ({m('vllm:avg_itl', S)}) >= 0", "s",
         "Sliding-window average ITL per backend (-1 = no data, hidden).")
    d.ts("Average decoding length per backend", f"max by (server) ({m('vllm:avg_decoding_length', S)}) >= 0",
         "short", "Average number of decoded tokens per request per backend (-1 = no data, hidden).")

    d.row("Backend load")
    d.ts("Running vs waiting per backend", [
        (f"sum by (server) ({m('vllm:num_requests_running', S)})", "running {{server}}"),
        (f"sum by (server) ({m('vllm:num_requests_waiting', S)})", "waiting {{server}}")], "short",
         "Engine queue state as scraped by the router from each backend.")
    d.ts("Prefill vs decoding per backend", [
        (f"sum by (server) ({m('vllm:num_prefill_requests', S)})", "prefill {{server}}"),
        (f"sum by (server) ({m('vllm:num_decoding_requests', S)})", "decoding {{server}}")], "short",
         "Requests in prefill and decode phase per backend, as tracked by the router.")
    d.ts("Prefix cache hit rate per backend", f"max by (server) ({m('vllm:gpu_prefix_cache_hit_rate', S)})",
         "percentunit", "GPU prefix cache hit rate reported per backend. Useful to validate session/prefix-aware routing.",
         min_=0, max_=1)
    d.ts("Router resources", [
        (f"max({m('router_cpu_usage_percent', 'namespace=~\"$namespace\", pod=~\"$pod\"')})", "cpu"),
        (f"max({m('router_memory_usage_percent', 'namespace=~\"$namespace\", pod=~\"$pod\"')})", "memory"),
        (f"max({m('router_disk_usage_percent', 'namespace=~\"$namespace\", pod=~\"$pod\"')})", "disk")],
         "percent", "CPU, memory and disk usage reported by the router process.", min_=0, max_=100)
    return d


# --------------------------------------------------------------------- SGLang
def sglang():
    S = 'namespace=~"$namespace", pod=~"$pod", model_name=~"$model_name"'
    v = ns_pod_vars("sglang:num_running_reqs") + [
        query_var("model_name", "Model",
                  'label_values(sglang:num_running_reqs{namespace=~"$namespace", pod=~"$pod"}, model_name)')]
    d = Dashboard("ai-obs-sglang", "SGLang",
                  "SGLang runtime (launch with --enable-metrics): latency, throughput, queue, KV token pool "
                  "and radix cache.", ["sglang", "llm", "inference"], v, LINK)
    d.row("Overview")
    d.stat("Running requests", f"sum({m('sglang:num_running_reqs', S)})", "short", "Requests in the running batch.")
    d.stat("Queued requests", f"sum({m('sglang:num_queue_reqs', S)})", "short",
           "Requests waiting in the scheduler queue.", thresholds=[(5, "yellow"), (20, "red")])
    d.stat("Token pool usage (max)", f"max({m('sglang:token_usage', S)})", "percentunit",
           "Fraction of the KV token pool in use.", thresholds=[(0.85, "yellow"), (0.95, "red")], min_=0, max_=1)
    d.stat("Generation throughput", f"sum({m('sglang:gen_throughput', S)})", TOKS,
           "Decode throughput reported by the scheduler.")
    d.stat("Radix cache hit rate", f"avg({m('sglang:cache_hit_rate', S)})", "percentunit",
           "Prefix (radix tree) cache hit rate.", min_=0, max_=1)
    d.stat("TTFT p95", q("0.95", "sglang:time_to_first_token_seconds", "le", S), "s",
           "95th percentile time to first token.", thresholds=[(1, "yellow"), (3, "red")])

    d.row("Latency")
    d.ts("Time to first token", pct(P3, "sglang:time_to_first_token_seconds", S), "s",
         "Time from request arrival to first output token.")
    d.ts("Inter-token latency", pct(P3, "sglang:inter_token_latency_seconds", S), "s",
         "Time between consecutive output tokens.")
    d.ts("End-to-end request latency", pct(P3, "sglang:e2e_request_latency_seconds", S), "s",
         "Total request latency (streaming and non-streaming).")
    d.ts("Queue time", pct(("0.5", "0.95"), "sglang:queue_time_seconds", S), "s",
         "Time requests spend waiting before prefill starts.")

    d.row("Throughput")
    d.ts("Token throughput", [
        (f"sum by (model_name) ({rate('sglang:prompt_tokens_total', S)})", "prompt {{model_name}}"),
        (f"sum by (model_name) ({rate('sglang:generation_tokens_total', S)})", "generation {{model_name}}"),
        (f"sum by (model_name) ({rate('sglang:cached_tokens_total', S)})", "cached prompt {{model_name}}")],
         TOKS, "Prompt, generated and cache-served prompt tokens per second.")
    d.ts("Requests", [
        (f"sum by (model_name) ({rate('sglang:num_requests_total', S)})", "finished {{model_name}}"),
        (f"sum by (model_name) ({rate('sglang:num_aborted_requests_total', S)})", "aborted {{model_name}}")],
         "reqps", "Finished and aborted requests per second.")

    d.row("Capacity")
    d.ts("Running vs queued by pod", [
        (f"sum by (pod) ({m('sglang:num_running_reqs', S)})", "running {{pod}}"),
        (f"sum by (pod) ({m('sglang:num_queue_reqs', S)})", "queued {{pod}}")], "short",
         "Batch size and backlog per server pod.")
    d.ts("KV token pool usage by pod", f"max by (pod) ({m('sglang:token_usage', S)})", "percentunit",
         "Fraction of the KV token pool in use per pod.", min_=0, max_=1,
         thresholds=[(0.85, "yellow"), (0.95, "red")], show_thresholds=True)
    d.ts("Used tokens by pod", f"sum by (pod) ({m('sglang:num_used_tokens', S)})", "short",
         "Number of KV tokens currently allocated.")
    d.ts("Radix cache hit rate by pod", f"avg by (pod) ({m('sglang:cache_hit_rate', S)})", "percentunit",
         "Prefix cache hit rate per pod.", min_=0, max_=1)
    return d


# ------------------------------------------------------------------ llama.cpp
def llamacpp():
    S = 'namespace=~"$namespace", pod=~"$pod"'
    v = ns_pod_vars("llamacpp:requests_processing")
    d = Dashboard("ai-obs-llama-cpp", "llama.cpp server",
                  "llama.cpp llama-server (start with --metrics): token throughput, decode speed, slots, "
                  "prompt cache and speculative decoding.", ["llama-cpp", "llm", "inference"], v, LINK)
    d.row("Overview")
    d.stat("Requests processing", f"sum({m('llamacpp:requests_processing', S)})", "short",
           "Requests currently using a slot.")
    d.stat("Requests deferred", f"sum({m('llamacpp:requests_deferred', S)})", "short",
           "Requests waiting for a free slot. Persistent values mean you need more --parallel slots or replicas.",
           thresholds=[(1, "yellow"), (5, "red")])
    d.stat("Prompt tokens/s", f"sum({rate('llamacpp:prompt_tokens_total', S)})", TOKS,
           "Prompt tokens evaluated per second (excludes cache hits).")
    d.stat("Generated tokens/s", f"sum({rate('llamacpp:tokens_predicted_total', S)})", TOKS,
           "Tokens generated per second.")
    d.stat("Prompt cache reuse",
           f"sum({rate('llamacpp:prompt_tokens_cached_total', S)}) / (sum({rate('llamacpp:prompt_tokens_cached_total', S)}) + sum({rate('llamacpp:prompt_tokens_total', S)}))",
           "percentunit", "Share of prompt tokens reused from the KV cache instead of being recomputed.", min_=0, max_=1)
    d.stat("Max sequence length", f"max({m('llamacpp:n_tokens_max', S)})", "short",
           "Largest sequence (prompt + generation) observed since start. Compare with --ctx-size.", graph=False)

    d.row("Throughput and speed")
    d.ts("Tokens per second", [
        (f"sum by (pod) ({rate('llamacpp:prompt_tokens_total', S)})", "prompt {{pod}}"),
        (f"sum by (pod) ({rate('llamacpp:tokens_predicted_total', S)})", "generated {{pod}}")], TOKS,
         "Wall-clock token throughput per pod.")
    d.ts("Processing speed while busy", [
        (f"sum by (pod) ({rate('llamacpp:prompt_tokens_total', S)}) / sum by (pod) ({rate('llamacpp:prompt_seconds_total', S)})",
         "prompt eval {{pod}}"),
        (f"sum by (pod) ({rate('llamacpp:tokens_predicted_total', S)}) / sum by (pod) ({rate('llamacpp:tokens_predicted_seconds_total', S)})",
         "generation {{pod}}")], TOKS,
         "Tokens per second of compute time (counters ratio). Measures model/hardware speed independent of load.")
    d.ts("Server-reported average speed", [
        (f"avg by (pod) ({m('llamacpp:prompt_tokens_seconds', S)})", "prompt {{pod}}"),
        (f"avg by (pod) ({m('llamacpp:predicted_tokens_seconds', S)})", "generation {{pod}}")], TOKS,
         "Average throughput gauges computed by llama-server itself.")
    d.ts("llama_decode calls", f"sum by (pod) ({rate('llamacpp:n_decode_total', S)})", "ops",
         "llama_decode() calls per second (excludes speculative and multimodal decoding).")

    d.row("Slots, cache and speculative decoding")
    d.ts("Requests processing vs deferred", [
        (f"sum by (pod) ({m('llamacpp:requests_processing', S)})", "processing {{pod}}"),
        (f"sum by (pod) ({m('llamacpp:requests_deferred', S)})", "deferred {{pod}}")], "short",
         "Slot occupancy and backlog.")
    d.ts("Busy slots per decode", f"avg by (pod) ({m('llamacpp:n_busy_slots_per_decode', S)})", "short",
         "Average number of slots batched in each llama_decode() call. Close to --parallel means good batching.")
    d.ts("Prompt cache reuse", [
        (f"sum by (pod) ({rate('llamacpp:prompt_tokens_cached_total', S)}) / (sum by (pod) ({rate('llamacpp:prompt_tokens_cached_total', S)}) + sum by (pod) ({rate('llamacpp:prompt_tokens_total', S)}))",
         "{{pod}}")], "percentunit", "Share of prompt tokens reused from cache per pod.", min_=0, max_=1)
    d.ts("Speculative decoding acceptance", [
        (f"sum by (pod) ({rate('llamacpp:spec_decode_num_accepted_tokens_total', S)}) / sum by (pod) ({rate('llamacpp:spec_decode_num_draft_tokens_total', S)})",
         "acceptance {{pod}}")], "percentunit",
         "Accepted draft tokens divided by drafted tokens. Empty when no draft model is configured.", min_=0, max_=1)
    return d


# ------------------------------------------------------------------------ TEI
def tei():
    S = 'namespace=~"$namespace", pod=~"$pod"'
    v = ns_pod_vars("te_queue_size")
    d = Dashboard("ai-obs-hf-tei", "Hugging Face Text Embeddings Inference",
                  "Hugging Face TEI router metrics: request rate, failures, latency breakdown "
                  "(tokenization, queue, inference), batching and input length.", ["tei", "embeddings", "inference"],
                  v, LINK)
    d.row("Overview")
    d.stat("Requests/s", f"sum({rate('te_request_count', S)})", "reqps", "Requests received per second.")
    d.stat("Success ratio", f"sum({rate('te_request_success', S)}) / sum({rate('te_request_count', S)})",
           "percentunit", "Successful requests divided by received requests.",
           thresholds=[(0.95, "yellow"), (0.99, "green")], min_=0, max_=1)
    d.stat("Failures/s", f"sum({rate('te_request_failure', S)}) or vector(0)", "reqps", "Failed requests per second.",
           thresholds=[(0.01, "red")])
    d.stat("Queue size", f"sum({m('te_queue_size', S)})", "short", "Requests waiting in the batching queue.",
           thresholds=[(50, "yellow"), (200, "red")])
    d.stat("Request p95", q("0.95", "te_request_duration", "le", S), "s", "95th percentile request duration.")
    d.stat("Input length p95", q("0.95", "te_request_input_length", "le", S), "short",
           "95th percentile input length in tokens.")

    d.row("Requests")
    d.ts("Requests by method", f"sum by (method) ({rate('te_request_count', S)})", "reqps",
         "Requests per second by method label (e.g. single vs batch).")
    d.ts("Failures by error", f"sum by (err) ({rate('te_request_failure', S)})", "reqps",
         "Failures by error kind (tokenization, validation, inference, overloaded...).")
    d.ts("Requests by route", [
        (f"sum({rate('te_embed_count', S)})", "embed"),
        (f"sum({rate('te_predict_count', S)})", "predict / rerank")], "reqps",
         "Embed vs predict (classification / reranking) endpoint traffic.")
    d.ts("Request latency", pct(P3, "te_request_duration", S), "s", "End-to-end request latency.")

    d.row("Latency breakdown and batching")
    d.ts("Latency breakdown (p95)", [
        (q("0.95", "te_request_tokenization_duration", "le", S), "tokenization"),
        (q("0.95", "te_request_queue_duration", "le", S), "queue"),
        (q("0.95", "te_request_inference_duration", "le", S), "inference")], "s",
         "p95 of each phase. High queue time means the model is saturated: raise max-batch-tokens or add replicas.")
    d.ts("Input length", pct(("0.5", "0.95"), "te_request_input_length", S), "short",
         "Request input length in tokens.")
    d.ts("Next batch size", pct(("0.5", "0.95"), "te_batch_next_size", S), "short",
         "Number of requests in each batch sent to the model.")
    d.ts("Next batch tokens", pct(("0.5", "0.95"), "te_batch_next_tokens", S), "short",
         "Number of tokens in each batch sent to the model.")
    return d


# ---------------------------------------------------------------------- Triton
def triton():
    S = 'namespace=~"$namespace", pod=~"$pod", model=~"$model"'
    SN = 'namespace=~"$namespace", pod=~"$pod"'
    v = ns_pod_vars("nv_inference_request_success") + [
        query_var("model", "Model",
                  'label_values(nv_inference_request_success{namespace=~"$namespace", pod=~"$pod"}, model)')]
    d = Dashboard("ai-obs-triton", "NVIDIA Triton Inference Server",
                  "Triton Inference Server: request rate, failures by reason, latency breakdown, dynamic "
                  "batching and GPU/CPU usage reported by Triton (port 8002 /metrics).",
                  ["triton", "inference", "nvidia"], v, LINK)
    reqs = f"(sum by (model) ({rate('nv_inference_request_success', S)}) + sum by (model) ({rate('nv_inference_request_failure', S)}))"
    d.row("Overview")
    d.stat("Successful requests/s", f"sum({rate('nv_inference_request_success', S)})", "reqps",
           "Successful inference requests per second (a batched request counts once).")
    d.stat("Failure ratio",
           f"sum({rate('nv_inference_request_failure', S)}) / (sum({rate('nv_inference_request_success', S)}) + sum({rate('nv_inference_request_failure', S)}))",
           "percentunit", "Failed requests over all requests.", thresholds=[(0.01, "yellow"), (0.05, "red")])
    d.stat("Inferences/s", f"sum({rate('nv_inference_count', S)})", "ops",
           "Inferences per second (a batch of n counts n).")
    d.stat("Avg batch size", f"sum({rate('nv_inference_count', S)}) / sum({rate('nv_inference_exec_count', S)})",
           "short", "Inferences per execution. Near 1 means dynamic batching is not kicking in.", decimals=1)
    d.stat("Pending requests", f"sum({m('nv_inference_pending_request_count', S)})", "short",
           "Requests queued waiting for a backend instance.", thresholds=[(10, "yellow"), (50, "red")])
    d.stat("Avg request latency",
           f"sum({rate('nv_inference_request_duration_us', S)}) / (sum({rate('nv_inference_request_success', S)}) + sum({rate('nv_inference_request_failure', S)}))",
           "µs", "Average end-to-end request latency inside Triton (cumulative counters).")

    d.row("Throughput and errors")
    d.ts("Requests by model", f"sum by (model) ({rate('nv_inference_request_success', S)})", "reqps",
         "Successful requests per second per model.")
    d.ts("Failures by reason", f"sum by (model, reason) ({rate('nv_inference_request_failure', S)})", "reqps",
         "REJECTED = scheduler queue timeout, CANCELED = cancelled, BACKEND = model/backend error, OTHER = core error.")
    d.ts("Average batch size by model",
         f"sum by (model) ({rate('nv_inference_count', S)}) / sum by (model) ({rate('nv_inference_exec_count', S)})",
         "short", "Inferences per batch execution.")
    d.ts("Pending requests by model", f"sum by (model) ({m('nv_inference_pending_request_count', S)})", "short",
         "Queue depth per model.")

    d.row("Latency")
    d.ts("Average latency breakdown", [
        (f"sum by (model) ({rate('nv_inference_queue_duration_us', S)}) / {reqs}", "queue {{model}}"),
        (f"sum by (model) ({rate('nv_inference_compute_input_duration_us', S)}) / {reqs}", "input {{model}}"),
        (f"sum by (model) ({rate('nv_inference_compute_infer_duration_us', S)}) / {reqs}", "infer {{model}}"),
        (f"sum by (model) ({rate('nv_inference_compute_output_duration_us', S)}) / {reqs}", "output {{model}}")],
         "µs", "Average time per request spent in queue, input copy, inference and output copy.", stack=True)
    d.ts("Request latency percentiles", pct(("0.5", "0.95", "0.99"), "nv_inference_request_histogram_us", S,
                                            by="model", extra_legend="{{model}}"), "µs",
         "Needs --metrics-config histogram_latencies=true. Empty otherwise: use the average breakdown.")
    d.ts("Queue latency p95", pct(("0.95",), "nv_inference_queue_histogram_us", S, by="model",
                                  extra_legend="{{model}}"), "µs",
         "Needs --metrics-config histogram_latencies=true.")
    d.ts("Time to first response p95 (decoupled)", pct(("0.95",), "nv_inference_first_response_histogram_ms", S,
                                                         by="model", extra_legend="{{model}}"), "ms",
         "Time to first response for decoupled models (LLM streaming, e.g. TensorRT-LLM / vLLM backend).")

    d.row("Resources reported by Triton")
    d.ts("GPU utilization", f"avg by (gpu_uuid) ({m('nv_gpu_utilization', SN)})", "percentunit",
         "GPU utilization (0-1) per GPU. For deeper GPU telemetry use the DCGM dashboard.", min_=0, max_=1)
    d.ts("GPU memory used", [
        (f"sum by (gpu_uuid) ({m('nv_gpu_memory_used_bytes', SN)})", "used {{gpu_uuid}}"),
        (f"sum by (gpu_uuid) ({m('nv_gpu_memory_total_bytes', SN)})", "total {{gpu_uuid}}")], "bytes",
         "GPU memory used vs total.")
    d.ts("GPU power", [
        (f"sum by (gpu_uuid) ({m('nv_gpu_power_usage', SN)})", "usage {{gpu_uuid}}"),
        (f"sum by (gpu_uuid) ({m('nv_gpu_power_limit', SN)})", "limit {{gpu_uuid}}")], "watt",
         "Power draw vs power limit.")
    d.ts("CPU", [
        (f"avg by (pod) ({m('nv_cpu_utilization', SN)})", "utilization {{pod}}"),
        (f"sum by (pod) ({m('nv_cpu_memory_used_bytes', SN)}) / sum by (pod) ({m('nv_cpu_memory_total_bytes', SN)})",
         "memory {{pod}}")], "percentunit", "Host CPU and memory usage seen by Triton.", min_=0, max_=1)
    return d


# ------------------------------------------------------------------------ DCGM
def dcgm():
    S = 'Hostname=~"$Hostname", gpu=~"$gpu", namespace=~"$namespace", pod=~"$pod"'
    v = [query_var("Hostname", "Node", "label_values(DCGM_FI_DEV_GPU_UTIL, Hostname)"),
         query_var("gpu", "GPU", 'label_values(DCGM_FI_DEV_GPU_UTIL{Hostname=~"$Hostname"}, gpu)'),
         query_var("namespace", "Workload namespace",
                   'label_values(DCGM_FI_DEV_GPU_UTIL{Hostname=~"$Hostname"}, namespace)'),
         query_var("pod", "Workload pod",
                   'label_values(DCGM_FI_DEV_GPU_UTIL{Hostname=~"$Hostname", namespace=~"$namespace"}, pod)')]
    L = "{{Hostname}} gpu{{gpu}}"
    by = "Hostname, gpu"
    d = Dashboard("ai-obs-dcgm", "NVIDIA GPU (DCGM exporter)",
                  "NVIDIA dcgm-exporter: utilization, memory, thermals, power, throttling, XID and ECC "
                  "health, plus per-workload GPU usage via the Kubernetes pod mapping.",
                  ["gpu", "nvidia", "dcgm"], v, LINK)
    d.row("Overview")
    d.stat("GPUs", f"count({m('DCGM_FI_DEV_GPU_UTIL', S)})", "short", "GPUs matching the filters.", graph=False)
    d.stat("Avg GPU utilization", f"avg({m('DCGM_FI_DEV_GPU_UTIL', S)})", "percent",
           "Average DCGM_FI_DEV_GPU_UTIL across the selection.", min_=0, max_=100)
    d.stat("Framebuffer used",
           f"sum({m('DCGM_FI_DEV_FB_USED', S)}) / (sum({m('DCGM_FI_DEV_FB_USED', S)}) + sum({m('DCGM_FI_DEV_FB_FREE', S)}))",
           "percentunit", "GPU memory in use across the selection.", thresholds=[(0.85, "yellow"), (0.95, "red")],
           min_=0, max_=1)
    d.stat("Power draw", f"sum({m('DCGM_FI_DEV_POWER_USAGE', S)})", "watt", "Total power draw.")
    d.stat("Max GPU temperature", f"max({m('DCGM_FI_DEV_GPU_TEMP', S)})", "celsius", "Hottest GPU.",
           thresholds=[(80, "yellow"), (87, "red")])
    d.stat("GPUs with XID error", f"count({m('DCGM_FI_DEV_XID_ERRORS', S)} != 0) or vector(0)", "short",
           "GPUs whose last XID error is non-zero. See the Errors row and NVIDIA's XID catalog.",
           thresholds=[(1, "red")], graph=False)

    d.row("Utilization")
    d.ts("GPU utilization", f"avg by ({by}) ({m('DCGM_FI_DEV_GPU_UTIL', S)})", "percent",
         "Fraction of time a kernel was running (coarse; 100% does not mean the SMs are saturated).",
         min_=0, max_=100, legend_calcs=("mean", "max"), legend=L)
    d.ts("Graphics engine and tensor activity", [
        (f"avg by ({by}) ({m('DCGM_FI_PROF_GR_ENGINE_ACTIVE', S)})", "gr engine " + L),
        (f"avg by ({by}) ({m('DCGM_FI_PROF_PIPE_TENSOR_ACTIVE', S)})", "tensor " + L)], "percentunit",
         "DCP profiling metrics (datacenter GPUs only). Tensor pipe activity is the best signal of real "
         "matmul load for LLM inference.", min_=0, max_=1, legend_calcs=("mean", "max"))
    d.ts("Memory bandwidth activity", [
        (f"avg by ({by}) ({m('DCGM_FI_PROF_DRAM_ACTIVE', S)})", "dram " + L),
        (f"avg by ({by}) ({m('DCGM_FI_DEV_MEM_COPY_UTIL', S)}) / 100", "mem copy " + L)], "percentunit",
         "Device memory interface activity. LLM decode is usually memory-bandwidth bound.", min_=0, max_=1,
         legend_calcs=("mean", "max"))
    d.ts("PCIe and NVLink throughput", [
        (f"sum by ({by}) ({m('DCGM_FI_PROF_PCIE_TX_BYTES', S)})", "pcie tx " + L),
        (f"sum by ({by}) ({m('DCGM_FI_PROF_PCIE_RX_BYTES', S)})", "pcie rx " + L),
        (f"sum by ({by}) ({m('DCGM_FI_DEV_NVLINK_BANDWIDTH_TOTAL', S)}) * 1e6", "nvlink " + L)], "Bps",
         "PCIe (DCP metric, bytes/s) and NVLink aggregate bandwidth (reported in MB/s, converted to bytes/s).",
         legend_calcs=("mean", "max"))

    d.row("Memory")
    d.ts("Framebuffer used", f"sum by ({by}) ({m('DCGM_FI_DEV_FB_USED', S)}) * 1048576", "bytes",
         "GPU memory used per GPU (reported in MiB). vLLM/SGLang pre-allocate most memory for KV cache, "
         "so high usage is expected.", legend_calcs=("lastNotNull", "max"), legend=L)
    d.ts("Framebuffer used ratio",
         f"sum by ({by}) ({m('DCGM_FI_DEV_FB_USED', S)}) / (sum by ({by}) ({m('DCGM_FI_DEV_FB_USED', S)}) + sum by ({by}) ({m('DCGM_FI_DEV_FB_FREE', S)}))",
         "percentunit", "Used / (used + free) per GPU.", min_=0, max_=1, legend_calcs=("lastNotNull", "max"), legend=L)

    d.row("Thermals, power and throttling")
    d.ts("Temperature", [
        (f"max by ({by}) ({m('DCGM_FI_DEV_GPU_TEMP', S)})", "gpu " + L),
        (f"max by ({by}) ({m('DCGM_FI_DEV_MEMORY_TEMP', S)})", "memory " + L)], "celsius",
         "GPU core and HBM temperature.", thresholds=[(80, "yellow"), (87, "red")], show_thresholds=True,
         legend_calcs=("mean", "max"))
    d.ts("Power draw", f"sum by ({by}) ({m('DCGM_FI_DEV_POWER_USAGE', S)})", "watt",
         "Power draw per GPU.", legend_calcs=("mean", "max"), legend=L)
    d.ts("SM clock", f"max by ({by}) ({m('DCGM_FI_DEV_SM_CLOCK', S)}) * 1e6", "hertz",
         "SM clock. A drop under load with high temperature or power means throttling.",
         legend_calcs=("mean", "min"), legend=L)
    d.ts("Time throttled", [
        (f"sum by ({by}) ({rate('DCGM_FI_DEV_POWER_VIOLATION', S)}) / 1e9", "power " + L),
        (f"sum by ({by}) ({rate('DCGM_FI_DEV_THERMAL_VIOLATION', S)}) / 1e9", "thermal " + L)], "percentunit",
         "Fraction of time clocks were limited by power or thermal constraints. These counters are commented "
         "out in the default dcgm-exporter CSV: enable DCGM_FI_DEV_POWER_VIOLATION and "
         "DCGM_FI_DEV_THERMAL_VIOLATION to populate this panel.", min_=0, max_=1, legend_calcs=("mean", "max"))

    d.row("Errors and health")
    d.table("Last XID error per GPU", f"max by ({by}, modelName, UUID) ({m('DCGM_FI_DEV_XID_ERRORS', S)}) != 0",
            "GPUs reporting a non-zero XID. 48 = double-bit ECC, 63/64 = row remapping, 79 = GPU fell off the bus, "
            "94/95 = contained/uncontained ECC, 13/31/43 = usually application faults. Empty table = healthy.",
            rename={"Value": "Last XID"}, value_color=True, thresholds=[(1, "red")])
    d.ts("PCIe replays", f"sum by ({by}) ({rate('DCGM_FI_DEV_PCIE_REPLAY_COUNTER', S)})", "ops",
         "PCIe link retries per second. Sustained values point to a bad riser, slot or cable.", legend=L)
    d.ts("Remapped rows", [
        (f"max by ({by}) ({m('DCGM_FI_DEV_UNCORRECTABLE_REMAPPED_ROWS', S)})", "uncorrectable " + L),
        (f"max by ({by}) ({m('DCGM_FI_DEV_CORRECTABLE_REMAPPED_ROWS', S)})", "correctable " + L),
        (f"max by ({by}) ({m('DCGM_FI_DEV_ROW_REMAP_FAILURE', S)})", "remap failure " + L)], "short",
         "HBM row remapping (A100/H100). Any remap failure or growing uncorrectable count means the GPU "
         "needs a reset or RMA.", legend_calcs=("lastNotNull", "max"))

    d.row("Per workload")
    d.bargauge("GPU utilization by pod", f'avg by (namespace, pod) ({m("DCGM_FI_DEV_GPU_UTIL", S, "pod!=\"\"")})',
               "percent", "Average utilization of the GPUs assigned to each pod (requires the dcgm-exporter "
               "Kubernetes pod mapping).", legend="{{namespace}}/{{pod}}", min_=0, max_=100,
               thresholds=[(30, "yellow"), (60, "green")])
    d.bargauge("GPU memory by pod",
               f'sum by (namespace, pod) ({m("DCGM_FI_DEV_FB_USED", S, "pod!=\"\"")}) * 1048576',
               "bytes", "Framebuffer used by the GPUs assigned to each pod.", legend="{{namespace}}/{{pod}}")
    return d


# --------------------------------------------------------------------- Volcano
def volcano():
    Q = 'queue_name=~"$queue"'
    v = [query_var("queue", "Queue", "label_values(volcano_queue_allocated_milli_cpu, queue_name)")]
    d = Dashboard("ai-obs-volcano", "Volcano scheduler",
                  "Volcano batch scheduler and controllers: scheduling latency, unschedulable work, queue "
                  "capacity (CPU, memory, GPU), pod groups and job outcomes.", ["volcano", "scheduler", "batch"], v,
                  LINK)
    d.row("Overview")
    d.stat("Unschedulable jobs", "sum(volcano_unschedule_job_count)", "short",
           "Jobs the scheduler could not place in the last session.", thresholds=[(1, "yellow"), (10, "red")])
    d.stat("Unschedulable tasks", "sum(volcano_unschedule_task_count)", "short",
           "Tasks (pods) that could not be placed.", thresholds=[(1, "yellow"), (50, "red")])
    d.stat("Pending pod groups", f"sum(volcano_queue_pod_group_pending_count{{{Q}}})", "short",
           "Pod groups in Pending state across the selected queues.")
    d.stat("Running pod groups", f"sum(volcano_queue_pod_group_running_count{{{Q}}})", "short",
           "Pod groups in Running state.")
    d.stat("E2E scheduling p95", q("0.95", "volcano_e2e_scheduling_latency_milliseconds"), "ms",
           "p95 duration of a full scheduling cycle.", thresholds=[(1000, "yellow"), (5000, "red")])
    d.stat("Preemption attempts", "sum(rate(volcano_total_preemption_attempts[$__rate_interval]))", "ops",
           "Preemption attempts per second.")

    d.row("Scheduler performance")
    d.ts("End-to-end scheduling latency", pct(P3, "volcano_e2e_scheduling_latency_milliseconds", ""), "ms",
         "Duration of each scheduling cycle (open session, actions, close session).")
    d.ts("Action latency p95", pct(("0.95",), "volcano_action_scheduling_latency_milliseconds", "", by="action",
                                   extra_legend="{{action}}"), "ms",
         "p95 per scheduler action (enqueue, allocate, preempt, reclaim, backfill).")
    d.ts("Plugin latency p95", pct(("0.95",), "volcano_plugin_scheduling_latency_milliseconds", "",
                                   by="plugin, OnSession", extra_legend="{{plugin}} {{OnSession}}"), "ms",
         "p95 per plugin, split by OnSessionOpen/OnSessionClose. Find the slow plugin here.")
    d.ts("Task scheduling latency", pct(("0.5", "0.95"), "volcano_task_scheduling_latency_milliseconds", ""), "ms",
         "Latency to schedule an individual task.")

    d.row("Queues")
    d.ts("CPU per queue", [
        (f"sum by (queue_name) (volcano_queue_allocated_milli_cpu{{{Q}}}) / 1000", "allocated {{queue_name}}"),
        (f"sum by (queue_name) (volcano_queue_deserved_milli_cpu{{{Q}}}) / 1000", "deserved {{queue_name}}"),
        (f"sum by (queue_name) (volcano_queue_request_milli_cpu{{{Q}}}) / 1000", "requested {{queue_name}}")],
         "short", "CPU cores allocated, deserved (fair share) and requested per queue.")
    d.ts("Memory per queue", [
        (f"sum by (queue_name) (volcano_queue_allocated_memory_bytes{{{Q}}})", "allocated {{queue_name}}"),
        (f"sum by (queue_name) (volcano_queue_deserved_memory_bytes{{{Q}}})", "deserved {{queue_name}}"),
        (f"sum by (queue_name) (volcano_queue_request_memory_bytes{{{Q}}})", "requested {{queue_name}}")],
         "bytes", "Memory allocated, deserved and requested per queue.")
    d.ts("Scalar resources (GPU) per queue", [
        (f"sum by (queue_name, resource) (volcano_queue_allocated_scalar_resources{{{Q}, resource!~\"pods|ephemeral-storage\"}})", "allocated {{queue_name}} {{resource}}"),
        (f"sum by (queue_name, resource) (volcano_queue_deserved_scalar_resources{{{Q}, resource!~\"pods|ephemeral-storage\"}})", "deserved {{queue_name}} {{resource}}")],
         "short", "Extended resources such as nvidia.com/gpu per queue (pods and ephemeral-storage hidden).")
    d.ts("Pod groups by state", [
        (f"sum by (queue_name) (volcano_queue_pod_group_pending_count{{{Q}}})", "pending {{queue_name}}"),
        (f"sum by (queue_name) (volcano_queue_pod_group_inqueue_count{{{Q}}})", "inqueue {{queue_name}}"),
        (f"sum by (queue_name) (volcano_queue_pod_group_running_count{{{Q}}})", "running {{queue_name}}")],
         "short", "Pod groups per state and queue (from vc-controller-manager).", stack=True)
    d.ts("Queue share", f"max by (queue_name) (volcano_queue_share{{{Q}}})", "short",
         "Dominant resource share of each queue (proportion plugin). > 1 means the queue uses more than it deserves.")
    d.table("Overused queues", f"max by (queue_name) (volcano_queue_overused{{{Q}}}) == 1",
            "Queues flagged overused by the scheduler (allocated above deserved). Empty = none.",
            rename={"Value": "Overused"})

    d.row("Jobs and preemption")
    d.ts("Job outcomes", [
        (f"sum by (queue_name) (rate(volcano_job_completed_phase_count{{{Q}}}[$__rate_interval]))", "completed {{queue_name}}"),
        (f"sum by (queue_name) (rate(volcano_job_failed_phase_count{{{Q}}}[$__rate_interval]))", "failed {{queue_name}}")],
         "ops", "Jobs reaching Completed or Failed per second.")
    d.ts("Job to pod creation latency", pct(("0.5", "0.95"), "volcano_controller_job_to_pod_creation_latency_milliseconds", ""),
         "ms", "Time from Job creation to pod creation by the job controller.")
    d.ts("Preemption", [
        ("sum(rate(volcano_total_preemption_attempts[$__rate_interval]))", "attempts/s"),
        ("sum(volcano_pod_preemption_victims)", "victims")], "short",
         "Preemption attempts per second and number of pods chosen as victims.")
    d.ts("Unschedulable work", [
        ("sum(volcano_unschedule_job_count)", "jobs"),
        ("sum(volcano_unschedule_task_count)", "tasks")], "short", "Jobs and tasks that could not be scheduled.")
    return d


# ------------------------------------------------------------- KServe / Knative
def kserve():
    SM = 'namespace=~"$namespace", model_name=~"$model_name"'
    SQ = 'namespace=~"$namespace", pod=~"$pod"'
    SR = 'k8s_namespace_name=~"$namespace", kn_revision_name=~"$revision"'
    v = [query_var("namespace", "Namespace", "label_values(kn_revision_pods_count, k8s_namespace_name)"),
         query_var("revision", "Revision",
                   'label_values(kn_revision_pods_count{k8s_namespace_name=~"$namespace"}, kn_revision_name)'),
         query_var("pod", "Pod", 'label_values(kn_serving_queue_depth{namespace=~"$namespace"}, pod)'),
         query_var("model_name", "Model", 'label_values(request_predict_seconds_count{namespace=~"$namespace"}, model_name)')]
    d = Dashboard("ai-obs-kserve-knative", "KServe / Knative model serving",
                  "KServe InferenceServices in Serverless mode: Python model server latency, Knative queue-proxy "
                  "traffic and autoscaler state. Knative panels use the OpenTelemetry metric names introduced "
                  "in Knative Serving 1.19.", ["kserve", "knative", "inference"], v, LINK)
    d.row("Overview")
    d.stat("Predict requests/s", f"sum(rate(request_predict_seconds_count{{{SM}}}[$__rate_interval]))", "reqps",
           "predict() calls per second in KServe Python model servers.")
    d.stat("Predict p95", q("0.95", "request_predict_seconds", "le", SM), "s", "p95 of model predict() time.")
    d.stat("Queue-proxy 5xx ratio",
           f'sum(rate(kn_serving_invocation_duration_seconds_count{{{SQ}, http_response_status_code=~"5.."}}[$__rate_interval])) / sum(rate(kn_serving_invocation_duration_seconds_count{{{SQ}}}[$__rate_interval]))',
           "percentunit", "Share of 5xx responses returned through the queue-proxy sidecar.",
           thresholds=[(0.01, "yellow"), (0.05, "red")])
    d.stat("Ready pods", f"sum(kn_revision_pods_count{{{SR}}})", "short", "Pods of the selected revisions.")
    d.stat("Desired pods", f"sum(kn_revision_pods_desired{{{SR}}})", "short", "Pods the autoscaler wants.")
    d.stat("Revisions in panic", f"count(kn_revision_panic_mode{{{SR}}} > 0) or vector(0)", "short",
           "Revisions where the autoscaler entered panic mode (sudden traffic burst).", thresholds=[(1, "orange")])

    d.row("KServe model server")
    d.ts("Predict latency", pct(P3, "request_predict_seconds", SM, by="model_name", extra_legend="{{model_name}}"), "s",
         "Time spent in predict() per model.")
    d.ts("Pre/post-processing p95", [
        (q("0.95", "request_preprocess_seconds", "le, model_name", SM), "preprocess {{model_name}}"),
        (q("0.95", "request_postprocess_seconds", "le, model_name", SM), "postprocess {{model_name}}")], "s",
         "Transformer pre/post-processing time per model.")
    d.ts("Predict rate by model", f"sum by (model_name) (rate(request_predict_seconds_count{{{SM}}}[$__rate_interval]))",
         "reqps", "predict() calls per second per model.")

    d.row("Knative queue-proxy")
    d.ts("Requests by status code",
         f"sum by (http_response_status_code) (rate(kn_serving_invocation_duration_seconds_count{{{SQ}}}[$__rate_interval]))",
         "reqps", "Requests per second through the queue-proxy, by HTTP status.", stack=True)
    d.ts("Request latency (queue-proxy)", pct(P3, "kn_serving_invocation_duration_seconds", SQ), "s",
         "Latency measured by the queue-proxy sidecar (includes time queued in the sidecar).")
    d.ts("Queue depth by pod", f"sum by (pod) (kn_serving_queue_depth{{{SQ}}})", "short",
         "Requests buffered in each queue-proxy (containerConcurrency reached).")
    d.ts("Activator concurrency", f"sum by (kn_revision_name) (kn_revision_request_concurrency{{{SR}}})", "short",
         "Requests routed through the activator (scale from zero or burst buffering).")

    d.row("Knative autoscaler")
    d.ts("Pods: desired vs requested vs actual", [
        (f"sum by (kn_revision_name) (kn_revision_pods_desired{{{SR}}})", "desired {{kn_revision_name}}"),
        (f"sum by (kn_revision_name) (kn_revision_pods_requested{{{SR}}})", "requested {{kn_revision_name}}"),
        (f"sum by (kn_revision_name) (kn_revision_pods_count{{{SR}}})", "actual {{kn_revision_name}}")], "short",
         "Scaling decisions vs reality. A long gap between requested and actual usually means GPU nodes are "
         "not available.")
    d.ts("Pods not ready / pending", [
        (f"sum by (kn_revision_name) (kn_revision_pods_not_ready_count{{{SR}}})", "not ready {{kn_revision_name}}"),
        (f"sum by (kn_revision_name) (kn_revision_pods_pending_count{{{SR}}})", "pending {{kn_revision_name}}")],
         "short", "Pods stuck before Ready (model download, image pull, no GPU capacity).")
    d.ts("Observed concurrency vs target", [
        (f"sum by (kn_revision_name) (kn_revision_concurrency_stable{{{SR}}})", "stable {{kn_revision_name}}"),
        (f"sum by (kn_revision_name) (kn_revision_concurrency_panic{{{SR}}})", "panic {{kn_revision_name}}"),
        (f"sum by (kn_revision_name) (kn_revision_concurrency_target{{{SR}}})", "target {{kn_revision_name}}")],
         "short", "Average concurrency over the stable and panic windows compared with the per-pod target.")
    d.ts("Excess burst capacity", f"sum by (kn_revision_name) (kn_revision_capacity_excess{{{SR}}})", "short",
         "Negative values mean the activator stays in the request path to absorb bursts.")
    return d


# ------------------------------------------------------------------ Ray Serve
def ray_serve():
    SA = 'application=~"$application"'
    SD = 'application=~"$application", deployment=~"$deployment"'
    v = [query_var("application", "Application", "label_values(ray_serve_deployment_status, application)"),
         query_var("deployment", "Deployment",
                   'label_values(ray_serve_deployment_status{application=~"$application"}, deployment)')]
    status_map = [{"type": "value", "options": {
        "0": {"text": "UNKNOWN", "color": "text"}, "1": {"text": "DEPLOY_FAILED", "color": "red"},
        "2": {"text": "UNHEALTHY", "color": "red"}, "3": {"text": "UPDATING", "color": "blue"},
        "4": {"text": "UPSCALING", "color": "blue"}, "5": {"text": "DOWNSCALING", "color": "blue"},
        "6": {"text": "HEALTHY", "color": "green"}}}]
    d = Dashboard("ai-obs-ray-serve", "Ray Serve",
                  "Ray Serve applications: HTTP ingress, per-deployment latency and errors, queueing, replica "
                  "health and autoscaling.", ["ray", "ray-serve", "inference"], v, LINK)
    d.row("Overview")
    d.stat("HTTP requests/s", f"sum(rate(ray_serve_num_http_requests_total{{{SA}}}[$__rate_interval]))", "reqps",
           "Requests processed by the Serve HTTP proxies.")
    d.stat("HTTP error ratio",
           f"sum(rate(ray_serve_num_http_error_requests_total{{{SA}}}[$__rate_interval])) / sum(rate(ray_serve_num_http_requests_total{{{SA}}}[$__rate_interval]))",
           "percentunit", "Non-2xx/3xx responses over all requests.", thresholds=[(0.01, "yellow"), (0.05, "red")])
    d.stat("HTTP latency p95", q("0.95", "ray_serve_http_request_latency_ms", "le", SA), "ms",
           "p95 end-to-end latency at the proxy.")
    d.stat("Queued requests", f"sum(ray_serve_deployment_queued_queries{{{SD}}})", "short",
           "Requests waiting for a replica (backpressure).", thresholds=[(10, "yellow"), (100, "red")])
    d.stat("Unhealthy replicas", f"count(ray_serve_deployment_replica_healthy{{{SD}}} == 0) or vector(0)", "short",
           "Replicas failing health checks.", thresholds=[(1, "red")])
    d.stat("Ongoing HTTP requests", "sum(ray_serve_num_ongoing_http_requests)", "short",
           "Requests in flight at the proxies (all applications).")

    d.row("HTTP ingress")
    d.ts("Requests by application and status",
         f"sum by (application, status_code) (rate(ray_serve_num_http_requests_total{{{SA}}}[$__rate_interval]))",
         "reqps", "HTTP requests per second.")
    d.ts("Errors by code", f"sum by (application, error_code) (rate(ray_serve_num_http_error_requests_total{{{SA}}}[$__rate_interval]))",
         "reqps", "HTTP error responses per second.")
    d.ts("HTTP latency", pct(P3, "ray_serve_http_request_latency_ms", SA, by="application",
                             extra_legend="{{application}}"), "ms", "Latency at the proxy per application.")

    d.row("Deployments")
    d.ts("Processing latency p95", pct(("0.95",), "ray_serve_deployment_processing_latency_ms", SD, by="deployment",
                                       extra_legend="{{deployment}}"), "ms",
         "Time spent in user code per deployment (excludes queueing).")
    d.ts("Requests by deployment", f"sum by (deployment) (rate(ray_serve_deployment_request_counter_total{{{SD}}}[$__rate_interval]))",
         "reqps", "Requests completed by replicas per deployment.")
    d.ts("Exceptions", f"sum by (deployment, exception_type) (rate(ray_serve_deployment_error_counter_total{{{SD}}}[$__rate_interval]))",
         "ops", "Exceptions raised in user code.")
    d.ts("Queued vs assigned requests", [
        (f"sum by (deployment) (ray_serve_deployment_queued_queries{{{SD}}})", "queued {{deployment}}"),
        (f"sum by (deployment) (ray_serve_num_ongoing_requests_at_replicas{{{SD}}})", "at replicas {{deployment}}")],
         "short", "Requests waiting for a replica vs already sent to replicas.")

    d.row("Replicas and autoscaling")
    d.ts("Replica utilization", f"avg by (deployment) (ray_serve_replica_utilization_percent{{{SD}}})", "percent",
         "Share of replica capacity in use over a rolling window.", min_=0, max_=100)
    d.ts("Replicas: healthy vs target", [
        (f"sum by (deployment) (ray_serve_deployment_replica_healthy{{{SD}}})", "healthy {{deployment}}"),
        (f"max by (deployment) (ray_serve_autoscaling_target_replicas{{{SD}}})", "target {{deployment}}")], "short",
         "Healthy replicas compared with the autoscaler target.")
    d.table("Deployment status", f"max by (application, deployment) (ray_serve_deployment_status{{{SD}}})",
            "Current status of each deployment.", mappings=status_map, rename={"Value": "Status"},
            value_color=True, thresholds=None, w=24)
    return d


# -------------------------------------------------------------------- LiteLLM
def litellm():
    S = 'requested_model=~"$model", team_alias=~"$team"'
    v = [query_var("model", "Requested model", "label_values(litellm_proxy_total_requests_metric_total, requested_model)"),
         query_var("team", "Team", "label_values(litellm_proxy_total_requests_metric_total, team_alias)")]
    state_map = [{"type": "value", "options": {
        "0": {"text": "healthy", "color": "green"}, "1": {"text": "partial outage", "color": "orange"},
        "2": {"text": "complete outage", "color": "red"}}}]
    d = Dashboard("ai-obs-litellm", "LiteLLM proxy",
                  "LiteLLM proxy (callbacks: [prometheus]): traffic, failures, latency, tokens, spend, budgets "
                  "and deployment health.", ["litellm", "llm", "gateway", "finops"], v, LINK)
    d.row("Overview")
    d.stat("Requests/s", f"sum(rate(litellm_proxy_total_requests_metric_total{{{S}}}[$__rate_interval]))", "reqps",
           "Requests handled by the proxy.")
    d.stat("Failure ratio",
           f"sum(rate(litellm_proxy_failed_requests_metric_total{{{S}}}[$__rate_interval])) / sum(rate(litellm_proxy_total_requests_metric_total{{{S}}}[$__rate_interval]))",
           "percentunit", "Failed proxy requests over all requests.", thresholds=[(0.01, "yellow"), (0.05, "red")])
    d.stat("Spend in range", f"sum(increase(litellm_spend_metric_total{{{S}}}[$__range]))", "currencyUSD",
           "Spend tracked by LiteLLM over the selected time range.", graph=False, decimals=2)
    d.stat("Tokens/s", f"sum(rate(litellm_input_tokens_metric_total{{{S}}}[$__rate_interval])) + sum(rate(litellm_output_tokens_metric_total{{{S}}}[$__rate_interval]))",
           TOKS, "Input + output tokens per second.")
    d.stat("Total latency p95", q("0.95", "litellm_request_total_latency_metric", "le", S), "s",
           "p95 total request latency at the proxy.")
    d.stat("Deployments in outage", "count(litellm_deployment_state > 0) or vector(0)", "short",
           "Deployments in partial (1) or complete (2) outage.", thresholds=[(1, "red")])

    d.row("Traffic and failures")
    d.ts("Requests by model", f"sum by (requested_model) (rate(litellm_proxy_total_requests_metric_total{{{S}}}[$__rate_interval]))",
         "reqps", "Requests per second per requested model.")
    d.ts("Requests by status code", f"sum by (status_code) (rate(litellm_proxy_total_requests_metric_total{{{S}}}[$__rate_interval]))",
         "reqps", "Requests per second by HTTP status returned to clients.", stack=True)
    d.ts("Failures by exception", f"sum by (exception_class, exception_status) (rate(litellm_proxy_failed_requests_metric_total{{{S}}}[$__rate_interval]))",
         "reqps", "Failed requests by exception class and status (RateLimitError, Timeout...).")
    d.ts("Deployment cooldowns", "sum by (litellm_model_name, exception_status) (rate(litellm_deployment_cooled_down_total[$__rate_interval]))",
         "ops", "Deployments put in cooldown by the router (after repeated failures).")

    d.row("Latency")
    d.ts("Total request latency", pct(P3, "litellm_request_total_latency_metric", S), "s",
         "Latency at the proxy including LiteLLM overhead.")
    d.ts("LLM API latency p95 by model", pct(("0.95",), "litellm_llm_api_latency_metric", S, by="requested_model",
                                             extra_legend="{{requested_model}}"), "s",
         "Latency of the upstream provider call.")
    d.ts("Time to first token p95 by model", pct(("0.95",), "litellm_llm_api_time_to_first_token_metric", S,
                                                 by="requested_model", extra_legend="{{requested_model}}"), "s",
         "Streaming time to first token from the upstream provider.")

    d.row("Tokens and spend")
    d.ts("Tokens by model", [
        (f"sum by (requested_model) (rate(litellm_input_tokens_metric_total{{{S}}}[$__rate_interval]))", "input {{requested_model}}"),
        (f"sum by (requested_model) (rate(litellm_output_tokens_metric_total{{{S}}}[$__rate_interval]))", "output {{requested_model}}")],
         TOKS, "Input and output tokens per second.")
    d.ts("Spend rate by model", f"sum by (requested_model) (rate(litellm_spend_metric_total{{{S}}}[$__rate_interval])) * 3600",
         "currencyUSD", "Spend per hour by model (USD/h).", stack=True)
    d.bargauge("Spend by team (range)", f"sum by (team_alias) (increase(litellm_spend_metric_total{{{S}}}[$__range]))",
               "currencyUSD", "Spend per team over the selected range.", legend="{{team_alias}}")
    d.bargauge("Spend by API key (range)", f"topk(10, sum by (api_key_alias) (increase(litellm_spend_metric_total{{{S}}}[$__range])))",
               "currencyUSD", "Top 10 virtual keys by spend over the selected range.", legend="{{api_key_alias}}")

    d.row("Budgets and deployment health")
    d.bargauge("Remaining team budget", "max by (team_alias) (litellm_remaining_team_budget_metric)", "currencyUSD",
               "Budget left per team.", legend="{{team_alias}}")
    d.bargauge("Remaining API key budget", "max by (api_key_alias) (litellm_remaining_api_key_budget_metric)",
               "currencyUSD", "Budget left per virtual key.", legend="{{api_key_alias}}")
    d.table("Deployment state", "max by (model_group, litellm_model_name, api_provider) (litellm_deployment_state)",
            "Health of each upstream deployment as seen by the LiteLLM router.", mappings=state_map,
            rename={"Value": "State"}, value_color=True)
    d.ts("Remaining provider rate limits", [
        ("min by (model_group) (litellm_remaining_requests_metric)", "requests {{model_group}}"),
        ("min by (model_group) (litellm_remaining_tokens_metric)", "tokens {{model_group}}")], "short",
         "Remaining requests and tokens from the provider rate-limit headers. Near zero means 429s are coming.")
    return d


BUILDERS = {
    "vllm": vllm, "vllm-production-stack-router": vllm_router, "sglang": sglang, "llama-cpp": llamacpp,
    "hf-tei": tei, "triton": triton, "nvidia-dcgm": dcgm, "volcano": volcano, "kserve-knative": kserve,
    "ray-serve": ray_serve, "litellm": litellm,
}

if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    for name, fn in BUILDERS.items():
        with open(os.path.join(OUT, f"{name}.json"), "w") as f:
            json.dump(fn().to_json(), f, indent=2)
            f.write("\n")
        print("wrote", name)
