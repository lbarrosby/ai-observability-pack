"""Generate the synthetic fixtures in test/fixtures.

Only metric names, types and label names documented in each project's source
code or docs are used; values are made up. Real captures (llama.cpp, TEI,
Volcano, vLLM production-stack router, KServe Python model server, Qdrant,
Weaviate) are not generated here.
"""
import math
import os

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "test", "fixtures")
os.makedirs(OUT, exist_ok=True)

LAT = [0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 40, 80]
MS = [1, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000]
TOK = [1, 8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192]


def lbl(d):
    if not d:
        return ""
    return "{" + ",".join(f'{k}="{v}"' for k, v in d.items()) + "}"


class F:
    def __init__(self):
        self.lines = []

    def g(self, name, t, series, help_="synthetic", suffix=True):
        self.lines.append(f"# HELP {name} {help_}")
        self.lines.append(f"# TYPE {name} {t}")
        for labels, v in series:
            n = name + ("_total" if suffix and t == "counter" and not name.endswith("_total") else "")
            self.lines.append(f"{n}{lbl(labels)} {v}")

    def h(self, name, buckets, series):
        """Cumulative buckets from a log-normal distribution with the given mean."""
        self.lines.append(f"# HELP {name} synthetic")
        self.lines.append(f"# TYPE {name} histogram")
        sigma = 0.8
        for labels, count, mean in series:
            mu = math.log(mean) - sigma ** 2 / 2
            for b in buckets:
                cdf = 0.5 * (1 + math.erf((math.log(b) - mu) / (sigma * math.sqrt(2))))
                self.lines.append(f'{name}_bucket{lbl({**labels, "le": str(b)})} {int(count * cdf)}')
            self.lines.append(f'{name}_bucket{lbl({**labels, "le": "+Inf"})} {count}')
            self.lines.append(f"{name}_sum{lbl(labels)} {count * mean}")
            self.lines.append(f"{name}_count{lbl(labels)} {count}")

    def save(self, fn):
        with open(os.path.join(OUT, fn.removesuffix(".txt")), "w") as f:
            f.write("\n".join(self.lines) + "\n")


# vLLM V1 engine
f = F()
f.g("vllm:num_requests_running", "gauge", [({"model_name": "meta-llama/Llama-3.1-8B-Instruct", "engine": "0"}, 12), ({"model_name": "Qwen/Qwen2.5-7B-Instruct", "engine": "0"}, 5)])
f.g("vllm:num_requests_waiting", "gauge", [({"model_name": "meta-llama/Llama-3.1-8B-Instruct", "engine": "0"}, 3), ({"model_name": "Qwen/Qwen2.5-7B-Instruct", "engine": "0"}, 0)])
f.g("vllm:kv_cache_usage_perc", "gauge", [({"model_name": "meta-llama/Llama-3.1-8B-Instruct", "engine": "0"}, 0.72), ({"model_name": "Qwen/Qwen2.5-7B-Instruct", "engine": "0"}, 0.31)])
M = [({"model_name": "meta-llama/Llama-3.1-8B-Instruct", "engine": "0"}, 1.0), ({"model_name": "Qwen/Qwen2.5-7B-Instruct", "engine": "0"}, 0.4)]
f.g("vllm:prompt_tokens", "counter", [(l, int(900000 * k)) for l, k in M])
f.g("vllm:generation_tokens", "counter", [(l, int(400000 * k)) for l, k in M])
f.g("vllm:prefix_cache_queries", "counter", [(l, int(900000 * k)) for l, k in M])
f.g("vllm:prefix_cache_hits", "counter", [(l, int(300000 * k)) for l, k in M])
f.g("vllm:num_preemptions", "counter", [(l, int(12 * k)) for l, k in M])
f.g("vllm:request_success", "counter", [({**l, "finished_reason": r}, int(n * k)) for l, k in M for r, n in [("stop", 2000), ("length", 300), ("abort", 15)]])
f.h("vllm:time_to_first_token_seconds", LAT, [(l, int(2300 * k), 0.18) for l, k in M])
f.h("vllm:inter_token_latency_seconds", LAT, [(l, int(400000 * k), 0.025) for l, k in M])
f.h("vllm:e2e_request_latency_seconds", LAT, [(l, int(2300 * k), 4.2) for l, k in M])
f.h("vllm:request_queue_time_seconds", LAT, [(l, int(2300 * k), 0.05) for l, k in M])
f.h("vllm:request_prefill_time_seconds", LAT, [(l, int(2300 * k), 0.12) for l, k in M])
f.h("vllm:request_decode_time_seconds", LAT, [(l, int(2300 * k), 4.0) for l, k in M])
f.h("vllm:request_prompt_tokens", TOK, [(l, int(2300 * k), 390) for l, k in M])
f.h("vllm:request_generation_tokens", TOK, [(l, int(2300 * k), 170) for l, k in M])
f.save("vllm.txt")

# SGLang
f = F()
S = [({"model_name": "meta-llama/Llama-3.1-8B-Instruct"}, 1.0)]
f.g("sglang:num_running_reqs", "gauge", [(l, 9) for l, _ in S])
f.g("sglang:num_queue_reqs", "gauge", [(l, 2) for l, _ in S])
f.g("sglang:num_used_tokens", "gauge", [(l, 51234) for l, _ in S])
f.g("sglang:token_usage", "gauge", [(l, 0.41) for l, _ in S])
f.g("sglang:cache_hit_rate", "gauge", [(l, 0.37) for l, _ in S])
f.g("sglang:gen_throughput", "gauge", [(l, 1840.5) for l, _ in S])
f.g("sglang:prompt_tokens", "counter", [(l, 800000) for l, _ in S])
f.g("sglang:generation_tokens", "counter", [(l, 350000) for l, _ in S])
f.g("sglang:cached_tokens", "counter", [(l, 290000) for l, _ in S])
f.g("sglang:num_requests", "counter", [(l, 2100) for l, _ in S])
f.g("sglang:num_aborted_requests", "counter", [(l, 7) for l, _ in S])
f.h("sglang:time_to_first_token_seconds", LAT, [(l, 2100, 0.2) for l, _ in S])
f.h("sglang:inter_token_latency_seconds", LAT, [(l, 350000, 0.02) for l, _ in S])
f.h("sglang:e2e_request_latency_seconds", LAT, [({**l, "is_streaming": "true"}, 2100, 3.4) for l, _ in S])
f.h("sglang:queue_time_seconds", LAT, [(l, 2100, 0.04) for l, _ in S])
f.save("sglang.txt")

# DCGM exporter (k8s mapping enabled)
f = F()
G = []
for i, (pod, ns) in enumerate([("vllm-llama-0", "llm"), ("vllm-llama-0", "llm"), ("sglang-0", "llm"), ("", "")]):
    G.append({"gpu": str(i), "UUID": f"GPU-0000000{i}-aaaa-bbbb-cccc-00000000000{i}", "pci_bus_id": f"00000000:0{i}:00.0", "device": f"nvidia{i}", "modelName": "NVIDIA H100 80GB HBM3", "Hostname": "gpu-node-1", "DCGM_FI_DRIVER_VERSION": "570.124.06", "container": "server" if pod else "", "namespace": ns, "pod": pod})
vals = [(0.92, 71, 640, 1830, 72000, 0), (0.88, 69, 610, 1830, 70000, 0), (0.55, 61, 420, 1980, 45000, 0), (0.0, 34, 70, 345, 0, 0)]
f.g("DCGM_FI_DEV_GPU_UTIL", "gauge", [(g, int(v[0] * 100)) for g, v in zip(G, vals)])
f.g("DCGM_FI_DEV_MEM_COPY_UTIL", "gauge", [(g, int(v[0] * 60)) for g, v in zip(G, vals)])
f.g("DCGM_FI_DEV_GPU_TEMP", "gauge", [(g, v[1]) for g, v in zip(G, vals)])
f.g("DCGM_FI_DEV_MEMORY_TEMP", "gauge", [(g, v[1] + 6) for g, v in zip(G, vals)])
f.g("DCGM_FI_DEV_POWER_USAGE", "gauge", [(g, v[2]) for g, v in zip(G, vals)])
f.g("DCGM_FI_DEV_TOTAL_ENERGY_CONSUMPTION", "counter", [(g, v[2] * 1000 * 3600 * 24) for g, v in zip(G, vals)], suffix=False)
f.g("DCGM_FI_DEV_SM_CLOCK", "gauge", [(g, v[3]) for g, v in zip(G, vals)])
f.g("DCGM_FI_DEV_MEM_CLOCK", "gauge", [(g, 2619) for g in G])
f.g("DCGM_FI_DEV_FB_USED", "gauge", [(g, v[4]) for g, v in zip(G, vals)])
f.g("DCGM_FI_DEV_FB_FREE", "gauge", [(g, 81559 - v[4]) for g, v in zip(G, vals)])
f.g("DCGM_FI_DEV_FB_RESERVED", "gauge", [(g, 0) for g in G])
f.g("DCGM_FI_DEV_XID_ERRORS", "gauge", [(g, 13 if g["gpu"] == "3" else 0) for g in G])
f.g("DCGM_FI_DEV_PCIE_REPLAY_COUNTER", "counter", [(g, 0) for g in G], suffix=False)
f.g("DCGM_FI_DEV_UNCORRECTABLE_REMAPPED_ROWS", "counter", [(g, 0) for g in G], suffix=False)
f.g("DCGM_FI_DEV_CORRECTABLE_REMAPPED_ROWS", "counter", [(g, 1) for g in G], suffix=False)
f.g("DCGM_FI_DEV_ROW_REMAP_FAILURE", "gauge", [(g, 0) for g in G])
f.g("DCGM_FI_PROF_GR_ENGINE_ACTIVE", "gauge", [(g, v[0] * 0.95) for g, v in zip(G, vals)])
f.g("DCGM_FI_PROF_PIPE_TENSOR_ACTIVE", "gauge", [(g, v[0] * 0.4) for g, v in zip(G, vals)])
f.g("DCGM_FI_PROF_DRAM_ACTIVE", "gauge", [(g, v[0] * 0.7) for g, v in zip(G, vals)])
f.g("DCGM_FI_PROF_PCIE_TX_BYTES", "gauge", [(g, int(v[0] * 3e9)) for g, v in zip(G, vals)])
f.g("DCGM_FI_PROF_PCIE_RX_BYTES", "gauge", [(g, int(v[0] * 5e9)) for g, v in zip(G, vals)])
f.g("DCGM_FI_DEV_NVLINK_BANDWIDTH_TOTAL", "gauge", [(g, int(v[0] * 90000)) for g, v in zip(G, vals)])
f.g("DCGM_FI_DEV_POWER_VIOLATION", "counter", [(g, 0) for g in G], suffix=False)
f.g("DCGM_FI_DEV_THERMAL_VIOLATION", "counter", [(g, 0) for g in G], suffix=False)
f.save("dcgm.txt")

# Triton
f = F()
T = [({"model": "resnet50", "version": "1"}, 1.0), ({"model": "bert-base", "version": "2"}, 0.5)]
f.g("nv_inference_request_success", "counter", [(l, int(50000 * k)) for l, k in T], suffix=False)
f.g("nv_inference_request_failure", "counter", [({**l, "reason": r}, n) for l, k in T for r, n in [("REJECTED", 3), ("BACKEND", 1), ("CANCELED", 0), ("OTHER", 0)]], suffix=False)
f.g("nv_inference_count", "counter", [(l, int(400000 * k)) for l, k in T], suffix=False)
f.g("nv_inference_exec_count", "counter", [(l, int(60000 * k)) for l, k in T], suffix=False)
f.g("nv_inference_pending_request_count", "gauge", [(l, int(4 * k)) for l, k in T])
for n, us in [("request", 12000), ("queue", 900), ("compute_input", 300), ("compute_infer", 9500), ("compute_output", 200)]:
    f.g(f"nv_inference_{n}_duration_us", "counter", [(l, int(50000 * k * us)) for l, k in T], suffix=False)
f.h("nv_inference_request_histogram_us", [100, 500, 1000, 5000, 10000, 50000, 100000, 500000], [(l, int(50000 * k), 12000) for l, k in T])
f.h("nv_inference_queue_histogram_us", [100, 500, 1000, 5000, 10000, 50000, 100000, 500000], [(l, int(50000 * k), 900) for l, k in T])
f.h("nv_inference_first_response_histogram_ms", [100, 500, 2000, 5000], [(l, int(500 * k), 280) for l, k in T])
GT = [{"gpu_uuid": "GPU-00000000-aaaa-bbbb-cccc-000000000000"}]
f.g("nv_gpu_utilization", "gauge", [(g, 0.74) for g in GT])
f.g("nv_gpu_memory_used_bytes", "gauge", [(g, 21474836480) for g in GT])
f.g("nv_gpu_memory_total_bytes", "gauge", [(g, 85899345920) for g in GT])
f.g("nv_gpu_power_usage", "gauge", [(g, 310.5) for g in GT])
f.g("nv_gpu_power_limit", "gauge", [(g, 700) for g in GT])
f.g("nv_cpu_utilization", "gauge", [({}, 0.22)])
f.g("nv_cpu_memory_used_bytes", "gauge", [({}, 8589934592)])
f.g("nv_cpu_memory_total_bytes", "gauge", [({}, 68719476736)])
f.save("triton.txt")

# LiteLLM proxy
f = F()
base = {"end_user": "", "hashed_api_key": "88dc28d0f030c55ed4ab77ed8faf098196cb1c05df778539800c9f1243fe6b4b", "api_key_alias": "svc-chatbot", "team": "t1", "team_alias": "platform", "user": "", "user_email": ""}
R = [("gpt-4o", "openai", 1.0), ("claude-sonnet", "anthropic", 0.6), ("llama-3.1-8b", "hosted_vllm", 2.0)]
f.g("litellm_proxy_total_requests_metric", "counter", [({**base, "requested_model": m, "model_id": m + "-id", "api_provider": p, "route": "/chat/completions", "status_code": sc, "client_ip": "", "user_agent": ""}, int(n * k)) for m, p, k in R for sc, n in [("200", 3000), ("429", 40), ("500", 6)]])
f.g("litellm_proxy_failed_requests_metric", "counter", [({**base, "requested_model": m, "exception_status": s, "exception_class": c}, int(n * k)) for m, p, k in R for s, c, n in [("429", "RateLimitError", 40), ("500", "InternalServerError", 6)]])
for name, n in [("litellm_input_tokens_metric", 2400000), ("litellm_output_tokens_metric", 900000)]:
    f.g(name, "counter", [({**base, "requested_model": m, "model_id": m + "-id", "api_provider": p}, int(n * k)) for m, p, k in R])
f.g("litellm_spend_metric", "counter", [({**base, "requested_model": m, "model_id": m + "-id", "api_provider": p, "client_ip": "", "user_agent": ""}, round(c * k, 4)) for (m, p, k), c in zip(R, [41.2, 33.7, 0.0])])
for name, mean in [("litellm_request_total_latency_metric", 3.1), ("litellm_llm_api_latency_metric", 2.9), ("litellm_llm_api_time_to_first_token_metric", 0.6)]:
    f.h(name, LAT, [({**base, "requested_model": m, "model_group": m, "model_id": m + "-id", "api_provider": p}, int(3000 * k), mean) for m, p, k in R])
f.g("litellm_deployment_state", "gauge", [({"litellm_model_name": m, "model_id": m + "-id", "api_base": "https://api.example", "api_provider": p, "model_group": m}, 1 if m == "claude-sonnet" else 0) for m, p, k in R])
f.g("litellm_remaining_requests_metric", "gauge", [({"model_group": m, "api_provider": p, "api_base": "https://api.example", "litellm_model_name": m, "model_id": m + "-id"}, 4200) for m, p, k in R])
f.g("litellm_remaining_tokens_metric", "gauge", [({"model_group": m, "api_provider": p, "api_base": "https://api.example", "litellm_model_name": m, "model_id": m + "-id"}, 750000) for m, p, k in R])
f.g("litellm_deployment_cooled_down", "counter", [({"litellm_model_name": m, "model_id": m + "-id", "api_base": "https://api.example", "api_provider": p, "exception_status": "429"}, 2) for m, p, k in R])
f.g("litellm_remaining_team_budget_metric", "gauge", [({"team": "t1", "team_alias": "platform"}, 412.5)])
f.g("litellm_remaining_api_key_budget_metric", "gauge", [({"hashed_api_key": base["hashed_api_key"], "api_key_alias": "svc-chatbot"}, 88.2)])
MT = [("github", "create_issue", 120), ("github", "search_code", 340), ("kubernetes", "pods_list", 510), ("jira", "get_issue", 80)]
mlb = {"hashed_api_key": base["hashed_api_key"], "api_key_alias": "svc-chatbot", "team": "t1", "team_alias": "platform", "user": "", "end_user": ""}
f.g("litellm_mcp_tool_calls_total", "counter", [({**mlb, "mcp_tool_name": t, "mcp_server_name": srv}, n) for srv, t, n in MT])
f.g("litellm_mcp_tool_call_spend_metric", "counter", [({**mlb, "mcp_tool_name": t, "mcp_server_name": srv}, round(n * 0.002, 4)) for srv, t, n in MT if srv == "github"])
f.save("litellm.txt")

# Ray Serve
f = F()
A = [("llm-app", "LLMServer", 1.0), ("embed-app", "Embedder", 0.5)]
f.g("ray_serve_num_http_requests", "counter", [({"route": "/" + a, "method": "POST", "application": a, "status_code": sc}, int(n * k)) for a, d, k in A for sc, n in [("200", 9000), ("500", 12), ("503", 5)]])
f.g("ray_serve_num_http_error_requests", "counter", [({"route": "/" + a, "method": "POST", "application": a, "error_code": ec}, int(n * k)) for a, d, k in A for ec, n in [("500", 12), ("503", 5)]])
f.h("ray_serve_http_request_latency_ms", MS, [({"route": "/" + a, "method": "POST", "application": a, "status_code": "200"}, int(9000 * k), 850) for a, d, k in A])
f.g("ray_serve_num_ongoing_http_requests", "gauge", [({"node_id": "abc", "node_ip_address": "10.0.0.5"}, 14)])
f.g("ray_serve_deployment_queued_queries", "gauge", [({"deployment": d, "application": a, "handle": "h1", "actor_id": "x"}, int(3 * k)) for a, d, k in A])
f.g("ray_serve_num_ongoing_requests_at_replicas", "gauge", [({"deployment": d, "application": a, "handle": "h1", "actor_id": "x"}, int(8 * k)) for a, d, k in A])
f.g("ray_serve_replica_processing_queries", "gauge", [({"deployment": d, "replica": f"{d}#r{i}", "application": a}, 4) for a, d, k in A for i in range(2)])
f.g("ray_serve_replica_utilization_percent", "gauge", [({"deployment": d, "replica": f"{d}#r{i}", "application": a}, 63.0) for a, d, k in A for i in range(2)])
f.g("ray_serve_deployment_request_counter", "counter", [({"deployment": d, "replica": f"{d}#r{i}", "route": "/" + a, "application": a}, int(4500 * k)) for a, d, k in A for i in range(2)])
f.g("ray_serve_deployment_error_counter", "counter", [({"deployment": d, "replica": f"{d}#r0", "route": "/" + a, "application": a, "exception_type": "RuntimeError"}, 3) for a, d, k in A])
f.h("ray_serve_deployment_processing_latency_ms", MS, [({"deployment": d, "replica": f"{d}#r{i}", "route": "/" + a, "application": a}, int(4500 * k), 700) for a, d, k in A for i in range(2)])
f.g("ray_serve_deployment_replica_healthy", "gauge", [({"deployment": d, "application": a, "replica": f"{d}#r{i}"}, 1) for a, d, k in A for i in range(2)])
f.g("ray_serve_autoscaling_target_replicas", "gauge", [({"deployment": d, "application": a}, 2) for a, d, k in A])
f.g("ray_serve_autoscaling_desired_replicas", "gauge", [({"deployment": d, "application": a}, 2) for a, d, k in A])
f.g("ray_serve_deployment_status", "gauge", [({"deployment": d, "application": a}, 6) for a, d, k in A])
f.save("ray_serve.txt")

# Knative >= 1.19 queue-proxy/autoscaler (OTel -> Prometheus names)
f = F()
K = [("sklearn-iris-predictor-00001", "sklearn-iris-predictor"), ("llm-chat-predictor-00003", "llm-chat-predictor")]
f.g("kn_serving_queue_depth", "gauge", [({}, 1)])
f.h("kn_serving_invocation_duration_seconds", LAT, [({"http_response_status_code": c}, n, mean) for c, n, mean in [("200", 8000, 0.05), ("503", 4, 0.01)]])
f.save("knative_queue.txt")
f = F()
for n, v in [("kn_revision_pods_desired", 2), ("kn_revision_pods_requested", 2), ("kn_revision_pods_count", 2), ("kn_revision_pods_not_ready_count", 0), ("kn_revision_pods_pending_count", 0), ("kn_revision_concurrency_stable", 3.2), ("kn_revision_concurrency_panic", 3.5), ("kn_revision_concurrency_target", 10), ("kn_revision_capacity_excess", 12), ("kn_revision_panic_mode", 0)]:
    f.g(n, "gauge", [({"k8s_namespace_name": "models", "kn_revision_name": r, "kn_service_name": s, "kn_configuration_name": s}, v) for r, s in K])
f.g("kn_revision_request_concurrency", "gauge", [({"k8s_namespace_name": "models", "kn_revision_name": r, "kn_service_name": s, "kn_configuration_name": s}, 0) for r, s in K])
f.save("knative_autoscaler.txt")

# OpenTelemetry Go SDK + Prometheus exporter: every series carries the instrumentation scope.
def scope(name, version=""):
    return {"otel_scope_name": name, "otel_scope_schema_url": "", "otel_scope_version": version}


GENAI_LAT = [0.01, 0.02, 0.04, 0.08, 0.16, 0.32, 0.64, 1.28, 2.56, 5.12, 10.24, 20.48, 40.96, 81.92]
GENAI_TOK = [1, 4, 16, 64, 256, 1024, 4096, 16384, 65536, 262144, 1048576]
TTFT_B = [0.001, 0.005, 0.01, 0.02, 0.04, 0.06, 0.08, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 7.5, 10.0, 15.0, 20.0, 30.0, 45.0, 60.0]
TPOT_B = [0.01, 0.025, 0.05, 0.075, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.75, 1.0, 2.5]
MCP_B = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10]
OTEL_DEFAULT_B = [5, 10, 25, 50, 75, 100, 250, 500, 750, 1000, 2500, 5000, 7500, 10000]  # SDK default, "0" bucket left out (always 0 here)
SEMCONV_MCP_B = [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 30, 60, 120, 300]

# agentgateway (prometheus-client, prefix agentgateway_)
f = F()
RT = {"bind": "8080", "gateway": "agentgateway-system/agentgateway", "listener": "http", "route": "default/mcp", "route_rule": "unknown"}
f.g("agentgateway_build_info", "gauge", [({"tag": "v1.2.0"}, 1)])
f.g("agentgateway_config_synchronized", "gauge", [({}, 1)])
REQS = [("mcp-tools", "mcp", "POST", "200", "Upstream", 9000), ("mcp-tools", "mcp", "POST", "401", "JwtAuth", 40),
        ("openai", "llm", "POST", "200", "Upstream", 4200), ("openai", "llm", "POST", "429", "RateLimit", 25),
        ("openai", "llm", "POST", "503", "NoHealthyBackend", 6), ("billing-agent", "a2a", "POST", "200", "Upstream", 700),
        ("billing-agent", "a2a", "POST", "500", "Upstream", 4)]
HL = lambda b, p, mt, st, r: {"backend": b, "protocol": p, "method": mt, "status": st, "reason": r, **RT}
f.g("agentgateway_requests", "counter", [(HL(b, p, mt, st, r), n) for b, p, mt, st, r, n in REQS])
f.h("agentgateway_request_duration_seconds", LAT, [(HL(b, p, mt, st, r), n, 0.4 if p == "mcp" else 3.5) for b, p, mt, st, r, n in REQS])
for name, mean in [("agentgateway_request_processing_seconds", 0.0008), ("agentgateway_response_processing_seconds", 0.0004)]:
    f.h(name, [0.0001, 0.0005, 0.001, 0.005, 0.01, 0.05, 0.1], [({"backend": b, **RT}, 9000, mean) for b in ("mcp-tools", "openai")])
f.g("agentgateway_requests_active", "gauge", [({"backend": "mcp-tools"}, 6), ({"backend": "openai"}, 11), ({"backend": "billing-agent"}, 2)])
f.g("agentgateway_upstream_connections_active", "gauge", [({"backend": "mcp-tools"}, 4), ({"backend": "openai"}, 8), ({"backend": "billing-agent"}, 1)])
f.g("agentgateway_retries", "counter", [(HL("openai", "llm", "POST", "200", "Upstream"), 30)])
f.g("agentgateway_requests_shed", "counter", [({"bind": "8080"}, 3)])
f.g("agentgateway_downstream_connections_shed", "counter", [({"bind": "8080"}, 1)])
TCP = {"bind": "8080", "gateway": RT["gateway"], "listener": "http"}
f.g("agentgateway_downstream_connections", "counter", [({**TCP, "protocol": "HTTP"}, 3000), ({**TCP, "protocol": "HTTPS"}, 1200)])
f.g("agentgateway_downstream_received_bytes", "counter", [({**TCP, "protocol": "HTTP"}, 90000000)])
f.g("agentgateway_downstream_sent_bytes", "counter", [({**TCP, "protocol": "HTTP"}, 420000000)])
f.h("agentgateway_upstream_connect_duration_seconds", [0.0005, 0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1], [({"transport": "plaintext"}, 2000, 0.004), ({"transport": "tls"}, 900, 0.03)])
f.h("agentgateway_tls_handshake_duration_seconds", [0.0005, 0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1], [({**TCP, "protocol": "HTTPS"}, 1200, 0.008)])
MCPS = [("initialize", "success", "unknown", "github", "unknown", 300), ("tools/list", "success", "unknown", "github", "unknown", 600),
        ("tools/call", "success", "tool", "github", "create_issue", 1400), ("tools/call", "tool_error", "tool", "github", "create_issue", 30),
        ("tools/call", "success", "tool", "kubernetes", "pods_list", 2600), ("tools/call", "gateway_error", "tool", "kubernetes", "pods_log", 12),
        ("tools/call", "success", "tool", "fetch", "fetch", 900), ("resources/read", "success", "resource", "kubernetes", "k8s://events", 200),
        ("prompts/get", "success", "prompt", "github", "review_pr", 60), ("tools/call", "protocol_error", "tool", "fetch", "fetch", 8)]
f.g("agentgateway_mcp_requests", "counter", [({"method": mt, "outcome": o, "resource_type": rt, "server": srv, "resource": res, **RT}, n) for mt, o, rt, srv, res, n in MCPS])
GL = lambda model: {"gen_ai_operation_name": "chat", "gen_ai_system": "openai", "gen_ai_request_model": model, "gen_ai_response_model": model + "-2024-08-06", **{**RT, "route": "default/llm"}}
GM = [("gpt-4o", 1.0), ("gpt-4o-mini", 2.5)]
f.h("agentgateway_gen_ai_client_token_usage", GENAI_TOK, [({"gen_ai_token_type": t, **GL(mo)}, int(2000 * k), mean) for mo, k in GM for t, mean in [("input", 900), ("output", 260), ("input_cache_read", 300)]])
f.g("agentgateway_gen_ai_client_cost_usd", "counter", [(GL(mo), c) for mo, c in [("gpt-4o", 18.4), ("gpt-4o-mini", 2.1)]])
f.h("agentgateway_gen_ai_server_request_duration", GENAI_LAT, [(GL(mo), int(2000 * k), 3.2) for mo, k in GM] + [({**GL(mo), "error_type": "_OTHER"}, int(25 * k), 0.4) for mo, k in GM])
f.h("agentgateway_gen_ai_server_time_to_first_token", GENAI_LAT, [(GL(mo), int(1500 * k), 0.45) for mo, k in GM])
f.h("agentgateway_gen_ai_server_time_per_output_token", GENAI_LAT, [(GL(mo), int(1500 * k), 0.02) for mo, k in GM])
f.h("agentgateway_gen_ai_server_inter_chunk_latency", GENAI_LAT, [(GL(mo), int(1500 * k), 0.03) for mo, k in GM])
f.g("agentgateway_guardrail_checks", "counter", [({"phase": ph, "action": a}, n) for ph, a, n in [("Request", "Allow", 4000), ("Request", "Reject", 35), ("Response", "Allow", 3900), ("Response", "Mask", 60)]])
f.g("agentgateway_cost_catalog_lookups", "counter", [({"status": st, **GL(mo)}, n) for mo, _ in GM for st, n in [("found", 2000)]])
f.save("agentgateway")

# Envoy AI Gateway / Agent Router external processor (OTel -> Prometheus)
f = F()
SE = scope("envoyproxy/ai-gateway")
EG = [("gpt-4o-mini", "openai", "default/openai", 1.0), ("claude-sonnet-4", "aws.bedrock", "default/bedrock", 0.5), ("llama-3.1-8b", "openai", "default/vllm", 1.6)]
EL = lambda mo, pr, be, op="chat": {"gen_ai_backend": be, "gen_ai_operation_name": op, "gen_ai_original_model": mo, "gen_ai_provider_name": pr, "gen_ai_request_model": mo, "gen_ai_response_model": mo, **SE}
f.h("gen_ai_server_request_duration_seconds", GENAI_LAT, [(EL(mo, pr, be), int(3000 * k), 2.8) for mo, pr, be, k in EG] + [({**EL(mo, pr, be), "error_type": "_OTHER"}, int(20 * k), 0.3) for mo, pr, be, k in EG] + [(EL("text-embedding-3-small", "openai", "default/openai", "embeddings"), 1200, 0.15)])
f.h("gen_ai_server_time_to_first_token_seconds", TTFT_B, [(EL(mo, pr, be), int(2500 * k), 0.35) for mo, pr, be, k in EG])
f.h("gen_ai_server_time_per_output_token_seconds", TPOT_B, [(EL(mo, pr, be), int(2500 * k), 0.022) for mo, pr, be, k in EG])
f.h("gen_ai_client_token_usage", GENAI_TOK, [({**EL(mo, pr, be), "gen_ai_token_type": t}, int(3000 * k), mean) for mo, pr, be, k in EG for t, mean in [("input", 850), ("output", 240), ("cached_input", 310)]])
MM = [("initialize", "success", 400), ("tools/list", "success", 900), ("tools/call", "success", 5200), ("tools/call", "error", 40), ("tools/call", "failed", 15), ("resources/read", "success", 300), ("notifications/initialized", "success", 400)]
f.g("mcp_method_count", "counter", [({"mcp_backend": be, "mcp_method_name": mt, "status": st, **SE}, int(n * k)) for be, k in [("github", 0.6), ("kubernetes", 0.4)] for mt, st, n in MM])
f.h("mcp_request_duration", MCP_B, [({"mcp_backend": be, **SE}, int(7000 * k), 0.12) for be, k in [("github", 0.6), ("kubernetes", 0.4)]] + [({"mcp_backend": "github", "error_type": e, **SE}, n, 0.02) for e, n in [("invalid_session_id", 12), ("unsupported_method", 4)]])
f.g("mcp_capabilities_negotiated", "counter", [({"capability_side": side, "capability_type": c, **SE}, 400) for side, c in [("server", "tools"), ("server", "resources"), ("server", "prompts"), ("client", "sampling"), ("client", "roots")]])
f.g("mcp_progress_notifications", "counter", [(SE, 90)])
f.save("envoy_ai_gateway")

# ToolHive MCP proxy, vMCP, rate limits, auth server (OTel -> Prometheus)
f = F()
ST_ = scope("github.com/stacklok/toolhive")
TH = [("github", "streamable-http"), ("fetch", "stdio")]
REQ_T = []
for srv, tr in TH:
    for mm, rid, sc, n in [("initialize", "", "200", 200), ("tools/list", "", "200", 500), ("tools/call", "create_issue" if srv == "github" else "fetch", "200", 3000),
                           ("tools/call", "create_issue" if srv == "github" else "fetch", "500", 25), ("resources/read", "repo://readme", "200", 150)]:
        REQ_T.append(({"mcp_method": mm, "mcp_resource_id": rid, "method": "POST", "server": srv, "status": "error" if int(sc) >= 400 else "success", "status_code": sc, "transport": tr, **ST_}, n if srv == "github" else n // 2))
f.g("toolhive_mcp_requests", "counter", REQ_T)
f.h("toolhive_mcp_request_duration_seconds", SEMCONV_MCP_B, [(l, n, 0.35 if l["mcp_method"] == "tools/call" else 0.04) for l, n in REQ_T])
f.g("toolhive_mcp_tool_calls", "counter", [({"server": srv, "status": st, "tool": t, **ST_}, n) for srv, t in [("github", "create_issue"), ("github", "search_code"), ("fetch", "fetch")] for st, n in [("success", 1500), ("error", 12)]])
f.g("toolhive_mcp_active_connections", "gauge", [({"connection_type": "sse", "server": "github", "transport": "streamable-http", **ST_}, 7), ({"connection_type": "sse", "server": "fetch", "transport": "stdio", **ST_}, 2)])
f.h("mcp_server_operation_duration_seconds", SEMCONV_MCP_B, [({"gen_ai_operation_name": "execute_tool", "gen_ai_tool_name": t, "jsonrpc_protocol_version": "2.0", "mcp_method_name": "tools/call", "network_protocol_name": "http", "network_transport": "tcp", **ST_}, 1500, mean) for t, mean in [("create_issue", 0.6), ("search_code", 1.1), ("fetch", 0.3)]]
    + [({"jsonrpc_protocol_version": "2.0", "mcp_method_name": mm, "network_protocol_name": "http", "network_transport": "tcp", **ST_}, n, 0.03) for mm, n in [("initialize", 300), ("tools/list", 750)]])
f.g("toolhive_vmcp_backends_discovered", "gauge", [(ST_, 3)])
VB = [("github", 1.0), ("fetch", 0.6), ("osv", 0.3)]
VL = lambda w, a: {"action": a, "mcp_method_name": "tools/call" if a == "call_tool" else "tools/list", "mcp_protocol_revision": "2025-06-18", "target_base_url": f"http://{w}.toolhive:8080", "target_transport_type": "streamable-http", "target_workload_id": w, "target_workload_name": w, **ST_}
f.g("toolhive_vmcp_backend_requests", "counter", [(VL(w, a), int(n * k)) for w, k in VB for a, n in [("call_tool", 2000), ("list_capabilities", 300)]])
f.g("toolhive_vmcp_backend_errors", "counter", [(VL(w, "call_tool"), int(15 * k)) for w, k in VB])
f.h("toolhive_vmcp_backend_requests_duration_seconds", SEMCONV_MCP_B, [(VL(w, "call_tool"), int(2000 * k), 0.4 + k / 2) for w, k in VB])
f.h("mcp_client_operation_duration_seconds", SEMCONV_MCP_B, [({"mcp_method_name": mm, "network_transport": "tcp", **ST_}, n, mean) for mm, n, mean in [("tools/call", 3800, 0.6), ("tools/list", 570, 0.05)]])
f.g("toolhive_vmcp_workflow_executions", "counter", [({"workflow_name": w, **ST_}, n) for w, n in [("triage-incident", 120), ("release-notes", 40)]])
f.g("toolhive_vmcp_workflow_errors", "counter", [({"workflow_name": "triage-incident", **ST_}, 3)])
f.h("toolhive_vmcp_workflow_duration_seconds", SEMCONV_MCP_B, [({"workflow_name": w, **ST_}, n, 8) for w, n in [("triage-incident", 120), ("release-notes", 40)]])
f.g("toolhive_rate_limit_decisions", "counter", [({"decision": d_, "namespace": "toolhive", "operation_type": ot, "scope": sc, "server": "github", **ST_}, n) for d_, sc, ot, n in [("allowed", "shared", "server", 4000), ("allowed", "per_user", "tool", 3500), ("rejected", "per_user", "tool", 45)]])
f.g("toolhive_rate_limit_redis_errors", "counter", [({"error_type": "timeout", "namespace": "toolhive", "server": "github", **ST_}, 2)])
f.g("toolhive_rate_limit_fail_open", "counter", [({"namespace": "toolhive", "server": "github", **ST_}, 2)])
f.h("toolhive_rate_limit_check_latency_seconds", [0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1], [({"namespace": "toolhive", "server": "github", **ST_}, 7500, 0.0018)])
f.g("toolhive_authserver_dcr_clients", "gauge", [(ST_, 1840)])
f.g("toolhive_authserver_dcr_capacity_rejections", "counter", [(ST_, 0)])
f.save("toolhive")

# Kuadrant MCP Gateway broker (OTel -> Prometheus)
f = F()
SK_ = scope("mcp-broker")
KU = [("mcp-system/github", 41, 52000, 1.0), ("mcp-system/kubernetes", 23, 31000, 1.0), ("mcp-system/weather", 0, 0, 0.4)]
f.g("mcp_broker_discovery", "counter", [({"server_name": s_, "status": st, **SK_}, int(n * k)) for s_, t, b, k in KU for st, n in [("success", 600 if t else 50), ("failure", 2 if t else 300)]])
f.h("mcp_broker_discovery_duration_seconds", OTEL_DEFAULT_B, [({"server_name": s_, **SK_}, 600, 0.08 if t else 2.0) for s_, t, b, k in KU])
f.g("mcp_broker_tools_discovered", "gauge", [({"server_name": s_, **SK_}, t) for s_, t, b, k in KU])
f.g("mcp_broker_tools_list_response_bytes", "gauge", [({"server_name": s_, **SK_}, b) for s_, t, b, k in KU])
f.g("mcp_broker_upstream_connection_failures", "counter", [({"server_name": s_, **SK_}, 2 if t else 300) for s_, t, b, k in KU])
f.g("target_info", "gauge", [({"service_name": "mcp-broker-router", "service_version": "v0.6.0"}, 1)])
f.save("kuadrant_mcp_gateway")

# containers/kubernetes-mcp-server (OTel -> Prometheus)
f = F()
SKM = scope("k8s-mcp-server")
KT = [("pods_list", 2200, 0.25), ("pods_log", 900, 0.8), ("resources_get", 1300, 0.12), ("events_list", 700, 0.3), ("helm_list", 120, 1.4)]
f.g("k8s_mcp_tool_calls", "counter", [({"tool_name": t, **SKM}, n) for t, n, _ in KT])
f.g("k8s_mcp_tool_errors", "counter", [({"tool_name": t, **SKM}, max(1, n // 60)) for t, n, _ in KT])
f.h("k8s_mcp_tool_duration_seconds", OTEL_DEFAULT_B, [({"tool_name": t, **SKM}, n, mean) for t, n, mean in KT])
f.g("k8s_mcp_http_requests", "counter", [({"http_request_method": mt, "http_response_status_class": sc, "url_path": pth, **SKM}, n) for mt, pth, sc, n in [("POST", "/mcp", "2xx", 6000), ("POST", "/mcp", "4xx", 30), ("POST", "/mcp", "5xx", 4), ("GET", "/healthz", "2xx", 2000), ("GET", "/metrics", "2xx", 700)]])
f.g("k8s_mcp_requests_in_flight", "gauge", [({"mcp_method_name": mm, **SKM}, v_) for mm, v_ in [("tools/call", 3), ("tools/list", 0), ("initialize", 0)]])
f.g("k8s_mcp_server_info", "gauge", [({"go_version": "go1.25.4", "version": "v0.0.60", **SKM}, 1)])
f.save("kubernetes_mcp_server")

# GenAI semantic conventions received over OTLP (names as Prometheus translates them)
f = F()
GO = [("chat", "openai", "gpt-4o-mini", 1.0), ("chat", "anthropic", "claude-sonnet-4", 0.5), ("embeddings", "openai", "text-embedding-3-small", 0.8)]
GOL = lambda op, pr, mo: {"gen_ai_operation_name": op, "gen_ai_provider_name": pr, "gen_ai_request_model": mo, "gen_ai_response_model": mo, "server_address": "api." + pr.split(".")[0] + ".com", "server_port": "443"}
f.h("gen_ai_client_operation_duration_seconds", GENAI_LAT, [(GOL(op, pr, mo), int(2500 * k), 0.3 if op == "embeddings" else 3.4) for op, pr, mo, k in GO] + [({**GOL(op, pr, mo), "error_type": "RateLimitError"}, int(18 * k), 0.2) for op, pr, mo, k in GO])
f.h("gen_ai_client_token_usage", GENAI_TOK, [({**GOL(op, pr, mo), "gen_ai_token_type": t}, int(2500 * k), mean) for op, pr, mo, k in GO for t, mean in ([("input", 700), ("output", 220)] if op == "chat" else [("input", 400)])])
f.h("gen_ai_server_request_duration_seconds", GENAI_LAT, [(GOL("chat", "openai", "meta-llama/Llama-3.1-8B-Instruct"), 4000, 2.2)])
f.h("gen_ai_server_time_to_first_token_seconds", GENAI_LAT, [(GOL("chat", "openai", "meta-llama/Llama-3.1-8B-Instruct"), 4000, 0.21)])
f.h("gen_ai_server_time_per_output_token_seconds", GENAI_LAT, [(GOL("chat", "openai", "meta-llama/Llama-3.1-8B-Instruct"), 4000, 0.018)])
AG = [("k8s-agent", 1.0), ("helm-agent", 0.4)]
AGL = lambda a: {"gen_ai_agent_name": a, "gen_ai_operation_name": "invoke_agent", "gen_ai_provider_name": "anthropic", "gen_ai_request_model": "claude-sonnet-4"}
f.h("gen_ai_invoke_agent_duration_seconds", GENAI_LAT, [(AGL(a), int(400 * k), 18) for a, k in AG] + [({**AGL(a), "error_type": "_OTHER"}, int(6 * k), 4) for a, k in AG])
f.h("gen_ai_invoke_agent_inference_calls", [1, 2, 4, 8, 16, 32, 64, 128], [(AGL(a), int(400 * k), 5) for a, k in AG])
f.h("gen_ai_invoke_agent_tool_calls", [1, 2, 4, 8, 16, 32, 64, 128], [(AGL(a), int(400 * k), 7) for a, k in AG])
f.h("gen_ai_execute_tool_duration_seconds", GENAI_LAT, [({"gen_ai_operation_name": "execute_tool", "gen_ai_tool_name": t}, n, mean) for t, n, mean in [("k8s_get_resources", 1800, 0.4), ("k8s_get_pod_logs", 700, 1.1), ("helm_list_releases", 300, 0.9)]])
for name, n in [("gen_ai_client_inference_usage_input_tokens", 4200000), ("gen_ai_client_inference_usage_output_tokens", 610000), ("gen_ai_client_inference_usage_cache_read_input_tokens", 2900000)]:
    f.g(name, "counter", [({"gen_ai_operation_name": "chat", "gen_ai_provider_name": "anthropic", "gen_ai_request_model": "claude-sonnet-4", "gen_ai_token_modality": "text"}, n)])
f.h("mcp_client_operation_duration_seconds", SEMCONV_MCP_B, [({"mcp_method_name": mm, "network_transport": "tcp"}, n, mean) for mm, n, mean in [("tools/call", 2800, 0.5), ("tools/list", 400, 0.04)]])
f.save("genai_otel")
print("ok")
