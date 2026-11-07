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
           f"(sum({rate('vllm:request_errors_total', SM)}) or vector(0)) / sum({rate('vllm:num_incoming_requests_total', SM)})",
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
           f"(sum({rate('nv_inference_request_failure', S)}) or vector(0)) / (sum({rate('nv_inference_request_success', S)}) + (sum({rate('nv_inference_request_failure', S)}) or vector(0)))",
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
           f'(sum(rate(kn_serving_invocation_duration_seconds_count{{{SQ}, http_response_status_code=~"5.."}}[$__rate_interval])) or vector(0)) / sum(rate(kn_serving_invocation_duration_seconds_count{{{SQ}}}[$__rate_interval]))',
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
           f"(sum(rate(ray_serve_num_http_error_requests_total{{{SA}}}[$__rate_interval])) or vector(0)) / sum(rate(ray_serve_num_http_requests_total{{{SA}}}[$__rate_interval]))",
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
                  "LiteLLM proxy (callbacks: [prometheus]): traffic, failures, latency, tokens, spend, budgets, "
                  "deployment health and MCP tool calls.", ["litellm", "llm", "gateway", "finops"], v, LINK)
    d.row("Overview")
    d.stat("Requests/s", f"sum(rate(litellm_proxy_total_requests_metric_total{{{S}}}[$__rate_interval]))", "reqps",
           "Requests handled by the proxy.")
    d.stat("Failure ratio",
           f"(sum(rate(litellm_proxy_failed_requests_metric_total{{{S}}}[$__rate_interval])) or vector(0)) / sum(rate(litellm_proxy_total_requests_metric_total{{{S}}}[$__rate_interval]))",
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

    d.row("MCP tool calls")
    SK = 'team_alias=~"$team"'
    d.ts("MCP tool calls by server", f"sum by (mcp_server_name) (rate(litellm_mcp_tool_calls_total{{{SK}}}[$__rate_interval]))",
         "reqps", "Tool calls made through the LiteLLM MCP gateway per MCP server.", legend="{{mcp_server_name}}")
    d.ts("MCP tool calls by tool", f"topk(10, sum by (mcp_server_name, mcp_tool_name) (rate(litellm_mcp_tool_calls_total{{{SK}}}[$__rate_interval])))",
         "reqps", "Top 10 tools by call rate.", legend="{{mcp_server_name}} / {{mcp_tool_name}}")
    d.bargauge("MCP tool calls by team (range)", f"sum by (team_alias) (increase(litellm_mcp_tool_calls_total{{{SK}}}[$__range]))",
               "short", "Tool calls per team over the selected range.", legend="{{team_alias}}")
    d.bargauge("MCP tool spend (range)", f"topk(10, sum by (mcp_server_name, mcp_tool_name) (increase(litellm_mcp_tool_call_spend_metric_total{{{SK}}}[$__range])))",
               "currencyUSD", "Spend attributed to MCP tools that have a cost configured.", legend="{{mcp_server_name}} / {{mcp_tool_name}}")
    return d

ST5, ST4, SC5, NOTREADY = 'status=~"5.."', 'status=~"4.."', 'status_code=~"5.."', 'status!="READY"'


# --------------------------------------------------------------------- Qdrant
def qdrant():
    S = 'namespace=~"$namespace", pod=~"$pod"'
    SC = S + ', id=~"$collection"'
    v = ns_pod_vars("app_info") + [
        query_var("collection", "Collection", 'label_values(collection_points{namespace=~"$namespace", pod=~"$pod"}, id)')]
    d = Dashboard("ai-obs-qdrant", "Qdrant",
                  "Qdrant vector database (/metrics on the REST port 6333): REST traffic and latency per endpoint, "
                  "collections and points, optimizer and update queues, replicas and shard transfers, snapshots and memory. "
                  "Qdrant metric names have no prefix (app_info, collection_points...), so keep the namespace/pod filters on.",
                  ["qdrant", "vector-db", "rag"], v, LINK)
    d.no_value = "no data"
    d.row("Overview")
    d.stat("Collections", f"max({m('collections_total', S)})", "short", "Collections on the selected nodes.")
    d.stat("Points", f"sum({m('collection_points', SC)})", "short", "Points stored in the selected collections.")
    d.stat("REST requests", f"sum({rate('rest_responses_total', S)})", "reqps", "REST API requests per second.")
    d.stat("REST 5xx ratio", f'(sum({rate("rest_responses_total", S, ST5)}) or vector(0)) / sum({rate("rest_responses_total", S)})',
           "percentunit", "Share of REST responses with a 5xx status (4xx are client errors and not counted).",
           thresholds=[(0.01, "yellow"), (0.05, "red")])
    d.stat("REST p95", q("0.95", "rest_responses_duration_seconds", "le", S), "s",
           "95th percentile REST response time across endpoints.", thresholds=[(0.1, "yellow"), (0.5, "red")])
    d.stat("Dead replicas", f"max({m('collection_dead_replicas', S)})", "short",
           "Shard replicas in Dead state. Anything above 0 means a node lost data for some shard and needs recovery.",
           thresholds=[(1, "red")])

    d.row("REST traffic")
    d.ts("Requests by endpoint and status", f"sum by (endpoint, status) ({rate('rest_responses_total', S)})", "reqps",
         "REST requests per endpoint template and HTTP status.", legend="{{status}} {{endpoint}}")
    d.ts("Latency by endpoint (p95)", f"histogram_quantile(0.95, sum by (le, endpoint) ({rate('rest_responses_duration_seconds_bucket', S)}))",
         "s", "95th percentile response time per endpoint. Search (points/query, points/search) is usually the one to watch.",
         legend="{{endpoint}}")
    d.ts("Max latency by endpoint", f"max by (endpoint) ({m('rest_responses_max_duration_seconds', S)})", "s",
         "Slowest response seen per endpoint, as tracked by Qdrant itself (since start).", legend="{{endpoint}}")
    d.ts("Client errors (4xx)", f'sum by (endpoint, status) ({rate("rest_responses_total", S, ST4)})', "reqps",
         "400 = bad request (wrong vector size, bad filter), 404 = missing collection or point.", legend="{{status}} {{endpoint}}",
         no_value="no 4xx")

    d.row("Collections")
    d.ts("Points per collection", f"sum by (id) ({m('collection_points', SC)})", "short",
         "Points stored per collection.", legend="{{id}}")
    d.ts("Running optimizations", f"sum by (id) ({m('collection_running_optimizations', SC)})", "short",
         "Optimizer jobs (index building, segment merging) running per collection. Long-running optimizations slow down writes.",
         legend="{{id}}")
    d.ts("Update queue", [
        (f"sum by (id) ({m('collection_update_queue_length', SC)})", "queued updates {{id}}"),
        (f"sum by (id) ({m('collection_update_queue_deferred_points', SC)})", "deferred points {{id}}")], "short",
         "Pending update operations and points deferred until indexing catches up.")
    d.ts("Hardware usage by collection", [
        (f"sum by (id) (rate(collection_hardware_metric_cpu{{{SC}}}[$__rate_interval]))", "cpu {{id}}")], "short",
         "CPU units Qdrant attributes to each collection (collection_hardware_metric_cpu, a cumulative counter of internal CPU units).")
    d.ts("Vector and payload I/O by collection", [
        (f"sum by (id) (rate(collection_hardware_metric_vector_io_write{{{SC}}}[$__rate_interval]))", "vector write {{id}}"),
        (f"sum by (id) (rate(collection_hardware_metric_vector_io_read{{{SC}}}[$__rate_interval]))", "vector read {{id}}"),
        (f"sum by (id) (rate(collection_hardware_metric_payload_io_read{{{SC}}}[$__rate_interval]))", "payload read {{id}}")],
         "Bps", "Bytes read and written for vectors and payloads per collection.")

    d.row("Replication, snapshots and memory")
    d.ts("Replicas", [(f"min({m('collection_active_replicas_min', S)})", "min active replicas"),
                      (f"max({m('collection_dead_replicas', S)})", "dead replicas")], "short",
         "Lowest number of active replicas of any shard, and dead replicas. In distributed mode a min below the replication factor means a degraded shard.")
    d.ts("Shard transfers", [
        (f"sum({m('collection_shard_transfer_incoming', S)})", "incoming"),
        (f"sum({m('collection_shard_transfer_outgoing', S)})", "outgoing"),
        (f"sum({m('collection_shard_transfer_failed', S)})", "failed")], "short",
         "Shard transfers in progress (resharding, replica recovery) and failed ones.")
    d.ts("Snapshots", [
        (f"sum by (id) (increase(snapshot_created_total{{{SC}}}[$__range]))", "created {{id}}"),
        (f"sum by (id) ({m('snapshot_creation_running', SC)})", "running {{id}}")], "short",
         "Snapshots created in the selected range and snapshot jobs running.")
    d.ts("Memory", [(f"max by (pod) ({m('memory_resident_bytes', S)})", "resident {{pod}}"),
                    (f"max by (pod) ({m('memory_allocated_bytes', S)})", "allocated {{pod}}")], "bytes",
         "Resident and allocator-reported memory. Vectors kept in RAM (on_disk=false) dominate this.")
    return d


# ------------------------------------------------------------------- Weaviate
def weaviate():
    S = 'namespace=~"$namespace", pod=~"$pod"'
    SC = S + ', class_name=~"$collection"'
    v = ns_pod_vars("weaviate_build_info") + [
        query_var("collection", "Collection", 'label_values(vector_index_size{namespace=~"$namespace", pod=~"$pod"}, class_name)')]
    d = Dashboard("ai-obs-weaviate", "Weaviate",
                  "Weaviate vector database (PROMETHEUS_MONITORING_ENABLED=true, port 2112): HTTP API, queries, batch "
                  "imports, vector index (HNSW) size and queues, LSM storage and shards.", ["weaviate", "vector-db", "rag"], v, LINK)
    d.no_value = "no data"
    d.row("Overview")
    d.stat("Collections", f"max({m('weaviate_schema_collections', S)})", "short", "Collections (classes) in the schema.")
    d.stat("Vectors indexed", f"sum({m('vector_index_size', SC)})", "short", "Vectors in the HNSW indexes of the selected collections.")
    d.stat("Queries/s", f"sum({rate('queries_durations_ms_count', SC)})", "reqps", "Search queries per second (GraphQL Get, gRPC search).")
    d.stat("Query p95", q("0.95", "queries_durations_ms", "le", SC), "ms", "95th percentile query time.",
           thresholds=[(100, "yellow"), (500, "red")])
    d.stat("HTTP 5xx ratio", f'(sum({rate("weaviate_http_request_duration_seconds_count", S, SC5)}) or vector(0)) / sum({rate("weaviate_http_request_duration_seconds_count", S)})',
           "percentunit", "Share of REST/GraphQL HTTP requests answered with 5xx.", thresholds=[(0.01, "yellow"), (0.05, "red")])
    d.stat("Shards not ready", f'sum({m("weaviate_index_shards_total", S, NOTREADY)})', "short",
           "Shards loading or in another non-READY state.", thresholds=[(1, "orange")])

    d.row("API")
    d.ts("HTTP requests by route and status", f"sum by (route, status_code) ({rate('weaviate_http_request_duration_seconds_count', S)})",
         "reqps", "REST and GraphQL requests per route.", legend="{{status_code}} {{route}}")
    d.ts("HTTP latency by route (p95)", f"histogram_quantile(0.95, sum by (le, route) ({rate('weaviate_http_request_duration_seconds_bucket', S)}))",
         "s", "95th percentile HTTP time per route.", legend="{{route}}")
    d.ts("Requests by API and outcome", f"sum by (api, status) ({rate('requests_total', S)})", "reqps",
         "Weaviate's own request counter (requests_total, exported as a gauge that only goes up): ok, user_error, server_error per API.",
         legend="{{api}} {{status}}")
    d.ts("In-flight HTTP requests", f"sum by (route) ({m('weaviate_http_requests_inflight', S)})", "short",
         "Requests being served now per route.", legend="{{route}}")

    d.row("Queries")
    d.ts("Queries by collection and type", f"sum by (class_name, query_type) ({rate('queries_durations_ms_count', SC)})", "reqps",
         "Query rate per collection and query type.", legend="{{class_name}} {{query_type}}")
    d.ts("Query latency", pct(("0.5", "0.95", "0.99"), "queries_durations_ms", SC), "ms",
         "Query time distribution across the selected collections.")
    d.ts("Query dimensions", f"sum by (class_name, operation) ({rate('query_dimensions_total', SC)})", "short",
         "Vector dimensions searched per second (nearVector, nearText, hybrid). Weaviate Cloud bills on this.",
         legend="{{class_name}} {{operation}}")
    d.ts("Batch import latency (p95)", f"histogram_quantile(0.95, sum by (le, operation) ({rate('batch_durations_ms_bucket', SC)}))",
         "ms", "Time to store a batch, by phase (object_storage, vector_storage...).", legend="{{operation}}")

    d.row("Vector index and storage")
    d.ts("Vectors per collection", f"sum by (class_name) ({m('vector_index_size', SC)})", "short",
         "HNSW index size per collection.", legend="{{class_name}}")
    d.ts("Tombstones", f"sum by (class_name) ({m('vector_index_tombstones', SC)})", "short",
         "Deleted vectors not yet cleaned from the HNSW graph. A growing number slows queries until cleanup runs.",
         legend="{{class_name}}")
    d.ts("Objects imported", f"sum by (class_name) ({rate('batch_objects_processed_total', SC)})", "ops",
         "Objects processed by batch imports per second.", legend="{{class_name}}")
    d.ts("LSM memtable size", f"sum by (class_name, strategy) ({m('lsm_memtable_size', SC)})", "bytes",
         "Bytes held in LSM memtables before they are flushed to disk segments.", legend="{{class_name}} {{strategy}}")
    d.ts("Shards by state", f"sum by (state) ({m('weaviate_shards', S)})", "short",
         "Shards loaded, loading, unloaded (lazy loading of tenants or large collections).", legend="{{state}}")
    d.ts("Process memory", f"max by (pod) ({m('process_resident_memory_bytes', S, '')})", "bytes",
         "Resident memory of Weaviate. HNSW graphs and vector cache live in memory.", legend="{{pod}}")
    return d


NOT_UPSTREAM, KNOWN_RESOURCE, NOT_SUCCESS = 'reason!="Upstream"', 'resource_type!="unknown"', 'status!="success"'
STATUS_ERROR, STATUS_FAILURE, TOOLS_CALL = 'status="error"', 'status="failure"', 'mcp_method_name="tools/call"'
CLASS_5XX = 'http_response_status_class="5xx"'


def hq(level, metric, by, sel):
    """histogram_quantile grouped by le plus one label."""
    return f"histogram_quantile({level}, sum by (le, {by}) (rate({metric}_bucket{{{sel}}}[$__rate_interval])))"


# --------------------------------------------------------------- agentgateway
def agentgateway():
    S = 'namespace=~"$namespace", pod=~"$pod"'
    SG = S + ', gateway=~"$gateway"'
    v = ns_pod_vars("agentgateway_build_info") + [
        query_var("gateway", "Gateway", 'label_values(agentgateway_requests_total{namespace=~"$namespace", pod=~"$pod"}, gateway)')]
    REQ, MCP = "agentgateway_requests_total", "agentgateway_mcp_requests_total"
    MCP_ERR = 'outcome=~"tool_error|protocol_error|gateway_error"'
    d = Dashboard("ai-obs-agentgateway", "agentgateway",
                  "agentgateway (MCP, A2A and LLM gateway, /metrics on port 15020): HTTP traffic by backend protocol, "
                  "MCP calls by method, target server and tool, MCP outcomes, LLM tokens, latency, cost and guardrails, "
                  "connections and config sync.", ["agentgateway", "mcp", "a2a", "gateway", "llm"], v, LINK)
    d.row("Overview")
    d.stat("Requests/s", f"sum({rate(REQ, SG)})", "reqps", "HTTP requests handled by the gateway (all backend protocols).")
    d.stat("5xx ratio", f'(sum({rate(REQ, SG, ST5)}) or vector(0)) / sum({rate(REQ, SG)})', "percentunit",
           "Share of responses with a 5xx status, upstream or generated by the gateway.",
           thresholds=[(0.01, "yellow"), (0.05, "red")])
    d.stat("Request p95", q("0.95", "agentgateway_request_duration_seconds", "le", SG), "s",
           "95th percentile HTTP request duration at the gateway.", thresholds=[(1, "yellow"), (5, "red")])
    d.stat("MCP calls/s", f"sum({rate(MCP, SG)})", "reqps", "MCP JSON-RPC requests per second (initialize, tools/list, tools/call...).")
    d.stat("MCP error ratio", f"(sum({rate(MCP, SG, MCP_ERR)}) or vector(0)) / sum({rate(MCP, SG)})", "percentunit",
           "MCP requests whose outcome is tool_error, protocol_error or gateway_error.",
           thresholds=[(0.02, "yellow"), (0.1, "red")])
    d.stat("LLM cost in range", f"sum(increase(agentgateway_gen_ai_client_cost_usd_total{{{SG}}}[$__range]))", "currencyUSD",
           "USD cost of LLM requests over the selected range, from the gateway model cost catalog.", graph=False, decimals=2)

    d.row("HTTP traffic")
    d.ts("Requests by backend protocol and status", f"sum by (protocol, status) ({rate(REQ, SG)})", "reqps",
         "protocol is the backend type: http, mcp, a2a, llm or tcp.", legend="{{protocol}} {{status}}")
    d.ts("Gateway-generated responses", f'sum by (reason, status) ({rate(REQ, SG, NOT_UPSTREAM)})',
         "reqps", "Responses not produced by the backend: auth failures (JwtAuth, APIKeyAuth, ExtAuth, Authorization), "
         "rate limits, NoHealthyBackend, timeouts.", legend="{{reason}} {{status}}", no_value="none")
    d.ts("Request latency", pct(P3, "agentgateway_request_duration_seconds", SG), "s",
         "HTTP request duration measured by the gateway, including the backend.")
    d.ts("Gateway overhead (p95)", [
        (q("0.95", "agentgateway_request_processing_seconds", "le", SG), "request processing"),
        (q("0.95", "agentgateway_response_processing_seconds", "le", SG), "response processing")], "s",
         "Time spent inside the gateway before the outbound call and after the response (policies, transformations, guardrails).")
    d.ts("In-flight requests and upstream connections", [
        (f"sum by (backend) ({m('agentgateway_requests_active', S)})", "requests {{backend}}"),
        (f"sum by (backend) ({m('agentgateway_upstream_connections_active', S)})", "connections {{backend}}")], "short",
         "Requests awaiting a complete response and open upstream connections per backend.")
    d.ts("Retries and load shedding", [
        (f"sum({rate('agentgateway_retries_total', SG)})", "retries"),
        (f"sum({rate('agentgateway_requests_shed_total', S)})", "requests shed"),
        (f"sum({rate('agentgateway_downstream_connections_shed_total', S)})", "connections shed")], "ops",
         "Retries sent to backends and requests or connections rejected by the in-flight and connection limits.")

    d.row("MCP")
    d.ts("MCP requests by method", f"sum by (method) ({rate(MCP, SG)})", "reqps",
         "MCP JSON-RPC requests per method.", legend="{{method}}")
    d.ts("MCP outcomes", f"sum by (outcome) ({rate(MCP, SG)})", "reqps",
         "success; tool_error = the tool returned isError; protocol_error = JSON-RPC error; gateway_error = 4xx/5xx "
         "or a gateway failure; unknown = no result seen (e.g. streamed).", stack=True, legend="{{outcome}}")
    d.ts("MCP calls by target server", f"sum by (server) ({rate(MCP, SG)})", "reqps",
         "Requests per upstream MCP server (target) after multiplexing.", legend="{{server}}")
    d.ts("MCP errors by server", f"sum by (server, outcome) ({rate(MCP, SG, MCP_ERR)})", "reqps",
         "Failed MCP requests per target and outcome.", legend="{{server}} {{outcome}}", no_value="no errors")
    d.bargauge("Top tools (range)", f'topk(10, sum by (server, resource) (increase({MCP}{{{SG}, resource_type="tool"}}[$__range])))',
               "short", "Most called tools over the selected range.", legend="{{server}} / {{resource}}")
    d.ts("Tool, prompt and resource calls", f"sum by (resource_type) ({rate(MCP, SG, KNOWN_RESOURCE)})",
         "reqps", "MCP requests that target a tool, prompt, resource, resource template or task.", legend="{{resource_type}}")

    d.row("LLM")
    TOK = "agentgateway_gen_ai_client_token_usage"
    d.ts("Tokens by model", f"sum by (gen_ai_request_model, gen_ai_token_type) (rate({TOK}_sum{{{SG}}}[$__rate_interval]))",
         TOKS, "Input, output and cache tokens per second by requested model.",
         legend="{{gen_ai_token_type}} {{gen_ai_request_model}}")
    d.ts("Cost rate by model", f"sum by (gen_ai_request_model) (rate(agentgateway_gen_ai_client_cost_usd_total{{{SG}}}[$__rate_interval])) * 3600",
         "currencyUSD", "USD per hour by requested model.", stack=True, legend="{{gen_ai_request_model}}")
    d.ts("LLM request duration p95 by model", hq("0.95", "agentgateway_gen_ai_server_request_duration", "gen_ai_request_model", SG),
         "s", "Duration of LLM requests through the gateway.", legend="{{gen_ai_request_model}}")
    d.ts("Time to first token p95 by model", hq("0.95", "agentgateway_gen_ai_server_time_to_first_token", "gen_ai_request_model", SG),
         "s", "Streaming time to first token from the provider.", legend="{{gen_ai_request_model}}")
    d.ts("Time per output token p95 by model", hq("0.95", "agentgateway_gen_ai_server_time_per_output_token", "gen_ai_request_model", SG),
         "s", "Decode speed as seen by the gateway.", legend="{{gen_ai_request_model}}")
    d.ts("Failed LLM requests by model",
         f'sum by (gen_ai_request_model) (rate(agentgateway_gen_ai_server_request_duration_count{{{SG}, error_type="_OTHER"}}[$__rate_interval]))',
         "reqps", "LLM requests recorded with error_type=_OTHER.", legend="{{gen_ai_request_model}}", no_value="no errors")
    d.ts("Guardrail checks", f"sum by (phase, action) ({rate('agentgateway_guardrail_checks_total', S)})", "ops",
         "Guardrail verdicts on requests and responses: allow, mask, reject, audit, failOpen.", legend="{{phase}} {{action}}")
    d.ts("Cost catalog lookups", f"sum by (status) ({rate('agentgateway_cost_catalog_lookups_total', SG)})", "ops",
         "Model price lookups by result. Misses mean the cost metric undercounts for that model.", legend="{{status}}")

    d.row("Connections and config")
    d.ts("Downstream connections", f"sum by (protocol) ({rate('agentgateway_downstream_connections_total', S)})", "ops",
         "New client connections per second by protocol.", legend="{{protocol}}")
    d.ts("Upstream connect and TLS handshake (p95)", [
        (q("0.95", "agentgateway_upstream_connect_duration_seconds", "le", S), "upstream connect"),
        (q("0.95", "agentgateway_tls_handshake_duration_seconds", "le", S), "inbound TLS handshake")], "s",
         "Time to open upstream connections and to complete inbound TLS handshakes.")
    d.ts("Config synchronized", f"min by (pod) ({m('agentgateway_config_synchronized', S)})", "short",
         "1 when the last configuration load or reload succeeded, 0 when the running config is stale.",
         min_=0, max_=1, legend="{{pod}}")
    d.ts("Traffic volume", [
        (f"sum({rate('agentgateway_downstream_received_bytes_total', S)})", "received"),
        (f"sum({rate('agentgateway_downstream_sent_bytes_total', S)})", "sent")], "Bps",
         "Bytes received from and sent to clients.")
    return d


# ----------------------------------------------------- Envoy AI Gateway / Agent Router
def envoy_ai_gateway():
    S = 'namespace=~"$namespace", pod=~"$pod"'
    SC = S + ', otel_scope_name="envoyproxy/ai-gateway"'
    SM = SC + ', gen_ai_request_model=~"$model", gen_ai_backend=~"$backend"'
    DUR, TTFT, TPOT, TOK = ("gen_ai_server_request_duration_seconds", "gen_ai_server_time_to_first_token_seconds",
                            "gen_ai_server_time_per_output_token_seconds", "gen_ai_client_token_usage")
    MC, MD = "mcp_method_count_total", "mcp_request_duration"
    v = [query_var("namespace", "Namespace", f'label_values({DUR}_count{{otel_scope_name="envoyproxy/ai-gateway"}}, namespace)'),
         query_var("pod", "Pod", f'label_values({DUR}_count{{otel_scope_name="envoyproxy/ai-gateway", namespace=~"$namespace"}}, pod)'),
        query_var("model", "Model", f'label_values({DUR}_count{{namespace=~"$namespace", pod=~"$pod"}}, gen_ai_request_model)'),
        query_var("backend", "AI backend", f'label_values({DUR}_count{{namespace=~"$namespace", pod=~"$pod"}}, gen_ai_backend)')]
    d = Dashboard("ai-obs-envoy-ai-gateway", "Envoy AI Gateway",
                  "Envoy AI Gateway / Agent Router external processor (/metrics on admin port 1064): LLM requests, errors, "
                  "latency, TTFT, tokens by type (OpenTelemetry GenAI names) and the MCP proxy (methods, errors, latency, "
                  "capabilities).", ["envoy-ai-gateway", "gateway", "llm", "mcp"], v, LINK)
    d.row("Overview")
    d.stat("LLM requests/s", f"sum(rate({DUR}_count{{{SM}}}[$__rate_interval]))", "reqps", "LLM requests processed by the gateway.")
    d.stat("LLM error ratio", f'(sum(rate({DUR}_count{{{SM}, error_type!=""}}[$__rate_interval])) or vector(0)) / sum(rate({DUR}_count{{{SM}}}[$__rate_interval]))',
           "percentunit", "Requests recorded with an error.type attribute (failed upstream or gateway processing).",
           thresholds=[(0.01, "yellow"), (0.05, "red")])
    d.stat("Request p95", q("0.95", DUR, "le", SM), "s", "95th percentile LLM request duration, headers in to last byte out.",
           thresholds=[(10, "yellow"), (30, "red")])
    d.stat("TTFT p95", q("0.95", TTFT, "le", SM), "s", "95th percentile time to first token on streaming responses.",
           thresholds=[(1, "yellow"), (3, "red")])
    d.stat("Tokens/s", f'sum(rate({TOK}_sum{{{SM}, gen_ai_token_type=~"input|output"}}[$__rate_interval]))', TOKS,
           "Input + output tokens per second.")
    d.stat("MCP requests/s", f"sum({rate(MC, SC)})", "reqps", "MCP methods handled by the MCP proxy.")

    d.row("LLM traffic")
    d.ts("Requests by model and provider", f"sum by (gen_ai_request_model, gen_ai_provider_name) (rate({DUR}_count{{{SM}}}[$__rate_interval]))",
         "reqps", "LLM requests per requested model and upstream provider.", legend="{{gen_ai_provider_name}} {{gen_ai_request_model}}")
    d.ts("Errors by model and type", f'sum by (gen_ai_request_model, error_type) (rate({DUR}_count{{{SM}, error_type!=""}}[$__rate_interval]))',
         "reqps", "Failed requests per model.", legend="{{gen_ai_request_model}} {{error_type}}", no_value="no errors")
    d.ts("Requests by operation", f"sum by (gen_ai_operation_name) (rate({DUR}_count{{{SM}}}[$__rate_interval]))", "reqps",
         "chat, completion, embeddings, messages, responses, rerank, image_generation...", stack=True, legend="{{gen_ai_operation_name}}")
    d.ts("Requests by AI backend", f"sum by (gen_ai_backend) (rate({DUR}_count{{{SM}}}[$__rate_interval]))", "reqps",
         "Requests per AIServiceBackend (namespace/name) that served them.", legend="{{gen_ai_backend}}")

    d.row("Latency")
    d.ts("Request duration", pct(P3, DUR, SM), "s", "From request headers in the external processor to the end of the response body.")
    d.ts("Time to first token", pct(P3, TTFT, SM), "s", "Streaming responses only.")
    d.ts("Request duration p95 by model", hq("0.95", DUR, "gen_ai_request_model", SM), "s",
         "Latency per requested model.", legend="{{gen_ai_request_model}}")
    d.ts("Time per output token p95 by model", hq("0.95", TPOT, "gen_ai_request_model", SM), "s",
         "(duration - TTFT) / (output tokens - 1), successful responses only.", legend="{{gen_ai_request_model}}")

    d.row("Tokens")
    d.ts("Tokens by type", f"sum by (gen_ai_token_type) (rate({TOK}_sum{{{SM}}}[$__rate_interval]))", TOKS,
         "input, output, cached_input, cache_creation_input and reasoning tokens per second.", legend="{{gen_ai_token_type}}")
    d.ts("Tokens by model", [
        (f'sum by (gen_ai_request_model) (rate({TOK}_sum{{{SM}, gen_ai_token_type="input"}}[$__rate_interval]))', "input {{gen_ai_request_model}}"),
        (f'sum by (gen_ai_request_model) (rate({TOK}_sum{{{SM}, gen_ai_token_type="output"}}[$__rate_interval]))', "output {{gen_ai_request_model}}")],
         TOKS, "Input and output tokens per second per model.")
    d.ts("Prompt cache hit share", f'sum by (gen_ai_request_model) (rate({TOK}_sum{{{SM}, gen_ai_token_type="cached_input"}}[$__rate_interval])) '
         f'/ sum by (gen_ai_request_model) (rate({TOK}_sum{{{SM}, gen_ai_token_type="input"}}[$__rate_interval]))', "percentunit",
         "Share of input tokens served from the provider prompt cache.", min_=0, max_=1, legend="{{gen_ai_request_model}}")
    d.ts("Output tokens per request (p95)", f'histogram_quantile(0.95, sum by (le, gen_ai_request_model) (rate({TOK}_bucket{{{SM}, gen_ai_token_type="output"}}[$__rate_interval])))',
         "short", "Distribution of generated tokens per request.", legend="{{gen_ai_request_model}}")

    d.row("MCP proxy")
    d.ts("MCP methods by status", f"sum by (mcp_method_name, status) ({rate(MC, SC)})", "reqps",
         "MCP JSON-RPC methods handled by the proxy. status is success, failed or error.", legend="{{mcp_method_name}} {{status}}")
    d.ts("MCP failures by method", f'sum by (mcp_method_name, status) ({rate(MC, SC, NOT_SUCCESS)})', "reqps",
         "Methods that did not succeed.", legend="{{mcp_method_name}} {{status}}", no_value="no errors")
    d.ts("MCP request latency", pct(P3, MD, SC), "s", "Duration of MCP requests through the proxy (mcp.request.duration, recorded in seconds).")
    d.ts("MCP errors by type", f'sum by (error_type) (rate({MD}_count{{{SC}, error_type!=""}}[$__rate_interval]))', "reqps",
         "invalid_json_rpc, unsupported_method, invalid_session_id, internal_error...", legend="{{error_type}}", no_value="no errors")
    d.ts("MCP requests by backend", f"sum by (mcp_backend) ({rate(MC, SC)})", "reqps", "MCP traffic per upstream MCP backend.",
         legend="{{mcp_backend}}")
    d.ts("Capabilities negotiated", f"sum by (capability_type, capability_side) ({rate('mcp_capabilities_negotiated_total', SC)})", "ops",
         "Capabilities announced by clients and servers during initialize (tools, resources, prompts, sampling, elicitation...).",
         legend="{{capability_side}} {{capability_type}}")
    return d


# ------------------------------------------------------------------ ToolHive
def toolhive():
    S = 'namespace=~"$namespace", pod=~"$pod"'
    SS = S + ', server=~"$server"'
    REQ, DUR = "toolhive_mcp_requests_total", "toolhive_mcp_request_duration_seconds"
    v = ns_pod_vars(REQ) + [
        query_var("server", "MCP server", f'label_values({REQ}{{namespace=~"$namespace", pod=~"$pod"}}, server)')]
    d = Dashboard("ai-obs-toolhive", "ToolHive",
                  "Stacklok ToolHive MCP servers and Virtual MCP (enablePrometheusMetricsPath, diagnostics port 9464): MCP "
                  "requests, errors and latency per server, tool calls, active connections, vMCP backends and workflows, "
                  "Redis rate limits and the embedded auth server.", ["toolhive", "mcp"], v, LINK)
    d.row("Overview")
    d.stat("MCP requests/s", f"sum({rate(REQ, SS)})", "reqps", "MCP requests proxied to the selected servers.")
    d.stat("Error ratio", f'(sum({rate(REQ, SS, STATUS_ERROR)}) or vector(0)) / sum({rate(REQ, SS)})',
           "percentunit", "Requests answered with HTTP status >= 400.", thresholds=[(0.02, "yellow"), (0.1, "red")])
    d.stat("Request p95", q("0.95", DUR, "le", SS), "s", "95th percentile MCP request duration.", thresholds=[(2, "yellow"), (10, "red")])
    d.stat("Tool calls/s", f"sum({rate('toolhive_mcp_tool_calls_total', SS)})", "reqps", "tools/call requests per second.")
    d.stat("Active connections", f"sum({m('toolhive_mcp_active_connections', SS)})", "short", "Open MCP connections (SSE streams).")
    d.stat("Rate-limited/s", f'sum(rate(toolhive_rate_limit_decisions_total{{{S}, decision="rejected"}}[$__rate_interval])) or vector(0)',
           "reqps", "Requests rejected by the Redis rate limiter.", thresholds=[(0.1, "yellow"), (1, "red")])

    d.row("MCP requests")
    d.ts("Requests by MCP method", f"sum by (mcp_method) ({rate(REQ, SS)})", "reqps",
         "initialize, tools/list, tools/call, resources/read... sse_connection counts new SSE streams.", legend="{{mcp_method}}")
    d.ts("Requests by server and status", f"sum by (server, status_code) ({rate(REQ, SS)})", "reqps",
         "HTTP status returned per MCP server.", legend="{{server}} {{status_code}}")
    d.ts("Request latency", pct(P3, DUR, SS), "s", "MCP request duration measured by the ToolHive proxy.")
    d.ts("Latency p95 by method", hq("0.95", DUR, "mcp_method", SS), "s", "Slow methods usually are tools/call on a slow backend.",
         legend="{{mcp_method}}")

    d.row("Tools")
    d.ts("Tool calls by tool", f"sum by (server, tool) ({rate('toolhive_mcp_tool_calls_total', SS)})", "reqps",
         "tools/call per server and tool.", legend="{{server}} / {{tool}}")
    d.ts("Tool errors", f'sum by (server, tool) ({rate("toolhive_mcp_tool_calls_total", SS, STATUS_ERROR)})', "reqps",
         "Tool calls that failed at the HTTP level.", legend="{{server}} / {{tool}}", no_value="no errors")
    d.bargauge("Top tools (range)", f"topk(10, sum by (server, tool) (increase(toolhive_mcp_tool_calls_total{{{SS}}}[$__range])))",
               "short", "Most called tools over the selected range.", legend="{{server}} / {{tool}}")
    d.ts("Tool duration p95 (MCP semconv)", hq("0.95", "mcp_server_operation_duration_seconds", "gen_ai_tool_name",
                                               S + ', mcp_method_name="tools/call"'), "s",
         "p95 of mcp.server.operation.duration for tools/call, by tool.", legend="{{gen_ai_tool_name}}")
    d.ts("Active connections by server", f"sum by (server, transport) ({m('toolhive_mcp_active_connections', SS)})", "short",
         "Open connections per server and transport.", legend="{{server}} {{transport}}")
    d.ts("Operations by method (MCP semconv)", f"sum by (mcp_method_name) (rate(mcp_server_operation_duration_seconds_count{{{S}}}[$__rate_interval]))",
         "reqps", "Same traffic seen through the OpenTelemetry MCP semantic conventions metric.", legend="{{mcp_method_name}}")

    d.row("Virtual MCP")
    d.stat("Backends discovered", f"max({m('toolhive_vmcp_backends_discovered', S)})", "short",
           "Backends aggregated by the Virtual MCP server.", w=6)
    d.ts("Backend requests", f"sum by (target_workload_name) ({rate('toolhive_vmcp_backend_requests_total', S)})", "reqps",
         "Requests vMCP sends to each backend MCP server.", legend="{{target_workload_name}}", w=9)
    d.ts("Backend errors", f"sum by (target_workload_name, action) ({rate('toolhive_vmcp_backend_errors_total', S)})", "reqps",
         "Failed backend calls by action (call_tool, list_capabilities...).", legend="{{target_workload_name}} {{action}}",
         no_value="no errors", w=9)
    d.ts("Backend latency p95", hq("0.95", "toolhive_vmcp_backend_requests_duration_seconds", "target_workload_name", S), "s",
         "Duration of calls from vMCP to each backend.", legend="{{target_workload_name}}")
    d.ts("Workflows", [
        (f"sum by (workflow_name) ({rate('toolhive_vmcp_workflow_executions_total', S)})", "executions {{workflow_name}}"),
        (f"sum by (workflow_name) ({rate('toolhive_vmcp_workflow_errors_total', S)})", "errors {{workflow_name}}")], "ops",
         "Composite tool workflows executed by vMCP and their errors.")

    d.row("Rate limits and auth server")
    d.ts("Rate limit decisions", f"sum by (decision, scope) ({rate('toolhive_rate_limit_decisions_total', S)})", "ops",
         "Allowed and rejected bucket decisions, shared or per user.", legend="{{decision}} {{scope}}")
    d.ts("Redis errors and fail-open", [
        (f"sum by (error_type) ({rate('toolhive_rate_limit_redis_errors_total', S)})", "redis {{error_type}}"),
        (f"sum({rate('toolhive_rate_limit_fail_open_total', S)})", "allowed by fail-open")], "ops",
         "Redis failures during rate limit checks and requests let through because of them.", no_value="no errors")
    d.ts("Rate limit check latency p95", q("0.95", "toolhive_rate_limit_check_latency_seconds", "le", S), "s",
         "Duration of the atomic Redis check.")
    d.ts("DCR clients and rejections", [
        (f"max({m('toolhive_authserver_dcr_clients', S)})", "registered clients"),
        (f"sum(increase(toolhive_authserver_dcr_capacity_rejections_total{{{S}}}[$__rate_interval]))", "rejected registrations")],
         "short", "Dynamic client registrations counted against the cap, and /oauth/register calls rejected with 503 because it was reached.")
    return d


# --------------------------------------------------------- Kuadrant MCP Gateway
def kuadrant_mcp_gateway():
    S = 'namespace=~"$namespace", pod=~"$pod"'
    SS = S + ', server_name=~"$server"'
    v = ns_pod_vars("mcp_broker_tools_discovered") + [
        query_var("server", "Upstream server", 'label_values(mcp_broker_tools_discovered{namespace=~"$namespace", pod=~"$pod"}, server_name)')]
    d = Dashboard("ai-obs-kuadrant-mcp-gateway", "Kuadrant MCP Gateway",
                  "Kuadrant MCP Gateway broker-router (/metrics on port 9090): upstream MCP servers, tools discovered, "
                  "discovery attempts and latency, connection failures and tools/list size. Tool call traffic goes "
                  "through Envoy, so request rates come from the Envoy or Istio metrics.", ["kuadrant", "mcp", "gateway"], v, LINK)
    d.row("Overview")
    d.stat("Upstream servers", f"count({m('mcp_broker_tools_discovered', SS)})", "short", "Upstream MCP servers registered in the broker.")
    d.stat("Tools exposed", f"sum({m('mcp_broker_tools_discovered', SS)})", "short", "Tools currently served by the gateway.")
    d.stat("Servers without tools", f"count({m('mcp_broker_tools_discovered', SS)} == 0) or vector(0)", "short",
           "Upstreams with 0 tools: unreachable for 3 health checks in a row, or exposing nothing.", thresholds=[(1, "orange")])
    d.stat("Discovery failure ratio", f'(sum({rate("mcp_broker_discovery_total", SS, STATUS_FAILURE)}) or vector(0)) / sum({rate("mcp_broker_discovery_total", SS)})',
           "percentunit", "Discovery attempts (connect + tools/list) that failed.", thresholds=[(0.05, "yellow"), (0.2, "red")])
    d.stat("Connection failures (range)", f"sum(increase(mcp_broker_upstream_connection_failures_total{{{SS}}}[$__range])) or vector(0)",
           "short", "Failed connections or pings to upstream servers over the selected range.", graph=False,
           thresholds=[(1, "yellow"), (10, "red")])
    d.stat("Discovery p95", q("0.95", "mcp_broker_discovery_duration_seconds", "le", SS), "s",
           "95th percentile tools/list time during discovery. The histogram uses the OpenTelemetry default buckets "
           "(0, 5, 10, 25... s), so values under 5 s are interpolated.", thresholds=[(1, "yellow"), (5, "red")])

    d.row("Upstream servers")
    d.ts("Tools per server", f"sum by (server_name) ({m('mcp_broker_tools_discovered', SS)})", "short",
         "Tools discovered per upstream. A drop to 0 means the broker removed the server's tools.", legend="{{server_name}}")
    d.ts("Discovery attempts", f"sum by (server_name, status) ({rate('mcp_broker_discovery_total', SS)})", "ops",
         "Discovery attempts per server and status. They run on every health tick (1 min by default) and on list_changed notifications.",
         legend="{{server_name}} {{status}}")
    d.ts("Discovery latency p95", hq("0.95", "mcp_broker_discovery_duration_seconds", "server_name", SS), "s",
         "tools/list duration per upstream.", legend="{{server_name}}")
    d.ts("Connection failures", f"sum by (server_name) ({rate('mcp_broker_upstream_connection_failures_total', SS)})", "ops",
         "Failed connections or pings per upstream.", legend="{{server_name}}", no_value="no failures")
    d.ts("tools/list response size", f"max by (server_name) ({m('mcp_broker_tools_list_response_bytes', SS)})", "bytes",
         "Serialized size of each upstream's validated tool list. Large lists cost context tokens on every client.",
         legend="{{server_name}}")
    d.table("Upstream servers", None, "Tools, tools/list size and connection failures over the range per upstream server.",
            exprs=[(f"sum by (server_name) ({m('mcp_broker_tools_discovered', SS)})", "Tools"),
                   (f"max by (server_name) ({m('mcp_broker_tools_list_response_bytes', SS)})", "List bytes"),
                   (f"sum by (server_name) (increase(mcp_broker_upstream_connection_failures_total{{{SS}}}[$__range]))", "Failures")],
            hide=("Time",), rename={"server_name": "Server"}, sort_by="Tools")
    return d


# ----------------------------------------------------- kubernetes-mcp-server
def kubernetes_mcp_server():
    S = 'namespace=~"$namespace", pod=~"$pod"'
    ST = S + ', tool_name=~"$tool"'
    v = ns_pod_vars("k8s_mcp_server_info") + [
        query_var("tool", "Tool", 'label_values(k8s_mcp_tool_calls_total{namespace=~"$namespace", pod=~"$pod"}, tool_name)')]
    d = Dashboard("ai-obs-kubernetes-mcp-server", "Kubernetes MCP Server",
                  "containers/kubernetes-mcp-server in HTTP mode (/metrics on the server port): tool calls, errors and "
                  "duration per tool, in-flight requests per MCP method and HTTP traffic.", ["kubernetes-mcp-server", "mcp", "kubernetes"], v, LINK)
    d.row("Overview")
    d.stat("Tool calls/s", f"sum({rate('k8s_mcp_tool_calls_total', ST)})", "reqps", "MCP tool calls per second.")
    d.stat("Tool error ratio", f"(sum({rate('k8s_mcp_tool_errors_total', ST)}) or vector(0)) / sum({rate('k8s_mcp_tool_calls_total', ST)})",
           "percentunit", "Tool calls that returned an error (RBAC denied, resource not found, API errors).",
           thresholds=[(0.05, "yellow"), (0.2, "red")])
    d.stat("Tool p95", q("0.95", "k8s_mcp_tool_duration_seconds", "le", ST), "s",
           "95th percentile tool call duration. The histogram uses the OpenTelemetry default buckets (0, 5, 10, 25... s), "
           "so values under 5 s are interpolated.",
           thresholds=[(2, "yellow"), (10, "red")])
    d.stat("In-flight tools/call", f'sum({m("k8s_mcp_requests_in_flight", S, TOOLS_CALL)})', "short",
           "tools/call requests being handled now. A good autoscaling signal.")
    d.stat("HTTP 5xx ratio", f'(sum({rate("k8s_mcp_http_requests_total", S, CLASS_5XX)}) or vector(0)) / sum({rate("k8s_mcp_http_requests_total", S)})',
           "percentunit", "HTTP responses with a 5xx status.", thresholds=[(0.01, "yellow"), (0.05, "red")])
    d.stat("Servers", f"count({m('k8s_mcp_server_info', S)})", "short", "kubernetes-mcp-server instances reporting.")

    d.row("Tools")
    d.ts("Tool calls by tool", f"sum by (tool_name) ({rate('k8s_mcp_tool_calls_total', ST)})", "reqps",
         "Calls per tool (pods_list, resources_get, events_list...).", legend="{{tool_name}}")
    d.ts("Tool errors by tool", f"sum by (tool_name) ({rate('k8s_mcp_tool_errors_total', ST)})", "reqps",
         "Failed calls per tool.", legend="{{tool_name}}", no_value="no errors")
    d.ts("Tool duration p95 by tool", hq("0.95", "k8s_mcp_tool_duration_seconds", "tool_name", ST), "s",
         "Slow tools usually list large collections or read logs.", legend="{{tool_name}}")
    d.bargauge("Top tools (range)", f"topk(10, sum by (tool_name) (increase(k8s_mcp_tool_calls_total{{{ST}}}[$__range])))", "short",
               "Most called tools over the selected range.", legend="{{tool_name}}")

    d.row("HTTP and load")
    d.ts("In-flight requests by MCP method", f"sum by (mcp_method_name) ({m('k8s_mcp_requests_in_flight', S)})", "short",
         "MCP requests being handled. subscriptions/listen long-polls are not counted.", legend="{{mcp_method_name}}")
    d.ts("HTTP requests by status class", f"sum by (http_response_status_class) ({rate('k8s_mcp_http_requests_total', S)})", "reqps",
         "HTTP requests to the MCP server by status class.", stack=True, legend="{{http_response_status_class}}")
    d.ts("HTTP requests by path", f"topk(10, sum by (url_path, http_request_method) ({rate('k8s_mcp_http_requests_total', S)}))", "reqps",
         "Top paths (/mcp, /sse, /message, /healthz...).", legend="{{http_request_method}} {{url_path}}")
    d.table("Server version", f"max by (pod, version, go_version) ({m('k8s_mcp_server_info', S)})",
            "Version reported by each instance.", hide=("Time", "Value"), rename={"pod": "Pod", "version": "Version", "go_version": "Go"})
    return d


# ------------------------------------------------------ LLM / GenAI (OpenTelemetry)
def genai_otel():
    SJ = 'job=~"$job", gen_ai_provider_name=~"$provider", gen_ai_request_model=~"$model"'
    SA = 'job=~"$job", gen_ai_agent_name=~"$agent"'
    OP, TOK = "gen_ai_client_operation_duration_seconds", "gen_ai_client_token_usage"
    v = [query_var("job", "Service (job)", 'label_values({__name__=~"gen_ai_client_operation_duration_seconds_count|gen_ai_server_request_duration_seconds_count|gen_ai_invoke_agent_duration_seconds_count"}, job)'),
         query_var("provider", "Provider", 'label_values({__name__=~"gen_ai_client_operation_duration_seconds_count|gen_ai_server_request_duration_seconds_count", job=~"$job"}, gen_ai_provider_name)'),
         query_var("model", "Model", 'label_values({__name__=~"gen_ai_client_operation_duration_seconds_count|gen_ai_server_request_duration_seconds_count", job=~"$job"}, gen_ai_request_model)'),
         query_var("agent", "Agent", 'label_values(gen_ai_invoke_agent_duration_seconds_count{job=~"$job"}, gen_ai_agent_name)')]
    d = Dashboard("ai-obs-genai-otel", "LLM / GenAI (OpenTelemetry)",
                  "Any app, agent or model server instrumented with the OpenTelemetry GenAI semantic conventions and sent to "
                  "Prometheus (OTLP receiver or exporter, default name translation): client operations, tokens, server "
                  "latency (TTFT, time per output token), agent invocations, tool executions and MCP operations. Percentiles are "
                  "computed per job, because each instrumentation picks its own histogram buckets.",
                  ["genai", "opentelemetry", "llm", "agents", "mcp"], v, LINK)
    d.row("Overview")
    d.stat("Client operations/s", f"sum(rate({OP}_count{{{SJ}}}[$__rate_interval]))", "reqps",
           "GenAI client operations (chat, embeddings, generate_content...) recorded by instrumented apps.")
    d.stat("Client error ratio", f'(sum(rate({OP}_count{{{SJ}, error_type!=""}}[$__rate_interval])) or vector(0)) / sum(rate({OP}_count{{{SJ}}}[$__rate_interval]))',
           "percentunit", "Operations recorded with error.type.", thresholds=[(0.01, "yellow"), (0.05, "red")])
    d.stat("Client p95", f"max({hq('0.95', OP, 'job', SJ)})", "s", "95th percentile client operation duration.", thresholds=[(10, "yellow"), (30, "red")])
    d.stat("Tokens/s", f"sum(rate({TOK}_sum{{{SJ}}}[$__rate_interval]))", TOKS, "Input + output tokens per second (gen_ai.client.token.usage).")
    d.stat("Server TTFT p95", f"max({hq('0.95', 'gen_ai_server_time_to_first_token_seconds', 'job', SJ)})", "s",
           "95th percentile time to first token reported by model servers.", thresholds=[(1, "yellow"), (3, "red")])
    d.stat("Agent invocations/s", f"sum(rate(gen_ai_invoke_agent_duration_seconds_count{{{SA}}}[$__rate_interval]))", "reqps",
           "Agent invocations (gen_ai.invoke_agent.duration), e.g. kagent agents.")

    d.row("Client operations")
    d.ts("Operations by model", f"sum by (gen_ai_request_model, gen_ai_operation_name) (rate({OP}_count{{{SJ}}}[$__rate_interval]))", "reqps",
         "Operations per requested model and operation.", legend="{{gen_ai_operation_name}} {{gen_ai_request_model}}")
    d.ts("Errors by type", f'sum by (gen_ai_request_model, error_type) (rate({OP}_count{{{SJ}, error_type!=""}}[$__rate_interval]))', "reqps",
         "Failed operations by model and error.type (timeout, 429, 500, exception class).", legend="{{gen_ai_request_model}} {{error_type}}",
         no_value="no errors")
    d.ts("Operation duration", pct(P3, OP, SJ, by="job", extra_legend="{{job}}"), "s", "Client-side duration including network and queueing at the provider.")
    d.ts("Operation duration p95 by model", hq("0.95", OP, "job, gen_ai_request_model", SJ), "s", "Latency per requested model.",
         legend="{{gen_ai_request_model}}")

    d.row("Tokens")
    d.ts("Tokens by type", f"sum by (gen_ai_token_type) (rate({TOK}_sum{{{SJ}}}[$__rate_interval]))", TOKS,
         "Input and output tokens per second.", legend="{{gen_ai_token_type}}", stack=True)
    d.ts("Tokens by model", f"sum by (gen_ai_request_model, gen_ai_token_type) (rate({TOK}_sum{{{SJ}}}[$__rate_interval]))", TOKS,
         "Tokens per second per model and direction.", legend="{{gen_ai_token_type}} {{gen_ai_request_model}}")
    d.ts("Tokens per operation (p95)", f"histogram_quantile(0.95, sum by (le, job, gen_ai_token_type) (rate({TOK}_bucket{{{SJ}}}[$__rate_interval])))",
         "short", "Prompt and completion size distribution.", legend="{{gen_ai_token_type}}")
    d.ts("Token counters (newer conventions)", [
        (f"sum by (gen_ai_request_model) (rate(gen_ai_client_inference_usage_input_tokens_total{{{SJ}}}[$__rate_interval]))", "input {{gen_ai_request_model}}"),
        (f"sum by (gen_ai_request_model) (rate(gen_ai_client_inference_usage_output_tokens_total{{{SJ}}}[$__rate_interval]))", "output {{gen_ai_request_model}}"),
        (f"sum by (gen_ai_request_model) (rate(gen_ai_client_inference_usage_cache_read_input_tokens_total{{{SJ}}}[$__rate_interval]))", "cache read {{gen_ai_request_model}}")],
         TOKS, "gen_ai.client.inference.usage.* counters from the semantic-conventions-genai repo (kagent harness runtimes "
         "emit them). Empty if your instrumentation still uses gen_ai.client.token.usage.")

    d.row("Model servers")
    d.ts("Server request duration", pct(P3, "gen_ai_server_request_duration_seconds", SJ, by="job", extra_legend="{{job}}"), "s",
         "Time to last byte or last output token on the model server or gateway.")
    d.ts("Server time to first token", pct(P3, "gen_ai_server_time_to_first_token_seconds", SJ, by="job", extra_legend="{{job}}"), "s", "Prefill plus queueing.")
    d.ts("Time per output token p95 by model", hq("0.95", "gen_ai_server_time_per_output_token_seconds", "job, gen_ai_request_model", SJ), "s",
         "Decode speed per model.", legend="{{gen_ai_request_model}}")
    d.ts("Server requests by model", f"sum by (gen_ai_request_model) (rate(gen_ai_server_request_duration_seconds_count{{{SJ}}}[$__rate_interval]))",
         "reqps", "Requests served per model.", legend="{{gen_ai_request_model}}")

    d.row("Agents, tools and MCP")
    d.ts("Agent invocation p95 by agent", hq("0.95", "gen_ai_invoke_agent_duration_seconds", "job, gen_ai_agent_name", SA), "s",
         "Duration of one agent invocation (turn).", legend="{{gen_ai_agent_name}}")
    d.ts("Agent invocations by agent", [
        (f"sum by (gen_ai_agent_name) (rate(gen_ai_invoke_agent_duration_seconds_count{{{SA}}}[$__rate_interval]))", "{{gen_ai_agent_name}}"),
        (f'sum by (gen_ai_agent_name) (rate(gen_ai_invoke_agent_duration_seconds_count{{{SA}, error_type!=""}}[$__rate_interval]))', "failed {{gen_ai_agent_name}}")],
         "reqps", "Invocations and failed invocations per agent.")
    d.ts("Tool execution p95 by tool", hq("0.95", "gen_ai_execute_tool_duration_seconds", "job, gen_ai_tool_name", 'job=~"$job"'), "s",
         "gen_ai.execute_tool.duration per tool.", legend="{{gen_ai_tool_name}}")
    d.ts("Model and tool calls per invocation (p95)", [
        ("histogram_quantile(0.95, sum by (le, job, gen_ai_agent_name) (rate(gen_ai_invoke_agent_inference_calls_bucket{" + SA + "}[$__rate_interval])))", "model calls {{gen_ai_agent_name}}"),
        ("histogram_quantile(0.95, sum by (le, job, gen_ai_agent_name) (rate(gen_ai_invoke_agent_tool_calls_bucket{" + SA + "}[$__rate_interval])))", "tool calls {{gen_ai_agent_name}}")],
         "short", "How many model and tool calls an agent makes per invocation. Rising numbers mean loops or harder tasks.")
    d.ts("MCP operation p95 by method", [
        (hq("0.95", "mcp_client_operation_duration_seconds", "job, mcp_method_name", 'job=~"$job"'), "client {{mcp_method_name}}"),
        (hq("0.95", "mcp_server_operation_duration_seconds", "job, mcp_method_name", 'job=~"$job"'), "server {{mcp_method_name}}")], "s",
         "mcp.client.operation.duration and mcp.server.operation.duration (OpenTelemetry MCP conventions).")
    d.ts("MCP operations by method", [
        (f'sum by (mcp_method_name) (rate(mcp_client_operation_duration_seconds_count{{job=~"$job"}}[$__rate_interval]))', "client {{mcp_method_name}}"),
        (f'sum by (mcp_method_name) (rate(mcp_server_operation_duration_seconds_count{{job=~"$job"}}[$__rate_interval]))', "server {{mcp_method_name}}")],
         "reqps", "MCP operations per method on the client and server side.")
    return d


BUILDERS = {
    "vllm": vllm, "vllm-production-stack-router": vllm_router, "sglang": sglang, "llama-cpp": llamacpp,
    "hf-tei": tei, "triton": triton, "nvidia-dcgm": dcgm, "volcano": volcano, "kserve-knative": kserve,
    "ray-serve": ray_serve, "litellm": litellm, "qdrant": qdrant, "weaviate": weaviate,
    "agentgateway": agentgateway, "envoy-ai-gateway": envoy_ai_gateway, "toolhive": toolhive,
    "kuadrant-mcp-gateway": kuadrant_mcp_gateway, "kubernetes-mcp-server": kubernetes_mcp_server,
    "genai-otel": genai_otel,
}

# Dashboards a user typically jumps to from each one (shown as links next to the pack dropdown).
RELATED = {
    "vllm": ["nvidia-dcgm", "vllm-production-stack-router", "litellm"],
    "vllm-production-stack-router": ["vllm", "litellm"],
    "sglang": ["nvidia-dcgm", "litellm"],
    "llama-cpp": ["nvidia-dcgm", "litellm"],
    "hf-tei": ["nvidia-dcgm", "qdrant", "weaviate"],
    "triton": ["nvidia-dcgm", "kserve-knative"],
    "nvidia-dcgm": ["vllm", "sglang", "triton", "volcano"],
    "volcano": ["nvidia-dcgm", "ray-serve"],
    "kserve-knative": ["nvidia-dcgm", "vllm", "triton"],
    "ray-serve": ["nvidia-dcgm", "vllm"],
    "litellm": ["vllm", "vllm-production-stack-router", "sglang", "genai-otel"],
    "qdrant": ["hf-tei", "weaviate"],
    "weaviate": ["hf-tei", "qdrant"],
    "agentgateway": ["genai-otel", "litellm", "toolhive"],
    "envoy-ai-gateway": ["genai-otel", "agentgateway", "litellm"],
    "toolhive": ["agentgateway", "kubernetes-mcp-server", "genai-otel"],
    "kuadrant-mcp-gateway": ["toolhive", "kubernetes-mcp-server"],
    "kubernetes-mcp-server": ["toolhive", "agentgateway", "kuadrant-mcp-gateway"],
    "genai-otel": ["envoy-ai-gateway", "agentgateway", "litellm", "vllm"],
}
# Latency and health stats that had no thresholds: (dashboard, stat title) -> steps
THRESHOLDS = {
    ("hf-tei", "Request p95"): [(0.5, "yellow"), (2, "red")],
    ("kserve-knative", "Predict p95"): [(0.5, "yellow"), (2, "red")],
    ("litellm", "Total latency p95"): [(10, "yellow"), (30, "red")],
    ("ray-serve", "HTTP latency p95"): [(1000, "yellow"), (5000, "red")],
    ("triton", "Avg request latency"): [(0.5, "yellow"), (2, "red")],
    ("vllm-production-stack-router", "Router latency p95"): [(10, "yellow"), (30, "red")],
}
# Stats where 0 is the bad value (red below 1)
ZERO_IS_BAD = {("vllm-production-stack-router", "Healthy backends"), ("kserve-knative", "Ready pods")}


def polish(name, dash):
    """Pack-wide consistency applied after each builder: related links, missing thresholds, empty-state text."""
    j = dash.to_json()
    j["links"] = list(j["links"])  # LINK is shared by every builder: never append to it in place
    uids = {n: BUILDERS[n]().uid for n in RELATED.get(name, [])}
    for n, uid in uids.items():
        j["links"].append({"title": TITLES[n], "type": "link", "icon": "dashboard", "url": f"/d/{uid}",
                           "keepTime": True, "includeVars": False, "targetBlank": False, "asDropdown": False,
                           "tags": [], "tooltip": "Related dashboard"})
    for p in j["panels"]:
        if p["type"] == "row":
            continue
        p["fieldConfig"]["defaults"].setdefault("noValue", "no data")
        steps = p["fieldConfig"]["defaults"].get("thresholds", {}).get("steps")
        if (name, p["title"]) in THRESHOLDS and steps is not None:
            p["fieldConfig"]["defaults"]["thresholds"]["steps"] = [{"color": "green", "value": None}] + [
                {"color": c, "value": v} for v, c in THRESHOLDS[(name, p["title"])]]
        if (name, p["title"]) in ZERO_IS_BAD and steps is not None:
            p["fieldConfig"]["defaults"]["thresholds"]["steps"] = [{"color": "red", "value": None}, {"color": "green", "value": 1}]
    return j


TITLES = {"vllm": "vLLM", "vllm-production-stack-router": "vLLM router", "sglang": "SGLang", "llama-cpp": "llama.cpp",
          "hf-tei": "TEI", "triton": "Triton", "nvidia-dcgm": "GPU (DCGM)", "volcano": "Volcano",
          "kserve-knative": "KServe / Knative", "ray-serve": "Ray Serve", "litellm": "LiteLLM", "qdrant": "Qdrant",
          "weaviate": "Weaviate", "agentgateway": "agentgateway", "envoy-ai-gateway": "Envoy AI Gateway",
          "toolhive": "ToolHive", "kuadrant-mcp-gateway": "Kuadrant MCP Gateway",
          "kubernetes-mcp-server": "Kubernetes MCP Server", "genai-otel": "GenAI (OTel)"}

if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    for name, fn in BUILDERS.items():
        with open(os.path.join(OUT, f"{name}.json"), "w") as f:
            json.dump(polish(name, fn()), f, indent=2)
            f.write("\n")
        print("wrote", name)
