# Runbook

Short, actionable notes for the alerts in [`ai-inference-rules.yaml`](ai-inference-rules.yaml).
Thresholds are starting points: tune them to your SLOs and hardware.

## VLLMRequestsQueueing

Also used by `SGLangRequestsQueueing`.

1. Open the vLLM (or SGLang) dashboard and compare *Running* with *Waiting*.
2. If KV cache usage is near 100%, the engine is memory bound: go to the next section.
3. If KV cache has room but running requests sit at `--max-num-seqs`, raise `--max-num-seqs` (vLLM) or `--max-running-requests` (SGLang).
4. If both are maxed, scale out (more replicas behind the router) or route part of the traffic to a smaller model.

## VLLMKVCacheNearlyFull

Also used by `VLLMPreemptions` and `SGLangTokenPoolNearlyFull`.

1. Check *Prompt length* and *Generation length*: a shift to longer prompts fills the cache faster.
2. Options, cheapest first: enable prefix caching, cap `max_tokens` on the client or gateway, lower `--max-model-len`, raise `--gpu-memory-utilization` (vLLM) or `--mem-fraction-static` (SGLang), use a quantized KV cache (`--kv-cache-dtype fp8`), add replicas or tensor parallelism.
3. Sustained preemptions waste GPU time: treat them as a capacity alert, not noise.

## VLLMHighTimeToFirstToken

Also used by `VLLMHighAbortRatio` and `SGLangHighTimeToFirstToken`.

1. In *Where the time goes*, see whether queue, prefill or decode dominates.
2. Queue dominates: the engine is saturated, see `VLLMRequestsQueueing`.
3. Prefill dominates: long prompts. Enable chunked prefill, prefix caching, or move long-context traffic to a dedicated pool.
4. A high abort ratio with high TTFT means clients time out while waiting: raise client timeouts only after fixing capacity.

## VLLMRouterNoHealthyBackends

Also used by `VLLMRouterHighErrorRatio`.

1. `kubectl get pods` for the engine deployment: look for CrashLoopBackOff, OOMKilled or pending pods without GPU.
2. Check that the router service discovery (label selector / static backends) still matches the engine pods.
3. `ClientConnectorError` in the router errors panel means the router cannot reach the backend port.

## LlamaCppRequestsDeferred

1. Deferred requests wait for a free slot. Raise `--parallel` (each slot gets `ctx-size / parallel` context) or add replicas.
2. Check *Busy slots per decode*: if it is far below `--parallel`, batching is limited by context size, not slots.

## TEIQueueBacklog

Also used by `TEIHighFailureRatio`.

1. Compare queue time with inference time in *Latency breakdown*.
2. Raise `--max-batch-tokens` / `--max-concurrent-requests` if the GPU has headroom, otherwise scale out.
3. `err="tokenization"` or `"validation"` failures are client inputs (too long, empty); `"overloaded"` means the queue is full.

## TritonHighFailureRatio

Also used by `TritonPendingRequests`.

1. Group failures by `reason`. `REJECTED` = queue timeout in the scheduler (raise `max_queue_delay` / instance count), `BACKEND` = model or backend error (check Triton logs), `CANCELED` = client cancellations.
2. For pending requests, add `instance_group` count, enable dynamic batching, or scale replicas.

## GPUXidError

Also used by `GPUFatalXidError`, `GPURowRemapFailure`, `GPUUncorrectableRemappedRowsIncreasing` and `GPUPCIeReplays`.

1. Look the code up in the NVIDIA XID catalog (<https://docs.nvidia.com/deploy/xid-errors/>).
2. 13, 31, 43: usually the application (bad kernel, illegal memory access). Check the pod that owns the GPU.
3. 48, 92, 94, 95, row remap failure: memory errors. Cordon and drain the node, reset the GPU (`nvidia-smi -r`) or reboot, and RMA if it repeats.
4. 79 (fell off the bus), 74 (NVLink): hardware or firmware. Drain the node and escalate to the provider.
5. PCIe replays: reseat or replace the riser/cable, check BIOS PCIe settings.

## GPUThrottling

Covers `GPUHighTemperature`, `GPUThermalThrottling` and `GPUPowerThrottling`.

1. Confirm with *SM clock*: a clock drop together with high temperature or power is throttling.
2. Thermal: check node airflow, fan status and data center inlet temperature.
3. Power: some capping at full load is expected; persistent capping means the power limit is low (`nvidia-smi -q -d POWER`).

## GPUIdleWhileAllocated

1. The pod owns GPUs but barely uses them. Typical causes: a model server with no traffic, a stuck job, or a notebook left running.
2. Scale the deployment down, enable scale-to-zero (KServe/Knative, KEDA), or move the workload to a shared/time-sliced GPU.

## VolcanoUnschedulableJobs

Also used by `VolcanoSchedulingSlow` and `VolcanoJobsFailing`.

1. `kubectl describe podgroup -n <ns> <name>` shows why the gang cannot be placed.
2. Compare queue *allocated* vs *deserved* vs *capacity* in the Volcano dashboard: the queue may be out of quota even if the cluster has free GPUs.
3. Slow scheduling: the *Plugin latency* panel shows which plugin dominates the cycle.

## KnativeRevisionCannotScale

Also used by `KnativeQueueProxy5xx`.

1. `kubectl get pods -n <ns> -l serving.knative.dev/revision=<revision>`: pending pods usually mean no GPU node is free; check the cluster autoscaler.
2. Not-ready pods: model download or storage initializer is slow; check the `storage-initializer` container logs.
3. 5xx at the queue-proxy with healthy pods: check the model server logs and `containerConcurrency`.

## RayServeDeploymentUnhealthy

Also used by `RayServeHighErrorRatio` and `RayServeBackpressure`.

1. `serve status` (or the Ray dashboard) shows the failing replica and its traceback.
2. Backpressure: raise `max_replicas` or `max_ongoing_requests`, and check that the Ray cluster can add GPU nodes.

## LiteLLMDeploymentOutage

Also used by `LiteLLMHighFailureRatio`, `LiteLLMDeploymentDegraded` and `LiteLLMTeamBudgetLow`.

1. Check the provider status page and the `exception_class` breakdown (RateLimitError, AuthenticationError, Timeout).
2. Make sure fallbacks are configured for the model group so traffic moves to a healthy deployment.
3. Budget alerts: raise the team budget or tell the team before requests start failing.

## QdrantDeadReplicas

Also used by `QdrantHighErrorRatio`.

1. `GET /cluster` and `GET /collections/<name>/cluster` on any node show which shard replica is `Dead` and on which peer.
2. A dead replica usually follows a node crash or disk problem. Once the node is back, Qdrant recovers the replica from a healthy peer (shard transfer); watch *Shard transfers* on the dashboard.
3. If the peer is gone for good, remove it from the cluster and re-replicate the shard (`replicate_shard` cluster operation) to keep the replication factor.
4. 5xx without dead replicas: check pod memory (OOM during optimization) and disk space for segments and snapshots.

## WeaviateHighErrorRatio

Also used by `WeaviateSlowQueries` and `WeaviateShardsNotReady`.

1. On the dashboard, find the route with 5xx (*HTTP requests by route and status*) and the collection with slow queries.
2. Shards stuck in LOADING after a restart: large collections load lazily; give the pod time and memory, or check the logs for corrupted LSM segments.
3. Slow queries: a growing *Tombstones* line means deletes are waiting for cleanup; heavy batch imports compete with searches; the vector cache may be smaller than the index (`vectorCacheMaxObjects`).
4. Memory pressure: HNSW lives in memory. Compare *Process memory* with the pod limit.

## AgentgatewayHighErrorRatio

Also used by `AgentgatewayMCPErrors` and `AgentgatewayConfigNotSynchronized`.

1. On the agentgateway dashboard, *Gateway-generated responses* tells whether the 5xx come from the gateway (`NoHealthyBackend`, `Timeout`, `Internal`) or from the backend (`Upstream`).
2. MCP errors: `gateway_error` means the call never reached a healthy MCP target (auth, routing, backend down); `protocol_error` is a JSON-RPC error from the target; `tool_error` is the tool returning `isError`, usually bad arguments from the agent.
3. Config not synchronized: the gateway rejected the last config (file or xDS). Read the gateway logs for the validation error; the previous config keeps serving traffic.

## EnvoyAIGatewayHighErrorRatio

Also used by `EnvoyAIGatewayMCPErrors`.

1. *Errors by model and type* on the Envoy AI Gateway dashboard shows which `AIServiceBackend` and model fail.
2. Check the upstream provider status, the `BackendSecurityPolicy` credentials (expired API key or cloud role) and the provider rate limits.
3. MCP errors: `invalid_session_id` after a gateway restart means clients must re-initialize; `unsupported_method` means a client uses a method the proxy does not route.

## ToolHiveMCPHighErrorRatio

Also used by `ToolHiveVMCPBackendErrors`.

1. `thv logs <server>` or the MCPServer pod logs show why the MCP server answers with errors (missing secrets, upstream API failures).
2. For stdio servers, a crash of the container shows as 5xx from the proxy; check restarts.
3. vMCP backend errors: confirm the backend workload is running and that the vMCP outgoing auth to it is valid.

## ToolHiveRateLimitFailOpen

Also used by `ToolHiveDCRCapacityReached`.

1. Fail-open means Redis did not answer the rate limit check, so requests went through unlimited. Check Redis availability, auth and latency (*Redis errors* by `error_type`).
2. DCR cap reached: raise `storage.maxDCRClients` or let expired clients age out. Compare *DCR clients* with the configured cap.

## MCPGatewayUpstreamWithoutTools

Also used by `MCPGatewayUpstreamConnectionFailures`.

1. The broker drops an upstream's tools after 3 consecutive failed health checks. Check the upstream MCP server pod and its `MCPServerRegistration` status.
2. Connection failures with a reachable pod usually mean wrong URL/path, TLS or credentials for the upstream.
3. Once the upstream answers again, discovery restores its tools on the next health tick.

## KubernetesMCPServerToolErrors

1. Check the server logs for `forbidden`: the ServiceAccount (or kubeconfig) lacks RBAC for the resource the agent asked for. Grant read access deliberately, or keep the server in `--read-only` and accept the errors.
2. `not found` errors come from agents guessing names; they are harmless but point to prompts that need more context.

## GenAIClientHighErrorRatio

Also used by `GenAIAgentInvocationsFailing`.

1. *Errors by type* on the GenAI dashboard: 429/RateLimitError means quota, timeouts mean the provider or the network, 401/403 means credentials.
2. Agent invocations failing: look at tool execution latency and errors, the model provider errors for the agent's model, and the agent logs for the failed turn.
