"""Generate the synthetic fixtures in test/fixtures.

Only metric names, types and label names documented in each project's source
code or docs are used; values are made up. Real captures (llama.cpp, TEI,
Volcano, vLLM production-stack router) are not generated here.
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

# KServe model server (Python runtime) + Knative >= 1.19 queue-proxy/autoscaler (OTel -> Prometheus names)
f = F()
f.h("request_predict_seconds", LAT, [({"model_name": "sklearn-iris"}, 8000, 0.02), ({"model_name": "llm-chat"}, 900, 2.4)])
f.h("request_preprocess_seconds", LAT, [({"model_name": "sklearn-iris"}, 8000, 0.002), ({"model_name": "llm-chat"}, 900, 0.01)])
f.h("request_postprocess_seconds", LAT, [({"model_name": "sklearn-iris"}, 8000, 0.001), ({"model_name": "llm-chat"}, 900, 0.005)])
f.save("kserve.txt")
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
print("ok")
