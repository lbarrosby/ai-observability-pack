#!/usr/bin/env python3
"""Lint dashboards offline and, optionally, run every query against a Prometheus.

  python3 scripts/check_dashboards.py                       # lint only
  python3 scripts/check_dashboards.py --prometheus http://localhost:9090

Lint rules: valid JSON, unique uid and panel ids, every panel has a description,
a unit and the ${DS_PROMETHEUS} datasource, no overlapping panels, the first row is
an "Overview" row of at least three stat panels, panel links point to a dashboard
of the pack.
Query check: variables are replaced by their "All" value, $__rate_interval by 1m,
$__range by 15m; a query fails on a Prometheus error and warns on an empty result.
"""
import argparse
import glob
import json
import os
import re
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DASH_DIR = os.path.join(HERE, "..", "dashboards")


def panels(d):
    for p in d["panels"]:
        yield p
        for sub in p.get("panels", []):
            yield sub


def lint(path, d, uids, all_uids):
    errs = []
    top = [p for p in d["panels"] if p["gridPos"]["y"] <= 5]
    if not d["panels"] or d["panels"][0].get("type") != "row" or d["panels"][0]["title"] != "Overview":
        errs.append("first row is not 'Overview'")
    if sum(1 for p in top if p["type"] == "stat") < 3:
        errs.append("fewer than 3 stat panels in the Overview row")
    for ln in d.get("links", []):
        m = re.match(r"^/d/([\w-]+)", ln.get("url", ""))
        if m and m.group(1) not in all_uids:
            errs.append(f"dashboard link '{ln['title']}' points to unknown dashboard {m.group(1)}")
    if d["uid"] in uids:
        errs.append(f"duplicate uid {d['uid']}")
    uids.add(d["uid"])
    ids, cells = set(), {}
    for p in panels(d):
        if p["id"] in ids:
            errs.append(f"duplicate panel id {p['id']}")
        ids.add(p["id"])
        g = p["gridPos"]
        for x in range(g["x"], g["x"] + g["w"]):
            for y in range(g["y"], g["y"] + g["h"]):
                if (x, y) in cells:
                    errs.append(f"panel '{p['title']}' overlaps '{cells[(x, y)]}'")
                    break
                cells[(x, y)] = p["title"]
            else:
                continue
            break
        if p["type"] == "row":
            continue
        for ln in p.get("links", []):
            m = re.match(r"^/d/([\w-]+)", ln.get("url", ""))
            if m and m.group(1) not in all_uids:
                errs.append(f"panel '{p['title']}' links to unknown dashboard {m.group(1)}")
        if not p.get("description"):
            errs.append(f"panel '{p['title']}' has no description")
        if p.get("datasource", {}).get("uid") != "${DS_PROMETHEUS}":
            errs.append(f"panel '{p['title']}' does not use ${{DS_PROMETHEUS}}")
        if p["type"] != "table" and not p["fieldConfig"]["defaults"].get("unit"):
            errs.append(f"panel '{p['title']}' has no unit")
        for t in p.get("targets", []):
            if t.get("datasource", {}).get("uid") != "${DS_PROMETHEUS}":
                errs.append(f"target in '{p['title']}' does not use ${{DS_PROMETHEUS}}")
    return errs


def substitute(expr, variables):
    for name, value in sorted(variables.items(), key=lambda kv: -len(kv[0])):
        expr = expr.replace("${" + name + "}", value).replace("$" + name, value)
    return expr.replace("$__rate_interval", "1m").replace("$__range", "15m").replace("$__interval", "1m")


def run_query(base, expr):
    url = base.rstrip("/") + "/api/v1/query?" + urllib.parse.urlencode({"query": expr})
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            body = json.load(r)
    except urllib.error.HTTPError as e:
        body = json.load(e)
    if body.get("status") != "success":
        return "error", body.get("error", "unknown error")
    res = body["data"]["result"]
    if isinstance(res, list):
        # NaN (e.g. histogram_quantile over zero observations) renders as an empty panel too
        res = [r for r in res if r.get("value", [0, "NaN"])[1] not in ("NaN", "+Inf", "-Inf")]
    return ("ok" if res else "empty"), len(res)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prometheus")
    ap.add_argument("files", nargs="*")
    a = ap.parse_args()
    files = a.files or sorted(glob.glob(os.path.join(DASH_DIR, "*.json")))
    failed, uids, total, empty = False, set(), 0, []
    all_uids = set()
    for path in sorted(glob.glob(os.path.join(DASH_DIR, "*.json"))):
        with open(path) as f:
            all_uids.add(json.load(f)["uid"])
    for path in files:
        with open(path) as f:
            d = json.load(f)
        errs = lint(path, d, uids, all_uids)
        for e in errs:
            print(f"LINT {os.path.basename(path)}: {e}")
        failed |= bool(errs)
        if not a.prometheus:
            continue
        variables = {v["name"]: v.get("allValue", ".*") for v in d["templating"]["list"] if v["type"] == "query"}
        variables.update({v["name"]: v["current"]["value"] for v in d["templating"]["list"] if v["type"] in ("custom", "textbox")})
        queries = [(p["title"] + (f" [{t.get('legendFormat') or t['refId']}]" if len(p["targets"]) > 1 else ""), t["expr"])
                   for p in panels(d) for t in p.get("targets", [])]
        queries += [(f"variable {v['name']}", re.sub(r"^label_values\((.*),\s*[\w]+\)$", r"\1", v["definition"]))
                    for v in d["templating"]["list"] if v["type"] == "query"]
        for title, expr in queries:
            total += 1
            status, info = run_query(a.prometheus, substitute(expr, variables))
            if status == "error":
                failed = True
                print(f"ERROR {os.path.basename(path)} / {title}: {info}\n      {expr}")
            elif status == "empty":
                empty.append(f"{os.path.basename(path)} / {title}")
    if a.prometheus:
        print(f"{total} queries checked, {len(empty)} returned no data")
        for e in empty:
            print(f"  empty: {e}")
    print("FAILED" if failed else "OK")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
