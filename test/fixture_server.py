import math, os, re, sys, time, zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = sys.argv[1]
T0 = time.time()
LINE = re.compile(r'^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{.*\})?\s+(\S+)(\s+\d+)?$')


def load(path):
    types, out = {}, []
    for ln in open(path):
        ln = ln.rstrip("\n")
        if ln.startswith("# TYPE"):
            _, _, n, t = ln.split(None, 3)
            types[n] = t
        out.append(ln)
    return types, out


def base_type(name, types):
    if name in types:
        return types[name]
    for suf in ("_bucket", "_sum", "_count", "_total", "_created"):
        if name.endswith(suf) and name[: -len(suf)] in types:
            return types[name[: -len(suf)]]
    return "untyped"


def render(path):
    t = time.time() - T0
    grow = 1 + t / 300 + (10 / 300) * math.sin(t / 30)
    types, lines = load(path)
    res = []
    for ln in lines:
        m = LINE.match(ln)
        if not m or ln.startswith("#"):
            res.append(ln)
            continue
        name, labels, val = m.group(1), m.group(2) or "", m.group(3)
        try:
            v = float(val)
        except ValueError:
            res.append(ln)
            continue
        bt = base_type(name, types)
        if name.endswith("_created"):
            nv = v
        elif bt in ("counter", "histogram", "summary") and not name.endswith("_total") or bt == "counter":
            nv = v * grow
        elif bt == "gauge" and v > 0 and "XID" not in name:
            ph = zlib.crc32((name + labels).encode()) % 100
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


ThreadingHTTPServer(("0.0.0.0", int(sys.argv[2])), H).serve_forever()
