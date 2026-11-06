#!/usr/bin/env python3
"""Wrap alerts/ai-inference-rules.yaml into a prometheus-operator PrometheusRule."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "alerts", "ai-inference-rules.yaml")
DST = os.path.join(HERE, "..", "alerts", "ai-inference-prometheusrule.yaml")

HEADER = """apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: ai-inference
  labels:
    app.kubernetes.io/name: ai-observability-pack
    app.kubernetes.io/part-of: ai-observability-pack
spec:
"""

with open(SRC) as f:
    body = "".join(("  " + line) if line.strip() else line for line in f)
with open(DST, "w") as f:
    f.write(HEADER + body)
print("wrote", DST)
