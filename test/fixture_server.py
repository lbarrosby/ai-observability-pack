"""Serve /metrics fixtures so Prometheus sees live-looking series.

  python3 test/fixture_server.py test/fixtures 18999

GET /<file>/metrics returns test/fixtures/<file> with:
  * counters, histogram and summary _sum/_count growing at value/300 per second
    (so rate() returns the fixture value / 300), with a slow wave on top;
  * histogram buckets split between the captured distribution and a slower one, with a
    share that changes over time, so percentiles move (bucket counts stay cumulative);
  * gauges oscillating +-15% around their value (0 and 1 are kept as is);
  * values written as now-60 / now+86400 resolved to a Unix timestamp at scrape time;
  * a "# FIXTURE counter <name>" line treats that family as a counter even if the
    exporter declares it a gauge (Weaviate's requests_total only goes up); "# FIXTURE static
    <name>" keeps a family at its captured value (e.g. etcd leader changes).

The clock starts at the first run and is kept in <fixtures>/../.fixture_clock, so a
restart does not reset counters (which would show as a spike in rate()).
"""
import math
import os
import re
import sys
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = sys.argv[1]
CLOCK = os.path.join(os.path.dirname(os.path.abspath(ROOT)), ".fixture_clock")
try:
    T0 = float(open(CLOCK).read())
except (OSError, ValueError):
    T0 = time.time()
    with open(CLOCK, "w") as f:
        f.write(str(T0))
LINE = re.compile(r'^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{.*\})?\s+(\S+)(\s+\d+)?$')
LE = re.compile(r'(?<=[{,])le="([^"]+)",?')


def strip_le(labels):
    out = LE.sub("", labels).replace(",}", "}")
    return "" if out == "{}" else out
SUFFIXES = ("_bucket", "_sum", "_count", "_total", "_created")
# gauges that describe state or configuration (inventory, prices, timestamps, capacity) stay constant
STATIC = re.compile(r"^(kube_|node_.*_hourly_cost|node_gpu_count|pv_hourly_cost|kubecost_|x509_|rke2_certificate|"
                    r"etcd_server_quota|kubelet_volume_stats_capacity|kubelet_running_|collections_|collection_points|collection_vectors|vector_index_size|weaviate_schema_|weaviate_shards|.*_info$)|timestamp|not_after|not_before|expir|_time_seconds$")


def load(path):
    types, out = {}, []
    for ln in open(path):
        ln = ln.rstrip("\n")
        if ln.startswith("# TYPE"):
            _, _, n, t = ln.split(None, 3)
            types.setdefault(n, t)
        elif ln.startswith("# FIXTURE counter "):
            types[ln.split()[3]] = "counter"
        elif ln.startswith("# FIXTURE static "):
            types[ln.split()[3]] = "static"
        out.append(ln)
    return types, out


def base_type(name, types):
    if name in types:
        return types[name]
    for suf in SUFFIXES:
        if name.endswith(suf) and name[: -len(suf)] in types:
            return types[name[: -len(suf)]]
    return "untyped"


def grow(t, ph=0.0):
    """Non-decreasing in t: 1 + (t + 40*0.8*sin(t/40 + ph))/300, derivative >= 0.2/300."""
    return 1 + (t + 32 * (math.sin(t / 40 + ph) - math.sin(ph))) / 300


def slow_share(t, key):
    """Share of the shifted (slower) distribution, cumulative and non-decreasing."""
    ph = (zlib.crc32(key.encode()) % 628) / 100
    return (0.25 * t + 60 * (math.sin(t / 90 + ph) - math.sin(ph)) * 0.8 * 0.25 * 1.5) / 300


def render(path):
    t = time.time() - T0
    now = time.time()
    types, lines = load(path)
    parsed, buckets = [], {}
    for ln in lines:
        m = LINE.match(ln)
        if not m or ln.startswith("#"):
            parsed.append((ln, None))
            continue
        name, labels, val = m.group(1), m.group(2) or "", m.group(3)
        if val.startswith("now"):
            parsed.append((f"{name}{labels} {now + float(val[3:] or 0):.0f}", None))
            continue
        try:
            v = float(val)
        except ValueError:
            parsed.append((ln, None))
            continue
        parsed.append(((name, labels, v), None))
        if name.endswith("_bucket"):
            le = LE.search(labels)
            key = name[:-7] + strip_le(labels)
            buckets.setdefault(key, []).append((float(le.group(1)), v))
    # previous bucket value of each histogram series, used for the slower distribution
    shifted = {}
    for key, bs in buckets.items():
        bs.sort()
        prev = 0.0
        for le, v in bs:
            shifted[(key, le)] = prev if le != math.inf else v
            prev = v
    res = []
    for item, _ in parsed:
        if isinstance(item, str):
            res.append(item)
            continue
        name, labels, v = item
        bt = base_type(name, types)
        # phase from the labels only: counters of the same series set (hits/queries, success/total) move together,
        # so ratios between families stay below 1
        ph = (zlib.crc32(labels.encode()) % 100) / 16
        if name.endswith("_created") or bt == "static":
            nv = v
        elif bt == "histogram" and name.endswith("_bucket"):
            le = LE.search(labels)
            key = name[:-7] + strip_le(labels)
            s = slow_share(t, key)
            nv = v * grow(t) + shifted[(key, float(le.group(1)))] * s
        elif bt == "histogram" and name.endswith("_sum"):
            nv = v * grow(t) + v * 1.6 * slow_share(t, name[:-4] + labels)
        elif bt == "histogram" and name.endswith("_count"):
            nv = v * grow(t) + v * slow_share(t, name[:-6] + labels)
        elif bt == "summary" and (name.endswith("_sum") or name.endswith("_count")):
            nv = v * grow(t)
        elif bt == "counter" or (bt == "untyped" and name.endswith("_total")):
            nv = v * grow(t, ph)
        elif bt in ("gauge", "summary", "untyped") and v not in (0.0, 1.0) and "XID" not in name \
                and v < 1e9 and not STATIC.search(name):
            nv = v * (0.85 + 0.15 * math.sin(t / 20 + ph))
        else:
            nv = v
        res.append(f"{name}{labels} {nv:.6g}")
    return "\n".join(res) + "\n"


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        parts = self.path.strip("/").split("/")
        fn = os.path.join(ROOT, parts[0])
        if len(parts) != 2 or parts[1] != "metrics" or not os.path.isfile(fn):
            self.send_response(404)
            self.end_headers()
            return
        body = render(fn).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


ThreadingHTTPServer((sys.argv[3] if len(sys.argv) > 3 else "127.0.0.1", int(sys.argv[2])), H).serve_forever()
