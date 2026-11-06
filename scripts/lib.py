"""Tiny helper to build Grafana dashboards as JSON with a consistent style.

No third-party dependencies. Every panel gets a datasource bound to the
DS_PROMETHEUS dashboard variable, a unit and a description.
"""
import re

DS = {"type": "prometheus", "uid": "${DS_PROMETHEUS}"}
SCHEMA_VERSION = 41


def _thresholds(steps):
    out = [{"color": "green", "value": None}]
    for value, color in steps or []:
        out.append({"color": color, "value": value})
    return {"mode": "absolute", "steps": out}


def target(expr, legend="__auto", ref="A", instant=False):
    t = {"datasource": DS, "editorMode": "code", "expr": expr, "legendFormat": legend, "refId": ref}
    if instant:
        t.update({"instant": True, "range": False, "format": "table"})
    else:
        t["range"] = True
    return t


class Dashboard:
    def __init__(self, uid, title, description, tags, variables, links=None):
        self.uid = uid
        self.title = title
        self.description = description
        self.tags = ["ai-observability"] + tags
        self.variables = variables
        self.links = links or []
        self.panels = []
        self._id = 0
        self._x = 0
        self._y = 0
        self._row_h = 0
        self._types = set()

    def _place(self, w, h):
        if self._x + w > 24:
            self._x = 0
            self._y += self._row_h
            self._row_h = 0
        pos = {"x": self._x, "y": self._y, "w": w, "h": h}
        self._x += w
        self._row_h = max(self._row_h, h)
        return pos

    def _next_id(self):
        self._id += 1
        return self._id

    def row(self, title):
        if self._x:
            self._y += self._row_h
        self._x, self._row_h = 0, 0
        self.panels.append({"type": "row", "title": title, "id": self._next_id(), "collapsed": False,
                            "gridPos": {"x": 0, "y": self._y, "w": 24, "h": 1}, "panels": []})
        self._y += 1

    def _targets(self, exprs):
        if isinstance(exprs, str):
            exprs = [(exprs, "__auto")]
        refs = "ABCDEFGHIJ"
        out = []
        for i, (e, l) in enumerate(exprs):
            if l == "__auto":
                m = re.match(r"^\w+ by \(([\w, ]+)\)", e)
                if m and "," in m.group(1):
                    l = " ".join("{{" + x.strip() + "}}" for x in m.group(1).split(","))
            out.append(target(e, l, refs[i]))
        return out

    def stat(self, title, expr, unit, description, w=4, h=4, thresholds=None, decimals=None,
             mappings=None, color_mode="value", graph=True, min_=None, max_=None):
        self._types.add("stat")
        defaults = {"unit": unit, "thresholds": _thresholds(thresholds), "color": {"mode": "thresholds"},
                    "mappings": mappings or []}
        if decimals is None and unit == "short":
            decimals = 0
        if decimals is not None:
            defaults["decimals"] = decimals
        if min_ is not None:
            defaults["min"] = min_
        if max_ is not None:
            defaults["max"] = max_
        self.panels.append({
            "type": "stat", "title": title, "description": description, "id": self._next_id(),
            "datasource": DS, "gridPos": self._place(w, h),
            "fieldConfig": {"defaults": defaults, "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "colorMode": color_mode, "graphMode": "area" if graph else "none",
                        "justifyMode": "auto", "textMode": "value", "orientation": "auto",
                        "showPercentChange": False, "wideLayout": True},
            "targets": self._targets(expr),
        })

    def ts(self, title, exprs, unit, description, w=12, h=8, stack=False, min_=None, max_=None,
           thresholds=None, legend_calcs=("mean", "max", "lastNotNull"), fill=10, draw="line",
           overrides=None, decimals=None, show_thresholds=False, legend="__auto"):
        self._types.add("timeseries")
        custom = {"drawStyle": draw, "lineWidth": 1, "fillOpacity": fill, "gradientMode": "none",
                  "showPoints": "never", "pointSize": 4, "spanNulls": False, "lineInterpolation": "linear",
                  "axisPlacement": "auto", "axisSoftMin": 0 if min_ is None else None,
                  "stacking": {"mode": "normal" if stack else "none", "group": "A"},
                  "thresholdsStyle": {"mode": "dashed" if show_thresholds else "off"}}
        custom = {k: v for k, v in custom.items() if v is not None}
        defaults = {"unit": unit, "color": {"mode": "palette-classic"}, "custom": custom,
                    "thresholds": _thresholds(thresholds), "mappings": []}
        if min_ is not None:
            defaults["min"] = min_
        if max_ is not None:
            defaults["max"] = max_
        if decimals is not None:
            defaults["decimals"] = decimals
        self.panels.append({
            "type": "timeseries", "title": title, "description": description, "id": self._next_id(),
            "datasource": DS, "gridPos": self._place(w, h),
            "fieldConfig": {"defaults": defaults, "overrides": overrides or []},
            "options": {"legend": {"displayMode": "table" if legend_calcs else "list", "placement": "bottom",
                                   "showLegend": True, "calcs": list(legend_calcs or [])},
                        "tooltip": {"mode": "multi", "sort": "desc"}},
            "targets": self._targets(exprs if not isinstance(exprs, str) else [(exprs, legend)]),
        })

    def bargauge(self, title, expr, unit, description, w=12, h=8, thresholds=None, legend="__auto",
                 min_=None, max_=None):
        self._types.add("bargauge")
        defaults = {"unit": unit, "thresholds": _thresholds(thresholds), "color": {"mode": "thresholds"},
                    "mappings": []}
        if min_ is not None:
            defaults["min"] = min_
        if max_ is not None:
            defaults["max"] = max_
        self.panels.append({
            "type": "bargauge", "title": title, "description": description, "id": self._next_id(),
            "datasource": DS, "gridPos": self._place(w, h),
            "fieldConfig": {"defaults": defaults, "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "orientation": "horizontal", "displayMode": "gradient", "showUnfilled": True,
                        "valueMode": "color", "namePlacement": "auto", "sizing": "auto", "minVizHeight": 16,
                        "maxVizHeight": 300, "minVizWidth": 8},
            "targets": [target(expr, legend)],
        })

    def table(self, title, expr, description, w=12, h=8, value_unit="none", mappings=None,
              hide=("Time",), rename=None, thresholds=None, value_color=False):
        self._types.add("table")
        overrides = []
        if value_color:
            overrides.append({"matcher": {"id": "byName", "options": "Value"},
                              "properties": [{"id": "custom.cellOptions", "value": {"type": "color-background"}}]})
        self.panels.append({
            "type": "table", "title": title, "description": description, "id": self._next_id(),
            "datasource": DS, "gridPos": self._place(w, h),
            "fieldConfig": {"defaults": {"unit": value_unit, "mappings": mappings or [],
                                         "thresholds": _thresholds(thresholds),
                                         "custom": {"align": "auto", "cellOptions": {"type": "auto"}}},
                            "overrides": overrides},
            "options": {"showHeader": True, "cellHeight": "sm", "footer": {"show": False}},
            "targets": [target(expr, "__auto", instant=True)],
            "transformations": [{"id": "organize", "options": {
                "excludeByName": {k: True for k in hide}, "renameByName": rename or {}}}],
        })

    def to_json(self):
        requires = [{"type": "grafana", "id": "grafana", "name": "Grafana", "version": "11.0.0"},
                    {"type": "datasource", "id": "prometheus", "name": "Prometheus", "version": "1.0.0"}]
        names = {"stat": "Stat", "timeseries": "Time series", "bargauge": "Bar gauge", "table": "Table"}
        for t in sorted(self._types):
            requires.append({"type": "panel", "id": t, "name": names[t], "version": ""})
        return {
            "__requires": requires,
            "annotations": {"list": [{"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"},
                                      "enable": True, "hide": True, "iconColor": "rgba(0, 211, 255, 1)",
                                      "name": "Annotations & Alerts", "type": "dashboard"}]},
            "description": self.description,
            "editable": True,
            "fiscalYearStartMonth": 0,
            "graphTooltip": 1,
            "links": self.links,
            "panels": self.panels,
            "refresh": "30s",
            "schemaVersion": SCHEMA_VERSION,
            "tags": self.tags,
            "templating": {"list": [datasource_var()] + self.variables},
            "time": {"from": "now-1h", "to": "now"},
            "timepicker": {},
            "timezone": "browser",
            "title": self.title,
            "uid": self.uid,
            "version": 1,
        }


def datasource_var():
    return {"name": "DS_PROMETHEUS", "label": "Data source", "type": "datasource", "query": "prometheus",
            "current": {}, "hide": 0, "includeAll": False, "multi": False, "options": [], "refresh": 1,
            "regex": "", "skipUrlSync": False}


def query_var(name, label, query, all_value=".*", multi=True, include_all=True, hide=0, sort=1):
    v = {"name": name, "label": label, "type": "query", "datasource": DS,
         "definition": query, "query": {"qryType": 1, "query": query, "refId": "PrometheusVariableQueryEditor-VariableQuery"},
         "current": {}, "hide": hide, "includeAll": include_all, "multi": multi, "options": [],
         "refresh": 2, "regex": "", "skipUrlSync": False, "sort": sort}
    if include_all:
        v["allValue"] = all_value
        v["current"] = {"selected": True, "text": ["All"], "value": ["$__all"]}
    return v


def q(level, metric, by="le", sel=""):
    """histogram_quantile over a classic histogram."""
    return f"histogram_quantile({level}, sum by ({by}) (rate({metric}_bucket{{{sel}}}[$__rate_interval])))"


def pct(levels, metric, sel, by="le", extra_legend=""):
    out = []
    for lv in levels:
        by_ = by if by == "le" else f"le, {by}"
        legend = f"p{int(float(lv) * 100)}" + (f" {extra_legend}" if extra_legend else "")
        out.append((q(lv, metric, by_, sel), legend))
    return out


def link_to(tag):
    return {"asDropdown": True, "icon": "external link", "includeVars": False, "keepTime": True,
            "tags": [tag], "targetBlank": False, "title": "AI observability", "type": "dashboards"}
