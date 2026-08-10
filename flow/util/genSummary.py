#!/usr/bin/env python3
"""Write one comprehensive QoR-summary JSON per ORFS run.

ORFS emits a rich reports/.../metadata.json (470+ keys) plus per-stage *.json /
*.log files under logs/. Loading all of that over a network bucket for many
designs is slow. This tool distils EVERY important metric -- captured at EACH
flow stage -- into a single small file (`metadata-summary.json`), so the analysis side
reads ONE file per run yet can still do full stage-by-stage analysis.

Run it as an ORFS make target right after `metadata` (see `metadata-summary` in
util/utils.mk). It reads the freshly written metadata.json; if that is missing
(a crashed flow) it reconstructs from the per-stage files in --logs, so a summary
is still produced and the run is marked `errored` with the last stage reached.

THE TERMINAL STAGE IS CONFIGURABLE (--final-stage / $GENSUMMARY_FINAL_STAGE).
A flow that deliberately stops early -- e.g. a screening campaign whose ORFS
target ends at `globalroute` -- is NOT a failed run, and must not be recorded as
one. Point --final-stage at the stage the flow was asked to reach:

    genSummary.py --final-stage globalroute ...

`completed` then means "the DECLARED endpoint was reached", `status` follows it,
and `final` is the QoR snapshot AT that endpoint (see below). The default is
`finish`, so an unqualified invocation behaves exactly as before. `auto` is also
accepted; it treats the furthest stage reached as the endpoint provided no error
line was found in the logs (convenient, but it cannot distinguish "stopped on
purpose" from "silently truncated" -- prefer the explicit stage name).

FORGETTING TO DECLARE THE ENDPOINT IS LOUD, because it is silent in the data.
A run that did not reach its declared endpoint yet left NO error line anywhere in
the logs is not a diagnosis, it is a question: either it was killed leaving no
trace, or the endpoint was never declared. The second case is not hypothetical --
a 3120-run campaign recorded status="errored" on every single row that way (the
ORFS `metadata-summary` target passed no --final-stage and $GENSUMMARY_FINAL_STAGE
was unset, so every deliberately-globalroute-terminal run was scored against
`finish`). That combination therefore gets a WARNING NAMING THE FIX, plus its own
`failure.reason` (NO_ERROR_REASON) instead of the generic "Other" it used to share
with genuinely unclassifiable failures -- which is what makes the shape visible in
a data-quality table rather than invisible inside it. The default deliberately
does NOT become `auto` to paper over this: `auto` cannot tell "stopped on purpose"
from "silently truncated", so that smarter default would launder exactly the
truncation the warning exists to surface.

Output schema (schema_version 3):
  {
    "schema_version": 3,
    "platform","design","variant","openroad_version","generate_date","cores",
    "status": "ok|warned|errored", "completed": bool, "last_stage": str, "n_errors": int,
    "final_stage": str,            # the endpoint this summary was scored against
    "final_stage_reached": bool,   # == completed; explicit so `final` is self-describing
    "stage_order": [stages in flow order that produced data],
    "stages": { "<stage>": { <every metric present at that stage>,
                             runtime_s, cpu_s, mem_mb,
                             repair_timing_s } },   # only where that stage timed one
    "final":  { <metric>: value at final_stage, carried forward },  # endpoint QoR
    "totals": { runtime_s(sum), peak_mem_mb(max), cpu_total_s(sum), cpu_util },
    "clock_period": float|null,   # T: tightest clock, scoring T-normalization
    "n_endpoints":  int|null,     # N_ep: timing endpoints, TNS-norm denominator
    "host": { "id": str|null,     # machine identity, best-effort
              "source": "flow"|"gensummary" },
    "global_sizing": {            # null when --logs was not given (nothing scanned)
      "injected": bool,           # False = the engine did not run. NORMAL, not an error.
      "stages": [stages whose logs carried any record; >1 means records were
                 displaced, see scan_global_sizing],
      "<record>": null | { "stage": str|null, "raw": str, "fields": {str: str} }
    },
    "failure": {                  # an error line was found ANYWHERE (even past the
                                  #   endpoint), or the endpoint was not reached.
                                  #   Needs --logs: without it nothing is scanned
                                  #   and no failure block is written either way.
      "reason": str, "message": str, "stage": str|null,
      "congestion_gate": bool     # True = global_route REFUSED the design
    }                             #   (GRT-0116/GRT-0232), recoverable by
  }                               #   re-running with GLOBAL_ROUTE_ALLOW_CONGESTION=1

`stages.<stage>.repair_timing_s` is the in-tool timer resize.tcl emits around its
`repair_timing` call (metric `<stage>__repair_timing__wall_seconds`) -- t_tool,
as opposed to the stage's `runtime_s` (which also covers repair_design, the
parasitics estimates and the reports) and `totals.runtime_s` (t_flow, dominated
by host load). It is a resource measurement, not QoR, so it lives beside
runtime_s/cpu_s/mem_mb and stays OUT of `totals` (a sub-stage timer summed with
its own stages would double-count) and out of `final` (a snapshot of QoR). The
key is present only on stages that actually timed a call, so a missing key reads
"not timed", never "timed at zero".

`host` records WHICH machine, and honestly says whose: `source="flow"` is the
`<stage>__host` metric resize.tcl emits, i.e. the machine that ran the flow;
`source="gensummary"` is this process's own hostname, used only when the flow-time
value is absent -- and those differ whenever summaries are produced from a bucket.
Consumed for post-hoc runtime stratification only, never as a scoring input; a
null id is normal, not an error.

`global_sizing` carries the LR gate-sizing engine's per-run records (RSZ-0417 the
always-on effective-config echo, 0443 cap re-check, 0444 terminal alpha, 0445
min_size_fixviol repairs, 0450/0451 threshold-battery handover/stop) out of the
stage logs, which are discarded after the run. Each record keeps its RAW line
verbatim -- the ground truth, immune to format drift -- alongside a parsed
`fields` dict whose values are all STRINGS, deliberately uncoerced (see
parse_gs_fields). `injected` false means the engine never ran: the expected,
correct reading for stock ORFS, and exactly what a campaign checks to confirm its
injection took effect.

`final` semantics, decided explicitly so a GR-terminal run is unambiguous:
`final[m]` is the LAST non-null value of `m` over the stages **at or before
`final_stage`** -- i.e. the endpoint snapshot, with earlier stages carried
forward for metrics the endpoint itself does not re-emit. Consequences, all
intended:
  * A run stopping at `globalroute` gets `final` == the globalroute snapshot;
    route-only metrics (`drc`, routed `wirelength`) are null because they do not
    exist, not because anything failed.
  * A run that OVERSHOOTS the declared endpoint still reports `final` at the
    declared endpoint, so every run in a campaign is compared at the same stage.
    (The later stages remain available under `stages`.)
  * If the endpoint was never reached, `final` covers every stage that did run
    and `final_stage_reached` is false -- read it as a partial snapshot.

Both normalization inputs are sourced self-contained from the run's own outputs
(clock_period from metadata's constraints__clocks__details; n_endpoints from the
per-stage timing__path__endpoint__count metric report_metrics.tcl emits) -- no
external manifest or side-channel.
Utilization is stored as a percentage; memory in MiB (peak RSS KiB / 1024);
runtime and CPU time in seconds.
"""
import argparse
import glob
import json
import os
import re
import socket
import sys

# --- metric definitions (keep ids in sync with analyze_experiments.ipynb) ---
# Per-stage QoR metrics: friendly id -> key suffix after "<stage>__". Every one
# that exists at a stage is captured, so metrics can be tracked stage-by-stage.
STAGE_QOR_KEYS = {
    "area":           "design__instance__area",
    "area_stdcell":   "design__instance__area__stdcell",
    "utilization":    "design__instance__utilization",           # -> percent
    "insts":          "design__instance__count",
    "stdcells":       "design__instance__count__stdcell",
    "macros":         "design__instance__count__macros",
    "nets":           "design__nets",
    "pins":           "design__io",
    "power_int":      "power__internal__total",
    "power_leak":     "power__leakage__total",
    "power_sw":       "power__switching__total",
    "power_total":    "power__total",
    "wirelength":     "route__wirelength",                       # routed (detailedroute)
    "wirelength_est": "route__wirelength__estimated",            # pre-route estimate
    "drc":            "route__drc_errors",
    "gr_overflow":     "global_route__overflow__total",          # GRT global-route overflow (Gap 1)
    "gr_overflow_max": "global_route__overflow__max",            # GRT global-route overflow (Gap 1)
    "setup_ws":       "timing__setup__ws",
    "setup_tns":      "timing__setup__tns",
    "hold_ws":        "timing__hold__ws",
    "hold_tns":       "timing__hold__tns",
    "clk_skew":       "clock__skew__setup",
    "clk_skew_hold":  "clock__skew__hold",
    "setup_viol":     "timing__drv__setup_violation_count",
    "hold_viol":      "timing__drv__hold_violation_count",
    "max_slew_viol":  "timing__drv__max_slew",
    "max_cap_viol":   "timing__drv__max_cap",
    "max_fanout_viol": "timing__drv__max_fanout",
    "fmax":           "timing__fmax",
}
# Per-stage resource metrics come from the gnu-time line of each stage log.
#   runtime_s <- <stage>__elapsed_seconds, cpu_s <- <stage>__cpu__total,
#   mem_mb <- <stage>__mem__peak / 1024

#: In-tool timer: wall seconds of the stage's own `repair_timing` call, emitted
#: by resize.tcl as a utl metric. A resource measurement rather than QoR, so it
#: joins the block above instead of STAGE_QOR_KEYS -- which also keeps it out of
#: `final`, where a runtime has no business being carried forward.
#: NOTE the suffix is `__wall_seconds`, NOT `__elapsed_seconds`: stages_present()
#: derives the flow's stage list from every key ending in `__elapsed_seconds`, so
#: that spelling would mint a phantom stage named "<stage>__repair_timing".
REPAIR_TIMING_KEY = "repair_timing__wall_seconds"

#: Machine that ran the flow, emitted by resize.tcl as a utl string metric. See
#: flow_host() for why this is not simply socket.gethostname().
HOST_METRIC_KEY = "host"

# Flow order. Verified against the real log mtimes of an ASAP7 run:
#   5_1_grt -> 5_2_route -> 5_3_fillcell -> 6_1_fill -> 6_report / 6_1_merge.
# (`fillcell` used to be listed BEFORE `detailedroute`, which inverted the rank of
# two consecutive stages and therefore mis-ordered `stage_order` and the `final`
# carry-forward scan.) `finish` is kept last so `last_stage` reads "finish" on a
# completed run; 6_1_merge only merges GDS and emits no QoR, so its position
# relative to 6_report is immaterial to every derived field.
STAGE_ORDER = [
    "synth",
    "floorplan", "floorplan_io", "floorplan_macro", "floorplan_tap", "floorplan_pdn",
    "globalplace_skip_io", "globalplace_io", "globalplace", "placeopt", "detailedplace",
    "cts",
    "globalroute", "detailedroute", "fillcell", "density_fill",
    "finish_merge", "finish",
]
STAGE_RANK = {s: i for i, s in enumerate(STAGE_ORDER)}
DEFAULT_FINAL_STAGE = "finish"

STAGE_BASENAME_RULES = [
    ("place_gp_skip_io", "globalplace_skip_io"), ("place_iop", "globalplace_io"),
    ("place_gp", "globalplace"), ("place_resized", "placeopt"), ("place_dp", "detailedplace"),
    ("floorplan_macro", "floorplan_macro"), ("floorplan_tapcell", "floorplan_tap"),
    ("floorplan_tap", "floorplan_tap"), ("floorplan_pdn", "floorplan_pdn"),
    ("floorplan_io", "floorplan_io"), ("floorplan", "floorplan"),
    ("cts", "cts"), ("grt", "globalroute"), ("fillcell", "fillcell"),
    # 6_1_fill = density fill, a DIFFERENT stage from 5_3_fillcell. It carries a
    # gnu-time line, so without this rule its runtime/CPU/memory were silently
    # dropped from `stages` and undercounted in `totals`. Must stay AFTER the
    # "fillcell" rule, which is a longer match on the same substring.
    ("fill", "density_fill"),
    ("route", "detailedroute"), ("merge", "finish_merge"), ("report", "finish"),
    ("yosys", "synth"), ("synth", "synth"),
]

# "CPU time: user 3046.62 sys 1.93 (10772%)" -- `sys` is optional so the pattern
# still matches builds of the time wrapper that omit it.
_GNU_RE = re.compile(
    r"^Elapsed time: (\S+)\[h:\]min:sec.*?CPU time: user (\S+)(?:\s+sys\s+(\S+))?"
    r".*?Peak memory: (\S+)KB", re.M)
_THREAD_RE = re.compile(r"Using (\d+) thread")


def stage_of(basename):
    b = basename.lower()
    if "canonicalize" in b or "lec_check" in b:
        return None
    for pat, st in STAGE_BASENAME_RULES:
        if pat in b:
            return st
    return None


def fnum(x):
    try:
        if x is None:
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def runtime_to_seconds(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s or s.upper() == "ERR":
        return None
    try:
        parts = [float(p) for p in s.split(":")]
    except ValueError:
        return None
    sec = 0.0
    for p in parts:
        sec = sec * 60 + p
    return sec


# --- metric dict acquisition -------------------------------------------------
def load_metadata(path):
    with open(path) as f:
        return json.load(f)


# --- per-design scoring-normalization inputs (Gaps 2-3) ----------------------
def min_clock_period(clock_details):
    """Tightest create_clock -period across metadata's
    constraints__clocks__details (genMetrics.py writes each entry as
    'name: period'). The value is in the SDC's own time unit, which is the
    PLATFORM's time unit (`run__flow__platform__time_units`) -- ps on asap7, ns
    on nangate45/sky130 -- and is the SAME unit as the setup slack/TNS metrics,
    so it is used raw and no conversion is needed or wanted. (The previous
    docstring claimed ns universally, which is wrong for asap7; the arithmetic
    was always right because T and TNS share the unit and the scoring only ever
    forms the dimensionless ratio TNS/(T*N_ep).) The min-period domain governs
    the critical path, so it is the T normalization denominator the scoring uses.
    Returns None when no clock is present or the field is absent -- notably in
    the logs-only path, since constraints__clocks__details exists ONLY in
    metadata.json (genMetrics reads it from results/2_floorplan.sdc, which is not
    under logs/). main() warns in that case."""
    if not clock_details:
        return None
    periods = []
    for entry in clock_details:
        try:
            periods.append(float(str(entry).rsplit(":", 1)[1]))
        except (IndexError, ValueError):
            continue
    return min(periods) if periods else None


ENDPOINT_COUNT_KEY = "timing__path__endpoint__count"


def endpoint_count(meta, upto=None):
    """Total timing-endpoint count N_ep (the TNS-normalization denominator,
    scoring spec §2.2), a per-design constant sourced from the run's OWN
    outputs. report_metrics.tcl emits it at every report_metrics call via
    `utl::metric_int "timing__path__endpoint__count" [sta::endpoint_count]`, so
    it lands as <stage>__timing__path__endpoint__count in each stage's -metrics
    JSON and the merged metadata.json -- present in the metadata path, the
    logs-reconstruction path, and the merge of both. Returns the value from the
    latest stage present (all stages agree; the latest is closest to the scored
    endpoint), or None when no stage emitted it (metrics skipped, or a run built
    with an ORFS that predates the emission). ``upto`` restricts the scan to
    stages at or before a declared endpoint, so N_ep is read at the same stage
    the rest of `final` is (all stages agree in practice; this only keeps the
    summary internally consistent when they do not)."""
    limit = STAGE_RANK.get(upto, len(STAGE_ORDER)) if upto else len(STAGE_ORDER)
    val = None
    for s in STAGE_ORDER:
        if STAGE_RANK[s] > limit:
            break
        v = fnum(meta.get(f"{s}__{ENDPOINT_COUNT_KEY}"))
        if v is not None:
            val = v
    return int(val) if val is not None else None


def flow_host(*metas):
    """Hostname of the machine that RAN THE FLOW, from the `<stage>__host` string
    metric resize.tcl emits (`utl::metric "host" [info hostname]`).

    Deliberately not socket.gethostname(): this tool routinely runs somewhere
    else entirely -- summaries are collected from a bucket long after the flow --
    so its own hostname answers a different question, and silently substituting
    one for the other is how a runtime stratification ends up stratifying by the
    wrong machine. Returns None for a run whose flow never reached the emitting
    stage, or that was produced by an ORFS predating the emission; absence is
    normal, not an error."""
    for meta in metas:
        if not meta:
            continue
        for s in STAGE_ORDER:
            v = meta.get(f"{s}__{HOST_METRIC_KEY}")
            if v:
                return str(v)
    return None


def host_record(*metas):
    """`{"id", "source"}` -- which machine, and whose hostname that actually is.

    `source` is the whole point: "flow" is ground truth, "gensummary" is this
    process's own host standing in for it. Stamping the source keeps the two
    distinguishable in the collected data instead of quietly merging a fleet of
    flow hosts with one collection host."""
    host = flow_host(*metas)
    if host:
        return {"id": host, "source": "flow"}
    return {"id": socket.gethostname() or None, "source": "gensummary"}


def reconstruct_from_logs(logs_dir):
    """Rebuild the flat metric dict from logs/*.json (QoR) + logs/*.log gnu-time.

    Mirrors ORFS' genMetrics.py and is validated to match metadata.json; unlike
    metadata.json it also captures stages genMetrics misses (e.g. detailedroute
    timing) due to log-filename drift."""
    meta = {}
    for jf in sorted(glob.glob(os.path.join(logs_dir, "*.json"))):
        if os.path.basename(jf) == "metadata-summary.json":
            continue
        try:
            with open(jf) as f:
                d = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        # Several stages (floorplan_macro, floorplan_tap, floorplan_pdn,
        # place_gp_skip_io, place_iop, fillcell, 6_1_fill) emit their metrics
        # UNPREFIXED -- `flow__errors__count`, `design__violations`, ... . A flat
        # merge therefore (a) let the alphabetically-last file silently win every
        # such key, so `flow__errors__count` reflected 6_1_fill.json alone and the
        # error counts of six stages were dropped, and (b) left those stages with
        # no QoR at all, since build_summary looks up "<stage>__<suffix>". Prefix
        # any key that does not already carry a known stage prefix with the stage
        # the FILE belongs to, which is the information the flat merge threw away.
        st_file = stage_of(os.path.basename(jf))
        for k, v in d.items():
            k = k.replace(":", "__")                # net:VDD -> net__VDD
            # `run__flow__*` (provenance, incl. the platform unit declarations) and
            # `constraints__*` are flow-GLOBAL, not per-stage; prefixing them would
            # hide them from the readers that look them up unprefixed.
            if (st_file and k.split("__", 1)[0] not in STAGE_RANK
                    and not k.startswith(("run__flow__", "constraints__"))):
                k = f"{st_file}__{k}"
            meta[k] = v
    per_stage = {}
    for lf in sorted(glob.glob(os.path.join(logs_dir, "*.log"))):
        st = stage_of(os.path.basename(lf))
        if not st:
            continue
        try:
            with open(lf, errors="replace") as f:
                txt = f.read()
        except OSError:
            continue
        hits = _GNU_RE.findall(txt)
        if not hits:
            continue
        elapsed, user, sysv, peak = hits[-1]
        rt = runtime_to_seconds(elapsed)
        # CPU time = user + sys (the `sys` group is optional). Reporting user
        # alone understated cpu_total_s and cpu_util.
        cpu = fnum(user)
        sys_s = fnum(sysv)
        if cpu is not None and sys_s is not None:
            cpu += sys_s
        mem = fnum(peak)
        # Two logs can map to one stage (e.g. a retried stage); keep the longest.
        if st not in per_stage or (rt or 0) > (per_stage[st][0] or 0):
            per_stage[st] = (rt, cpu, mem)
    for st, (rt, cpu, mem) in per_stage.items():
        meta[f"{st}__elapsed_seconds"] = rt
        meta[f"{st}__cpu__total"] = cpu
        meta[f"{st}__mem__peak"] = mem
    return meta


def cores_from_logs(logs_dir):
    for lf in sorted(glob.glob(os.path.join(logs_dir, "*.log"))):
        try:
            with open(lf, errors="replace") as f:
                head = f.read(32768)
        except OSError:
            continue
        m = _THREAD_RE.search(head)
        if m:
            return int(m.group(1))
    return None


# --- summary construction ----------------------------------------------------

#: GRT's two TERMINAL congestion errors -- the congestion *gate*:
#:   GRT-0116  "Global routing finished with congestion." -- from
#:             finishGlobalRouting(), i.e. a batch `global_route`.
#:   GRT-0232  "Routing congestion too high."             -- from
#:             updateDirtyRoutes(), i.e. `global_route -end_incremental`.
#: Both end the stage BEFORE report_metrics, so a congested run carries no
#: global-route QoR at all -- the most congested designs are exactly the ones
#: missing from any congestion metric. Both are recoverable by re-running with
#: ORFS GLOBAL_ROUTE_ALLOW_CONGESTION=1, which passes -allow_congestion to every
#: global_route call in the stage and turns them into warnings, so the stage
#: finishes and its congestion is MEASURED instead of gating the run.
#:
#: That disposition -- refused, not broken, and recoverable by a re-run with one
#: variable set -- is why this gets its own reason instead of being folded into
#: the generic "congestion" word match below (which also catches detailed-route
#: congestion and any other message merely containing the word) or into the
#: generic "GRT-" match (any global-route error whatsoever).
GRT_CONGESTION_CODES = ("GRT-0116", "GRT-0232")
GRT_CONGESTION_REASON = "Global routing congestion"

#: `failure.reason` for a run that did not reach its declared endpoint and yet
#: left NO error line anywhere in its logs. scan_failure's "Other" used to cover
#: this, which put it in the same bucket as a run that DID fail in a way no rule
#: recognises -- two opposite situations under one word. They need different
#: names because they need different actions: "Other" means read the message,
#: this means there is no message to read, so either the run died without a trace
#: or the endpoint was mis-declared (see the module docstring; a whole campaign
#: has already been lost to the latter). main() pairs it with a loud warning.
NO_ERROR_REASON = "Endpoint not reached (no error found in logs)"

_FAIL_RULES = [
    # by error CODE, ahead of both generic matches -- see GRT_CONGESTION_CODES
    (GRT_CONGESTION_CODES[0], GRT_CONGESTION_REASON),
    (GRT_CONGESTION_CODES[1], GRT_CONGESTION_REASON),
    ("congestion",     "Routing congestion"),
    ("DRT-",           "Detailed routing"),
    ("GRT-",           "Global routing"),
    ("ANT-",           "Antenna violations"),
    ("RSZ-",           "Timing repair (resizer)"),
    ("GPL-",           "Global placement"),
    ("DPL-",           "Detailed placement"),
    ("CTS-",           "Clock tree synthesis"),
    ("PSM-",           "Power grid (PDN)"),
    ("PDN-",           "Power grid (PDN)"),
    ("std::bad_alloc", "Out of memory"),
    ("bad_alloc",      "Out of memory"),
    ("out of memory",  "Out of memory"),
    ("cannot allocate","Out of memory"),
    ("Killed",         "Killed (likely OOM)"),
]

def scan_failure(logs_dir):
    """Best-effort (reason, message, stage) for a failed run, from the stage logs.

    ``stage`` is the flow stage whose log carried the last error line, or None.
    It matters because the gnu-time wrapper writes its timing line in a `finally:`
    block -- i.e. **whether or not the stage succeeded** -- so "the stage produced
    an elapsed_seconds key" is NOT evidence that the stage worked. The error's
    stage is the only signal that distinguishes "stopped at the endpoint on
    purpose" from "crashed at the endpoint".

    Returns ("Other", "", None) when nothing identifiable is found (e.g. an OOM
    SIGKILL that left no error line -- the analysis side treats a *missing* summary
    as OOM). Note that "Other" is this function's sentinel, not necessarily what
    lands in the summary: an INCOMPLETE run with no error line is recorded under
    NO_ERROR_REASON instead, because "nothing failed that we can see" and "it
    failed in a way no rule matches" want different words. See main()."""
    last, last_stage = None, None
    for lf in sorted(glob.glob(os.path.join(logs_dir, "*.log")),
                     key=lambda p: (STAGE_RANK.get(stage_of(os.path.basename(p)), 999),
                                    os.path.basename(p))):
        try:
            with open(lf, errors="replace") as f:
                for line in f:
                    s = line.strip()
                    low = s.lower()
                    if ("[error" in low or low.startswith("error")
                            or "bad_alloc" in low or "out of memory" in low
                            or "cannot allocate" in low or s == "Killed"):
                        last, last_stage = s, stage_of(os.path.basename(lf))
        except OSError:
            continue
    if not last:
        return "Other", "", None
    for pat, reason in _FAIL_RULES:
        if pat.lower() in last.lower():
            return reason, last[:300], last_stage
    return "Other", last[:300], last_stage


# --- global-sizing engine records (RSZ-04xx) ---------------------------------
#: The LR gate-sizing engine (OpenROAD src/rsz) emits these INFO records into the
#: log of whichever stage ran `repair_timing`. They are the per-run proof that the
#: intended experiment cell actually took effect, and the stage logs are thrown
#: away after the run -- so they are captured here or nowhere.
#:
#:   name              code       what it records
GS_RECORDS = {
    "config":           "RSZ-0417",  # always-on echo of the EFFECTIVE config
    "cap_recheck":      "RSZ-0443",  # post-sweep max-cap reverts
    "terminal_alpha":   "RSZ-0444",  # livramento_alpha terminal schedule + floor
    "fixviol":          "RSZ-0445",  # min_size_fixviol repair counts
    "battery_handover": "RSZ-0450",  # threshold_battery timing -> power handover
    "battery_stop":     "RSZ-0451",  # threshold_battery stop + the phase split
}
_GS_BY_CODE = {code: name for name, code in GS_RECORDS.items()}
_GS_CODE_RE = re.compile("|".join(re.escape(c) for c in GS_RECORDS.values()))

# Every `key=value` token in a record. The engine prints no value containing a
# space, so \S+ is the whole value; message punctuation that abuts it is stripped
# ("T=0.34]." -> "0.34", "floor=0.001)." -> "0.001").
_GS_KV_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=(\S+)")
_GS_VALUE_TRAILING = "].,;:)"

#: Counts some records state in PROSE rather than as key=value. Named groups only;
#: a pattern that stops matching after an engine reword costs its parsed fields and
#: nothing else, because the raw line is always kept.
_GS_PROSE_RES = {
    "cap_recheck": re.compile(r"cap re-check:\s*(?P<reverted>\d+) move"),
    "terminal_alpha": re.compile(r"(?P<reschedules>\d+) reschedules"),
    "fixviol": re.compile(
        r"(?P<violating>\d+) of (?P<editable>\d+) editable .*?"
        r"(?P<cleared>\d+) cleared by upsizing, (?P<still_violating>\d+) still"),
    "battery_handover": re.compile(
        r"timing phase ended after (?P<timing_iters>\d+) iteration\(s\) "
        r"\((?P<reason>[^)]*)\)"),
    "battery_stop": re.compile(
        r"stopped after (?P<iters>\d+) iteration\(s\) \((?P<timing_iters>\d+) "
        r"timing \+ (?P<power_iters>\d+) power\) in the (?P<phase>\S+) phase "
        r"\((?P<reason>[^)]*)\)"),
}


def parse_gs_fields(line, record=None):
    """Every parseable field of an engine record, as STRINGS.

    Uncoerced on purpose. The question these records answer is "did this run get
    the cell I asked for", which is string equality against the requested config;
    coercion would fold `1e-06` and `0.000001` together, turn `max_iter=160` into
    a float, and -- worst -- give a value the engine may one day print as `inf` or
    `nan` a chance to blow up the collector on a run that is otherwise fine. The
    raw line is kept alongside, so a consumer that wants numbers has both.

    ``record`` names the GS_RECORDS entry the line came from and selects the extra
    regex for the counts that record states in prose. It lives here rather than in
    the caller so this function IS the whole parser: an archived `raw` line
    re-parsed later reproduces exactly the `fields` the collector stored."""
    fields = {k: v.rstrip(_GS_VALUE_TRAILING) for k, v in _GS_KV_RE.findall(line)}
    prose = _GS_PROSE_RES.get(record)
    if prose:
        m = prose.search(line)
        if m:
            fields.update(m.groupdict())
    return fields


def scan_global_sizing(logs_dir):
    """The gate-sizing engine's per-run records, from the stage logs.

    Scans every stage log, not just the resize stage's, and stamps each record
    with the stage whose log carried it: `repair_timing` is called from several
    stage scripts, so WHERE the engine ran is part of the answer -- and a scan
    hard-coded to one stage would silently report "not injected" for a campaign
    that injects somewhere else. Traversal mirrors scan_failure's (flow order,
    last occurrence wins).

    Returns the `global_sizing` block. `injected` is False -- never an error --
    when the always-on RSZ-0417 echo is absent, which is simply what stock ORFS
    with no injection looks like."""
    # `found` keeps one entry per code, so it cannot answer "where were records
    # seen" -- by construction it has already dropped the displaced ones. Stages
    # are collected separately, as they are encountered.
    found, seen_stages = {}, set()
    for lf in sorted(glob.glob(os.path.join(logs_dir, "*.log")),
                     key=lambda p: (STAGE_RANK.get(stage_of(os.path.basename(p)), 999),
                                    os.path.basename(p))):
        st = stage_of(os.path.basename(lf))
        try:
            with open(lf, errors="replace") as f:
                for line in f:
                    # This loop sees every line of every stage log, and all but a
                    # handful miss. A literal substring test is markedly cheaper
                    # than retrying the six-way alternation at each position.
                    if "RSZ-04" not in line:
                        continue
                    m = _GS_CODE_RE.search(line)
                    if m:
                        found[_GS_BY_CODE[m.group(0)]] = (line.strip(), st)
                        seen_stages.add(st)
        except OSError:
            continue

    # One slot per code, last occurrence winning, is right for the one-injection
    # case and lossy for any other: `repair_timing` is called from four stage
    # scripts, so a config applied flow-wide would emit RSZ-0417 several times and
    # the later stage would quietly displace the sizing stage's record. `stages`
    # lists every stage that carried ANY record, so that displacement is visible in
    # the summary instead of being a thing you could only learn from the logs --
    # which are gone. More than one entry here means the per-record `stage` fields
    # are the survivors, not the whole story.
    block = {"injected": "config" in found,
             "stages": sorted((s for s in seen_stages if s),
                              key=lambda s: (STAGE_RANK.get(s, len(STAGE_ORDER)), s))}
    for name in GS_RECORDS:
        if name not in found:
            block[name] = None
            continue
        raw, st = found[name]
        block[name] = {"stage": st, "raw": raw, "fields": parse_gs_fields(raw, name)}
    return block


def stages_present(meta):
    st = {k[: -len("__elapsed_seconds")] for k in meta if k.endswith("__elapsed_seconds")}
    # Tie-break unknown stages (rank 999) by name: a set's iteration order is
    # hash-randomized, and this ordering feeds stage_order, last_stage and the
    # `final` carry-forward scan, which must be reproducible run to run.
    return sorted(st, key=lambda s: (STAGE_RANK.get(s, 999), s))


def resolve_final_stage(requested, stages, had_error):
    """Turn a --final-stage request into the concrete endpoint to score against.

    A named stage is used verbatim (validated by the CLI). ``auto`` means "the
    flow stopped where it meant to": the furthest stage reached, provided the logs
    showed no error line. If an error WAS found, auto falls back to the default
    endpoint so the run is still reported as incomplete -- auto must never launder
    a crash into a success. With no stages at all there is nothing to infer from,
    so the default stands.

    Note this only handles the `auto` path; the explicit path is guarded inside
    build_summary, which fails the run whenever an error line was found at or
    before the declared endpoint (see `error_stage` there).
    """
    if requested and requested != "auto":
        return requested
    if not stages or had_error:
        return DEFAULT_FINAL_STAGE
    return stages[-1]


def build_summary(meta, platform, design, variant, cores,
                  final_stage=DEFAULT_FINAL_STAGE, error_stage=None):
    """Assemble the summary dict.

    ``error_stage`` is the stage whose log carried the last error line (from
    scan_failure), or None. It is REQUIRED for a correct `completed`: the gnu-time
    wrapper writes its timing line in a `finally:` block, so a stage appears in
    `stages` even when it crashed. Without this, a run that dies inside the
    declared endpoint stage would be recorded as a healthy completion -- the worst
    possible failure mode for a study that treats completion as a tested outcome.
    """
    stages = stages_present(meta)
    reached = final_stage in stages
    # An error at or before the endpoint means the endpoint was not cleanly
    # reached, however far the stage list appears to go.
    limit = STAGE_RANK.get(final_stage, len(STAGE_ORDER))
    failed_at_or_before = (error_stage is not None
                           and STAGE_RANK.get(error_stage, len(STAGE_ORDER)) <= limit)
    completed = reached and not failed_at_or_before
    # Per-stage error counts are the authoritative tally. metadata.json ALSO
    # carries an unprefixed flow-wide `flow__errors__count`; adding both
    # double-counted every error in the metadata-only path, so the flow-wide value
    # is used only as a fallback when NO stage reported the key at all (testing
    # presence, not truthiness -- "every stage reported 0" is a real answer).
    stage_keys = [f"{s}__flow__errors__count" for s in stages]
    if any(k in meta for k in stage_keys):
        n_err = sum(int(fnum(meta.get(k)) or 0) for k in stage_keys)
    else:
        n_err = int(fnum(meta.get("flow__errors__count")) or 0)
    status = "errored" if not completed else ("warned" if n_err else "ok")

    # every metric present at each stage + that stage's resources
    stage_out = {}
    for s in stages:
        d = {}
        for mid, suf in STAGE_QOR_KEYS.items():
            v = fnum(meta.get(f"{s}__{suf}"))
            if v is None:
                continue
            if mid == "utilization":
                v *= 100.0
            d[mid] = v
        rt = fnum(meta.get(f"{s}__elapsed_seconds"))
        cpu = fnum(meta.get(f"{s}__cpu__total"))
        mp = fnum(meta.get(f"{s}__mem__peak"))
        d["runtime_s"] = rt
        d["cpu_s"] = cpu
        d["mem_mb"] = (mp / 1024.0) if mp is not None else None
        # Set only where the stage actually timed a repair_timing call, so a
        # missing key reads "not timed" rather than "timed, and it took zero".
        rtt = fnum(meta.get(f"{s}__{REPAIR_TIMING_KEY}"))
        if rtt is not None:
            d["repair_timing_s"] = rtt
        stage_out[s] = d

    # `stages` is already flow-sorted (unknown stages last), so use it directly.
    # Filtering through STAGE_ORDER would DROP any stage not in that list -- from
    # stage_order, from `final`, and from the runtime/CPU/memory totals.
    ordered = list(stages)

    # `final` = the QoR snapshot AT final_stage, with earlier stages carried
    # forward for metrics the endpoint does not re-emit. Stages BEYOND the
    # declared endpoint are excluded so every run in a campaign is summarized at
    # the same stage even if some ran further (they stay visible under `stages`).
    limit = STAGE_RANK.get(final_stage, len(STAGE_ORDER))
    upto = [s for s in ordered if STAGE_RANK.get(s, len(STAGE_ORDER)) <= limit]
    final = {}
    for mid in STAGE_QOR_KEYS:
        val = None
        for s in upto:
            v = stage_out[s].get(mid)
            if v is not None:
                val = v
        final[mid] = val

    rts = [stage_out[s]["runtime_s"] for s in ordered if stage_out[s]["runtime_s"] is not None]
    mems = [stage_out[s]["mem_mb"] for s in ordered if stage_out[s]["mem_mb"] is not None]
    cpus = [stage_out[s]["cpu_s"] for s in ordered if stage_out[s]["cpu_s"] is not None]
    runtime_s = sum(rts) if rts else None
    peak_mem_mb = max(mems) if mems else None
    cpu_total_s = sum(cpus) if cpus else None
    cpu_util = (cpu_total_s / runtime_s) if (cpu_total_s and runtime_s) else None

    return {
        "schema_version": 3,
        "platform": platform or meta.get("run__flow__platform"),
        "design": design or meta.get("run__flow__design"),
        "variant": variant or meta.get("run__flow__variant"),
        "openroad_version": meta.get("run__flow__openroad_version"),
        "generate_date": meta.get("run__flow__generate_date"),
        "cores": cores,
        "status": status,
        "completed": completed,
        "last_stage": ordered[-1] if ordered else None,
        "final_stage": final_stage,
        "final_stage_reached": completed,
        "n_errors": n_err,
        "stage_order": ordered,
        "stages": stage_out,
        "final": final,
        "totals": {"runtime_s": runtime_s, "peak_mem_mb": peak_mem_mb,
                   "cpu_total_s": cpu_total_s, "cpu_util": cpu_util},
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-m", "--metadata", help="path to reports/.../metadata.json")
    ap.add_argument("-l", "--logs", help="path to logs/.../<variant> (fallback + cores)")
    ap.add_argument("-o", "--output", required=True, help="summary JSON to write")
    ap.add_argument("-c", "--cores", help="threads given to the flow (NUM_CORES)")
    ap.add_argument("-p", "--platform")
    ap.add_argument("-d", "--design")
    ap.add_argument("-v", "--variant")
    ap.add_argument("-f", "--final-stage",
                    default=os.environ.get("GENSUMMARY_FINAL_STAGE",
                                           DEFAULT_FINAL_STAGE),
                    help=("stage the flow was asked to reach; `completed`/`status`"
                          " and the `final` snapshot are scored against it. One of "
                          + ", ".join(STAGE_ORDER)
                          + ", or `auto` (= furthest stage reached, provided no "
                            "error line was found). Defaults to $GENSUMMARY_FINAL_"
                            "STAGE, else " + DEFAULT_FINAL_STAGE + "."))
    args = ap.parse_args()

    requested = (args.final_stage or DEFAULT_FINAL_STAGE).strip()
    if requested not in STAGE_ORDER and requested != "auto":
        ap.error(f"--final-stage {requested!r} is not a flow stage; expected one of "
                 f"{', '.join(STAGE_ORDER)} or 'auto'")

    cores = None
    if args.cores not in (None, "", "0"):
        try:
            cores = int(args.cores)
        except ValueError:
            cores = None

    # logs/ are authoritative for per-stage timing (complete); metadata.json fills
    # provenance + any QoR keys the logs lack (validated to otherwise match).
    meta, src, md_full = {}, "none", None
    if args.logs and os.path.isdir(args.logs):
        recon = reconstruct_from_logs(args.logs)
        if recon:
            meta, src = recon, "logs"
    if args.metadata and os.path.isfile(args.metadata):
        try:
            md = load_metadata(args.metadata)
        except (OSError, json.JSONDecodeError) as e:
            print(f"[WARN] could not read {args.metadata}: {e}", file=sys.stderr)
            md = None
        if md:
            md_full = md
            if meta:
                # logs are authoritative for stages/status/QoR/timing (they reflect
                # THIS run); only borrow provenance from metadata.json. Never let a
                # possibly-stale metadata.json inject a stage/finish the logs lack
                # (that would mis-mark a crashed/OOM run as completed).
                for k, v in md.items():
                    if k.startswith("run__flow__"):
                        meta.setdefault(k, v)
                src = "logs+metadata"
            else:
                meta, src = md, "metadata.json"

    if cores is None and args.logs and os.path.isdir(args.logs):
        cores = cores_from_logs(args.logs)

    # The failure scan is needed BEFORE build_summary when --final-stage is `auto`
    # (auto must not promote a crashed run's last stage into "the endpoint"), so
    # run it once here and reuse it for the `failure` block below.
    fail_reason, fail_msg, fail_stage = (None, None, None)
    if args.logs and os.path.isdir(args.logs):
        fail_reason, fail_msg, fail_stage = scan_failure(args.logs)
    had_error = bool(fail_msg)

    final_stage = resolve_final_stage(requested, stages_present(meta), had_error)
    if requested == "auto":
        print(f"[genQorSummary] --final-stage auto -> {final_stage!r}"
              f"{' (error found in logs; kept the default endpoint)' if had_error else ''}",
              file=sys.stderr)

    summary = build_summary(meta, args.platform, args.design, args.variant, cores,
                            final_stage=final_stage,
                            error_stage=fail_stage if had_error else None)

    # Per-design normalization inputs the scoring formulas need (Gaps 2-3),
    # both sourced self-contained from THIS run's own outputs (no side-channel).
    #   clock_period (T): tightest create_clock -period from metadata's
    #     constraints__clocks__details (genMetrics.py reads it from
    #     2_floorplan.sdc; reliably present for every clocked run). Note: in the
    #     logs+metadata path only run__flow__* keys land in `meta`, and it is a
    #     top-level (unprefixed) metadata key, so read it from the full metadata
    #     dict first, falling back to `meta` for the metadata-only path.
    #   n_endpoints (N_ep): the per-stage utl metric report_metrics.tcl emits
    #     (timing__path__endpoint__count = sta::endpoint_count), present in both
    #     the metadata and logs-reconstruction paths.
    clocks = None
    if md_full is not None:
        clocks = md_full.get("constraints__clocks__details")
    if clocks is None:
        clocks = meta.get("constraints__clocks__details")
    clock_period = min_clock_period(clocks)
    summary["clock_period"] = clock_period
    if clock_period is None:
        print(f"[WARN] no clock period for design={summary['design']} "
              f"(no constraints__clocks__details); scoring T-normalization "
              f"unavailable -- the Colab scorer must drop this run from timing "
              f"scoring (or treat it as clockless)", file=sys.stderr)

    # Prefer the full metadata dict (complete QoR keys); fall back to `meta`
    # (the logs-reconstruction dict) which carries the same stage-prefixed metric
    # in the logs-only path -- mirrors the clock_period resolution above.
    nep = endpoint_count(md_full, final_stage) if md_full is not None else None
    if nep is None:
        nep = endpoint_count(meta, final_stage)
    summary["n_endpoints"] = nep
    if summary["n_endpoints"] is None:
        print(f"[WARN] no endpoint count for design={summary['design']} (no "
              f"<stage>__{ENDPOINT_COUNT_KEY} in metadata/logs -- run built "
              f"before the report_metrics.tcl emission, or metrics skipped); "
              f"the Colab scorer must fall back to WNS-only timing (Delta_WNS, "
              f"needs only T) for this run", file=sys.stderr)

    # Record WHY, whenever the logs contained an error line -- the logs are the only
    # source and are discarded after the run, so this must be captured here. Emitted
    # UNCONDITIONALLY when a message was found (not only for incomplete runs): an
    # error after the declared endpoint still matters to a reader, and gating it on
    # `completed` silently dropped the evidence for a crash AT the endpoint. A run
    # that simply stopped where it was asked to leaves no error line and therefore
    # still gets no `failure` block -- that was the point of --final-stage.
    #
    # `congestion_gate` is emitted alongside so a consumer never has to string-match
    # the reason: True means "global_route REFUSED this design (GRT-0116/GRT-0232),
    # it did not crash" -- a run recoverable by re-running the stage with
    # GLOBAL_ROUTE_ALLOW_CONGESTION=1. Older summaries simply lack the key, so a
    # reader must treat a missing key as unknown, not as False.
    if had_error:
        summary["failure"] = {"reason": fail_reason, "message": fail_msg,
                              "stage": fail_stage,
                              "congestion_gate": fail_reason == GRT_CONGESTION_REASON}
    elif not summary["completed"] and fail_reason is not None:
        # Incomplete, and the logs name no culprit at all. Its own reason, not the
        # "Other" of a failure we merely could not classify -- and a warning that
        # says what to do about it, because the far likeliest cause is that the
        # endpoint was never declared and this run stopped exactly where it was
        # asked to. See NO_ERROR_REASON and the module docstring.
        summary["failure"] = {"reason": NO_ERROR_REASON, "message": fail_msg,
                              "stage": None, "congestion_gate": False}
        reached = summary["last_stage"]
        print(
            f"[WARN] ==================================================================\n"
            f"[WARN] status=errored, but NO error line was found in ANY log.\n"
            f"[WARN]   declared endpoint : {final_stage}   (--final-stage / "
            f"$GENSUMMARY_FINAL_STAGE)\n"
            f"[WARN]   furthest stage    : {reached}\n"
            f"[WARN] No log RECORDS a failure, which is NOT the same as nothing having\n"
            f"[WARN] failed. Two causes look identical from here: the run was killed\n"
            f"[WARN] leaving no trace (OOM / wall-clock -- that notice goes to the\n"
            f"[WARN] scheduler, never to a stage log), or the endpoint was never\n"
            f"[WARN] declared and a flow that stopped where it was TOLD to is being\n"
            f"[WARN] scored against {final_stage!r}.\n"
            + (f"[WARN] If -- AND ONLY IF -- this flow was configured to stop at\n"
               f"[WARN] {reached!r}, re-summarize with:\n"
               f"[WARN]     GENSUMMARY_FINAL_STAGE={reached}\n"
               f"[WARN] Do NOT apply that across a batch without checking: on a run that\n"
               f"[WARN] was killed it relabels a truncated run as a clean completion, and\n"
               f"[WARN] the stage logs are gone afterwards.\n" if reached else "")
            + f"[WARN] Left unfixed this marks EVERY run of the campaign status=errored /\n"
            f"[WARN] failure.reason={NO_ERROR_REASON!r}.\n"
            f"[WARN] ==================================================================",
            file=sys.stderr)

    # The timer is a per-stage field, so it rides `stages` -- and a stage exists
    # only if some log carried its gnu-time line. ORFS' fused targets (e.g.
    # `1_3_floorplan_to_place`, Makefile:397) run several stage scripts in ONE
    # process under ONE log, so a metric resize.tcl emits keeps its `placeopt__`
    # prefix while `placeopt` never becomes a stage. That stage's whole QoR is lost
    # the same way and always has been -- but staying silent is not acceptable for
    # the one field whose ABSENCE is defined to mean "not timed".
    suffix = f"__{REPAIR_TIMING_KEY}"
    orphaned = sorted(k for k in meta
                      if k.endswith(suffix) and k[:-len(suffix)] not in summary["stages"])
    if orphaned:
        print(f"[WARN] {len(orphaned)} repair_timing timer(s) belong to a stage that "
              f"produced no gnu-time line and so is absent from `stages`: "
              f"{', '.join(orphaned)}. That stage's QoR is missing too -- the usual "
              f"cause is a fused ORFS target running several stage scripts in one "
              f"process. t_tool is NOT in this summary: do NOT read the missing "
              f"repair_timing_s as 'the call was not timed'.", file=sys.stderr)

    # Which machine, and honestly whose hostname it is (see host_record).
    summary["host"] = host_record(meta, md_full)

    # The gate-sizing engine's per-run records, lifted out of the stage logs before
    # they are discarded. null (not `injected: false`) when there were no logs to
    # scan: "we looked and the engine did not run" and "we never looked" are
    # different facts, and only the first is evidence about the run.
    summary["global_sizing"] = (scan_global_sizing(args.logs)
                                if args.logs and os.path.isdir(args.logs) else None)

    out_dir = os.path.dirname(os.path.abspath(args.output))
    os.makedirs(out_dir, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(summary, f, indent=1)

    fail = f" reason={summary['failure']['reason']}" if summary.get("failure") else ""
    # One grep-able line per run for "did my injection actually run": the preset the
    # engine echoed, `none` when it did not run, absent when there were no logs.
    gs = summary["global_sizing"]
    if gs is None:
        gs_note = ""
    elif gs["injected"]:
        gs_note = f" gs={gs['config']['fields'].get('preset', 'yes')}"
    else:
        gs_note = " gs=none"
    print(f"[genQorSummary] {summary['platform']}/{summary['design']}/{summary['variant']} "
          f"status={summary['status']} last_stage={summary['last_stage']} "
          f"final_stage={summary['final_stage']}"
          f"{'' if summary['completed'] else ' NOT-REACHED'} "
          f"stages={len(summary['stages'])} cores={summary['cores']} src={src}{gs_note}{fail}"
          f" -> {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
