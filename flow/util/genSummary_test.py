#!/usr/bin/env python3
"""Tests for genSummary.py's failure classification and per-run records.

The first subject is `scan_failure(logs_dir)`, which is the only source of the
`failure.reason` recorded in every metadata-summary.json -- and therefore the only
thing downstream analysis can use to tell "this run was REFUSED by the global-route
congestion gate" (recoverable by re-running with GLOBAL_ROUTE_ALLOW_CONGESTION=1)
from "this run crashed" (not recoverable). It reads a directory of `*.log` files,
so the fixtures below are synthetic logs written to a temp dir -- no ORFS run
needed.

The second subject is the summary a whole invocation produces: the endpoint
declaration (`--final-stage`), the in-tool `repair_timing` timer, the host record
and the gate-sizing engine records. `FlowFixture` runs genSummary.py as a
SUBPROCESS over a synthetic run tree, because that is how ORFS runs it -- an
in-process call would bypass argparse, the $GENSUMMARY_FINAL_STAGE default and
main()'s own wiring, which is precisely where the campaign bug lived.

Run either way::

    python3 flow/util/genSummary_test.py
    bazel test //flow/util:genSummary_test
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

import genSummary

GENSUMMARY_PY = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                             "genSummary.py")


# Verbatim OpenROAD error lines (utl logger format: 4-digit zero-padded code).
GRT_116 = ("[ERROR GRT-0116] Global routing finished with congestion. "
           "Check the congestion regions in the DRC Viewer.")
GRT_232 = ("[ERROR GRT-0232] Routing congestion too high. "
           "Check the congestion heatmap in the GUI.")
GRT_OTHER = "[ERROR GRT-0033] Net foo has invalid pin positions."
DPL_ERR = "[ERROR DPL-0036] Detailed placement failed on instance _1234_."
DRT_CONG = "[ERROR DRT-0033] Detailed routing congestion is too high."


class ScanFailureFixture(unittest.TestCase):
    """Writes {basename: [lines]} into a temp logs dir and scans it."""

    def scan(self, logs):
        with tempfile.TemporaryDirectory() as d:
            for name, lines in logs.items():
                with open(os.path.join(d, name), "w") as f:
                    f.write("\n".join(lines) + "\n")
            return genSummary.scan_failure(d)


class CongestionGateTests(ScanFailureFixture):
    """The gate must be distinguishable from every other global-route failure."""

    def test_grt_0116_is_the_congestion_gate(self):
        reason, msg, stage = self.scan({"5_1_grt.log": ["[INFO GRT-0096] ...", GRT_116]})
        self.assertEqual(reason, genSummary.GRT_CONGESTION_REASON)
        self.assertEqual(reason, "Global routing congestion")
        self.assertEqual(stage, "globalroute")
        self.assertIn("GRT-0116", msg)

    def test_grt_0232_incremental_gate_classifies_the_same(self):
        """-end_incremental raises a DIFFERENT code for the same condition."""
        reason, _msg, stage = self.scan({"5_1_grt.log": [GRT_232]})
        self.assertEqual(reason, genSummary.GRT_CONGESTION_REASON)
        self.assertEqual(stage, "globalroute")

    def test_both_codes_are_declared(self):
        self.assertEqual(genSummary.GRT_CONGESTION_CODES, ("GRT-0116", "GRT-0232"))

    def test_code_rules_precede_the_generic_word_match(self):
        """Both messages contain 'congestion', which the generic rule also matches;
        the code rules must win, or the gate is indistinguishable from detailed-route
        congestion."""
        pats = [p for p, _r in genSummary._FAIL_RULES]
        self.assertLess(pats.index("GRT-0116"), pats.index("congestion"))
        self.assertLess(pats.index("GRT-0232"), pats.index("congestion"))
        self.assertLess(pats.index("GRT-0232"), pats.index("GRT-"))


class NotTheCongestionGateTests(ScanFailureFixture):
    """Everything that must NOT be classified as the gate."""

    def test_other_grt_error_stays_global_routing(self):
        reason, _m, stage = self.scan({"5_1_grt.log": [GRT_OTHER]})
        self.assertEqual(reason, "Global routing")
        self.assertEqual(stage, "globalroute")

    def test_detailed_placement_failure(self):
        reason, _m, stage = self.scan({"3_5_place_dp.log": [DPL_ERR]})
        self.assertEqual(reason, "Detailed placement")
        self.assertEqual(stage, "detailedplace")

    def test_detailed_route_congestion_is_not_the_global_route_gate(self):
        reason, _m, _s = self.scan({"5_2_route.log": [DRT_CONG]})
        self.assertEqual(reason, "Routing congestion")
        self.assertNotEqual(reason, genSummary.GRT_CONGESTION_REASON)

    def test_clean_logs_report_nothing(self):
        reason, msg, stage = self.scan({"5_1_grt.log": ["[INFO GRT-0096] Routing ok."]})
        self.assertEqual((reason, msg, stage), ("Other", "", None))

    def test_out_of_memory_still_wins_on_its_own_line(self):
        reason, _m, _s = self.scan({"5_1_grt.log": ["terminate called: std::bad_alloc"]})
        self.assertEqual(reason, "Out of memory")


class LastErrorWinsTests(ScanFailureFixture):
    """scan_failure keeps the LAST error line in the furthest-along stage."""

    def test_furthest_stage_wins_over_an_earlier_one(self):
        reason, _m, stage = self.scan({"3_5_place_dp.log": [DPL_ERR],
                                       "5_1_grt.log": [GRT_116]})
        self.assertEqual(reason, genSummary.GRT_CONGESTION_REASON)
        self.assertEqual(stage, "globalroute")

    def test_last_line_within_a_log_wins(self):
        """A mid-stage GRT error followed by the gate reports the gate."""
        reason, _m, _s = self.scan({"5_1_grt.log": [GRT_OTHER, GRT_116]})
        self.assertEqual(reason, genSummary.GRT_CONGESTION_REASON)


# --- gate-sizing engine records, VERBATIM from real runs ------------------------
# Not hand-written from the fmt strings at policy/GlobalSizingPolicy.cc:83/627/609,
# lr/InitPass.cc:382 and lr/Termination.cc:441/471 -- copied out of the logs of
# nangate45/gcd runs that actually fired them (`-preset chinnery_partial
# -timing_scale livramento_alpha` for 0417/0443/0444/0450/0451, `-preset
# sharma_seq_partial` for 0445). Transcribing from a format string is how a parser
# ends up pinned to a message no engine emits: the two `reason` fields below are
# free-text phrases WITH SPACES ("TNS target met"), not the enum-ish tokens the
# fmt string's `{}` suggests.
RSZ_0417 = (
    "[INFO RSZ-0417] GLOBAL_SIZING config: preset=chinnery_partial init=as_given "
    "init_seed=0 seed=constant update=sharma_cexp mu_policy=endpoint_lambda "
    "mu_autopair=false projection=proportional_reverse_topo sweep=gauss_seidel_topo "
    "gs_refresh=gs_local traversal=forward_topo guard=local_slack_veto "
    "move_set=full_library fast_olr_start=5 timing_scale=livramento_alpha "
    "term=threshold_battery best=none | max_iter=80 beta=0.6 mu_exp=2 "
    "lambda_floor=1e-12 timing_bias=1 budget_safety=1 upsize_hyst=0 clock_net=false "
    "margin=0 | lambda_init=1 seed_exp=2 est_iters=3 update_c=1 flach_k=1/4/1 "
    "sharma_r=1.01 sharma_k=10 reimann_rho=0.05 reimann_k=1 reimann_ksched=5/0.5/2 "
    "reimann_setpoint=s_init livramento_alpha0=1 | cost_upstream_load=true "
    "cost_fanout_slew=true cost_global_phi=false cost_delta_delay=false "
    "| gamma_local_slack=0 stagnation=5/2/0/false near_met_gate=-1 "
    "chinnery=0.1/0.01/0.1/0.01/3/2.59e+05 best_tns_frac=0.1")
RSZ_0443 = ("[INFO RSZ-0443] GLOBAL_SIZING cap re-check: 0 move(s) reverted for "
            "creating a max-cap violation the sweep's frozen veto could not see; "
            "pass_bound_hit=false.")
RSZ_0444 = ("[INFO RSZ-0444] GLOBAL_SIZING timing_scale=livramento_alpha: terminal "
            "alpha=1.0534 (alpha0=1, 5 reschedules); alpha_floor_bound=false "
            "(floor=1e-12).")
RSZ_0445 = ("[INFO RSZ-0445] GLOBAL_SIZING: min_size_fixviol repair - 1 of 571 "
            "editable gates violated max-cap/max-slew after the min-size reset; "
            "1 cleared by upsizing, 0 still violating (no member of the "
            "swappable group clears them).")
RSZ_0450 = ("[INFO RSZ-0450] GLOBAL_SIZING threshold_battery: timing phase ended "
            "after 1 iteration(s) (TNS target met); handing over to the "
            "power-reduction phase [tns=0 wns=2.28306e-12 leakage=1.36527e-05 "
            "T=4.6e-10].")
RSZ_0451 = ("[INFO RSZ-0451] GLOBAL_SIZING threshold_battery: stopped after 5 "
            "iteration(s) (1 timing + 4 power) in the power phase (power "
            "improvement stalled) [tns=0 wns=2.27951e-12 leakage=1.33314e-05 "
            "T=4.6e-10].")

#: What resize.tcl's two emissions look like once utl has applied the stage
#: template `placeopt__{}` and genMetrics has written the stage's metrics JSON.
RESIZE_METRICS = {"3_4_place_resized.json": {
    "placeopt__repair_timing__wall_seconds": 12.5,
    "placeopt__host": "flow-node-7",
    "placeopt__timing__path__endpoint__count": 55,
}}


class FlowFixture(unittest.TestCase):
    """Builds a synthetic ORFS run tree and summarizes it the way ORFS does."""

    #: The gnu-time line the ORFS wrapper appends to every stage log. genSummary
    #: derives the stage list from these, so one per stage is what makes the stage
    #: exist at all.
    GNU_TIME = ("Elapsed time: 0:03.21[h:]min:sec. CPU time: user 3.10 sys 0.10 "
                "(100%). Peak memory: 102400KB.")

    #: A flow that stopped at globalroute -- the screening campaign's shape.
    TO_GLOBALROUTE = ["1_1_yosys.log", "2_1_floorplan.log", "3_3_place_gp.log",
                      "3_4_place_resized.log", "3_5_place_dp.log", "4_1_cts.log",
                      "5_1_grt.log"]
    STAGES_TO_GLOBALROUTE = ["synth", "floorplan", "globalplace", "placeopt",
                             "detailedplace", "cts", "globalroute"]

    def run_summary(self, logs=None, metrics=None, final_stage=None, env=None,
                    with_logs=True, stage_logs=None):
        """Write the run tree, run genSummary.py, return (summary, stderr).

        `logs` adds lines to a stage log (default: only the gnu-time line);
        `metrics` writes stage metrics JSONs; `final_stage` passes --final-stage;
        `env` overrides environment variables; `stage_logs` replaces the default
        set of logs written (for a fused ORFS target, which produces one log for
        several stages). $GENSUMMARY_FINAL_STAGE is scrubbed from the inherited
        environment first, or a developer who happens to export it would silently
        pass the very tests that exist to catch its absence."""
        logs = dict(logs or {})
        base = self.TO_GLOBALROUTE if stage_logs is None else list(stage_logs)
        with tempfile.TemporaryDirectory() as d:
            log_dir = os.path.join(d, "logs")
            os.makedirs(log_dir)
            for name in base + [n for n in logs if n not in base]:
                lines = logs.pop(name, [])
                with open(os.path.join(log_dir, name), "w") as f:
                    f.write("\n".join(lines + [self.GNU_TIME]) + "\n")
            for name, payload in (metrics or {}).items():
                with open(os.path.join(log_dir, name), "w") as f:
                    json.dump(payload, f)
            out = os.path.join(d, "metadata-summary.json")
            cmd = [sys.executable, GENSUMMARY_PY, "--output", out,
                   "--platform", "nangate45", "--design", "gcd", "--variant", "base"]
            if with_logs:
                cmd += ["--logs", log_dir]
            if final_stage:
                cmd += ["--final-stage", final_stage]
            child_env = {k: v for k, v in os.environ.items()
                         if k != "GENSUMMARY_FINAL_STAGE"}
            child_env.update(env or {})
            proc = subprocess.run(cmd, capture_output=True, text=True, env=child_env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            with open(out) as f:
                return json.load(f), proc.stderr


class EndpointDeclarationTests(FlowFixture):
    """A flow that stops where it was told to is not a failed run."""

    def test_the_campaign_shape_is_reproduced(self):
        """Verbatim: a run that stopped at globalroute on purpose, summarized the
        way the campaign invoked genSummary -- no --final-stage anywhere. This is
        the shape that put status='errored' on all 3120 rows."""
        s, _err = self.run_summary()
        self.assertEqual(s["last_stage"], "globalroute")
        self.assertEqual(s["final_stage"], "finish")   # scored against the default
        self.assertFalse(s["completed"])
        self.assertEqual(s["status"], "errored")

    def test_the_campaign_shape_no_longer_hides_under_Other(self):
        """Same run: it still reads incomplete (it IS, against `finish`), but its
        reason now names the condition instead of sharing 'Other' with genuinely
        unclassifiable failures."""
        s, _err = self.run_summary()
        self.assertEqual(s["failure"]["reason"], genSummary.NO_ERROR_REASON)
        self.assertNotEqual(s["failure"]["reason"], "Other")
        self.assertFalse(s["failure"]["congestion_gate"])

    def test_the_warning_names_the_fix(self):
        """The smell the tool can name: a loud warning carrying the exact
        invocation change, so this cannot silently recur."""
        _s, err = self.run_summary()
        self.assertIn("NO error line was found", err)
        self.assertIn("GENSUMMARY_FINAL_STAGE=globalroute", err)

    def test_declaring_the_endpoint_reads_ok_with_no_failure_block(self):
        s, err = self.run_summary(final_stage="globalroute")
        self.assertTrue(s["completed"])
        self.assertTrue(s["final_stage_reached"])
        self.assertEqual(s["status"], "ok")
        self.assertNotIn("failure", s)
        self.assertNotIn("NO error line was found", err)

    def test_the_environment_variable_declares_it_too(self):
        """The collector-config route: the campaign sets this, not a CLI flag."""
        s, _err = self.run_summary(env={"GENSUMMARY_FINAL_STAGE": "globalroute"})
        self.assertEqual(s["final_stage"], "globalroute")
        self.assertEqual(s["status"], "ok")

    def test_a_real_failure_keeps_its_own_reason(self):
        """The new reason must not swallow a failure the rules DO classify."""
        s, _err = self.run_summary(logs={"5_1_grt.log": [GRT_116]},
                                   final_stage="globalroute")
        self.assertEqual(s["status"], "errored")
        self.assertEqual(s["failure"]["reason"], genSummary.GRT_CONGESTION_REASON)
        self.assertTrue(s["failure"]["congestion_gate"])
        self.assertNotEqual(s["failure"]["reason"], genSummary.NO_ERROR_REASON)


class RepairTimingTimerTests(FlowFixture):
    """The in-tool t_tool channel emitted by resize.tcl."""

    def test_the_timer_lands_on_its_own_stage(self):
        s, _err = self.run_summary(final_stage="globalroute", metrics=RESIZE_METRICS)
        self.assertEqual(s["stages"]["placeopt"]["repair_timing_s"], 12.5)

    def test_it_does_not_mint_a_phantom_stage(self):
        """The metric is `__wall_seconds`, never `__elapsed_seconds`:
        stages_present() splits every key on the latter, so the wrong spelling
        would add a stage named 'placeopt__repair_timing' to stage_order, to
        `final`'s carry-forward scan and to the runtime totals."""
        s, _err = self.run_summary(final_stage="globalroute", metrics=RESIZE_METRICS)
        self.assertEqual(s["stage_order"], self.STAGES_TO_GLOBALROUTE)
        self.assertNotIn("placeopt__repair_timing", s["stages"])

    def test_it_is_a_resource_metric_not_QoR(self):
        """So it stays out of `final` (a QoR snapshot) and out of `totals` (which
        sums whole stages -- a sub-stage timer added there would double-count)."""
        s, _err = self.run_summary(final_stage="globalroute", metrics=RESIZE_METRICS)
        self.assertNotIn("repair_timing_s", s["final"])
        self.assertAlmostEqual(s["totals"]["runtime_s"],
                               3.21 * len(self.STAGES_TO_GLOBALROUTE), places=6)

    def test_absent_where_no_call_was_timed(self):
        """A missing key reads 'not timed', never 'timed, and it took zero'."""
        s, _err = self.run_summary(final_stage="globalroute", metrics=RESIZE_METRICS)
        self.assertNotIn("repair_timing_s", s["stages"]["cts"])

    def test_a_timer_whose_stage_never_materialized_is_loud(self):
        """A fused ORFS target (1_3_floorplan_to_place) runs several stage scripts
        in one process under one log, so `placeopt` never becomes a stage while the
        metric keeps its `placeopt__` prefix. The timer is then genuinely absent --
        and since a missing key is DEFINED to mean 'not timed', the loss has to be
        said out loud rather than inferred."""
        s, err = self.run_summary(
            final_stage="cts",
            stage_logs=["1_3_floorplan_to_place.log", "4_1_cts.log"],
            metrics={"1_3_floorplan_to_place.json": {
                "placeopt__repair_timing__wall_seconds": 12.5}})
        self.assertNotIn("placeopt", s["stages"])
        self.assertIn("do NOT read the missing repair_timing_s", err)
        self.assertIn("placeopt__repair_timing__wall_seconds", err)


class HostTests(FlowFixture):
    """Best-effort machine identity, with its source stamped."""

    def test_the_flow_host_wins_and_says_it_is_the_flow_host(self):
        s, _err = self.run_summary(final_stage="globalroute", metrics=RESIZE_METRICS)
        self.assertEqual(s["host"], {"id": "flow-node-7", "source": "flow"})

    def test_the_fallback_admits_it_is_this_process(self):
        """genSummary often runs on a different machine than the flow, so the
        fallback must be labelled, not silently passed off as the flow's host."""
        s, _err = self.run_summary(final_stage="globalroute")
        self.assertEqual(s["host"]["source"], "gensummary")
        self.assertEqual(s["host"]["id"], socket.gethostname())


class GlobalSizingRecordTests(FlowFixture):
    """The engine's per-run records, lifted out of logs that are then discarded."""

    ALL_RECORDS = {"3_4_place_resized.log": [RSZ_0417, RSZ_0443, RSZ_0444,
                                             RSZ_0445, RSZ_0450, RSZ_0451]}

    def summarize_records(self):
        s, _err = self.run_summary(final_stage="globalroute",
                                   logs=dict(self.ALL_RECORDS))
        return s["global_sizing"]

    def test_a_stock_run_reads_not_injected_and_is_not_an_error(self):
        """No injection is the normal state of stock ORFS. It must be readable at a
        glance and must not fabricate a failure."""
        s, _err = self.run_summary(final_stage="globalroute")
        self.assertFalse(s["global_sizing"]["injected"])
        self.assertIsNone(s["global_sizing"]["config"])
        self.assertEqual(s["status"], "ok")
        self.assertNotIn("failure", s)

    def test_no_logs_is_unknown_not_not_injected(self):
        """'we looked and it did not run' and 'we never looked' are different
        facts; only the first is evidence about the run."""
        s, _err = self.run_summary(with_logs=False)
        self.assertIsNone(s["global_sizing"])

    def test_every_record_is_captured_with_its_raw_line_and_stage(self):
        gs = self.summarize_records()
        self.assertTrue(gs["injected"])
        for name in genSummary.GS_RECORDS:
            self.assertIsNotNone(gs[name], name)
            self.assertEqual(gs[name]["stage"], "placeopt", name)
            self.assertIn(genSummary.GS_RECORDS[name], gs[name]["raw"], name)

    def test_the_config_echo_is_kept_verbatim_and_parsed(self):
        gs = self.summarize_records()
        self.assertEqual(gs["config"]["raw"], RSZ_0417)
        fields = gs["config"]["fields"]
        self.assertEqual(fields["preset"], "chinnery_partial")
        # the iteration-2 spellings: `init=`/`init_seed=` replaced `presize=`
        self.assertEqual(fields["init"], "as_given")
        self.assertEqual(fields["init_seed"], "0")
        self.assertEqual(fields["max_iter"], "80")
        self.assertEqual(fields["flach_k"], "1/4/1")
        self.assertEqual(fields["lambda_floor"], "1e-12")
        self.assertEqual(fields["chinnery"], "0.1/0.01/0.1/0.01/3/2.59e+05")

    def test_cap_recheck_counts(self):
        rec = self.summarize_records()["cap_recheck"]
        self.assertEqual(rec["fields"]["reverted"], "0")
        self.assertEqual(rec["fields"]["pass_bound_hit"], "false")

    def test_terminal_alpha(self):
        f = self.summarize_records()["terminal_alpha"]["fields"]
        self.assertEqual(f["alpha"], "1.0534")
        self.assertEqual(f["alpha0"], "1")          # trailing ',' stripped
        self.assertEqual(f["reschedules"], "5")
        self.assertEqual(f["alpha_floor_bound"], "false")
        self.assertEqual(f["floor"], "1e-12")       # trailing ').' stripped

    def test_fixviol_repair_counts(self):
        f = self.summarize_records()["fixviol"]["fields"]
        self.assertEqual((f["violating"], f["editable"], f["cleared"],
                          f["still_violating"]), ("1", "571", "1", "0"))

    def test_battery_handover_carries_the_qor_at_the_boundary(self):
        f = self.summarize_records()["battery_handover"]["fields"]
        self.assertEqual(f["timing_iters"], "1")
        # free text with spaces -- the reason group must not stop at the first word
        self.assertEqual(f["reason"], "TNS target met")
        self.assertEqual((f["tns"], f["wns"], f["leakage"], f["T"]),
                         ("0", "2.28306e-12", "1.36527e-05", "4.6e-10"))

    def test_battery_stop_carries_the_phase_split(self):
        f = self.summarize_records()["battery_stop"]["fields"]
        self.assertEqual((f["iters"], f["timing_iters"], f["power_iters"]),
                         ("5", "1", "4"))
        self.assertEqual(f["phase"], "power")
        self.assertEqual(f["reason"], "power improvement stalled")

    def test_a_displaced_record_is_visible_in_stages(self):
        """One slot per code + last-wins means a later stage's RSZ-0417 displaces
        the sizing stage's. `stages` listing both is the only way a reader can tell
        that happened -- the logs it could otherwise be recovered from are gone."""
        s, _err = self.run_summary(final_stage="globalroute",
                                   logs={"3_4_place_resized.log": [RSZ_0417],
                                         "4_1_cts.log": [RSZ_0417]})
        gs = s["global_sizing"]
        self.assertEqual(gs["stages"], ["placeopt", "cts"])   # flow order
        self.assertEqual(gs["config"]["stage"], "cts")        # the survivor

    def test_stages_is_empty_when_nothing_was_found(self):
        s, _err = self.run_summary(final_stage="globalroute")
        self.assertEqual(s["global_sizing"]["stages"], [])

    def test_records_are_found_wherever_repair_timing_ran(self):
        """`repair_timing` is called from several stage scripts, so the scan is not
        hard-coded to one of them and the stage is reported rather than assumed."""
        s, _err = self.run_summary(final_stage="globalroute",
                                   logs={"4_1_cts.log": [RSZ_0417]})
        self.assertTrue(s["global_sizing"]["injected"])
        self.assertEqual(s["global_sizing"]["config"]["stage"], "cts")

    def test_an_unparseable_record_still_keeps_its_raw_line(self):
        """Format drift must cost the parsed fields and nothing more."""
        s, _err = self.run_summary(
            final_stage="globalroute",
            logs={"3_4_place_resized.log": ["[INFO RSZ-0445] GLOBAL_SIZING: "
                                            "reworded beyond recognition"]})
        rec = s["global_sizing"]["fixviol"]
        self.assertIn("reworded beyond recognition", rec["raw"])
        self.assertEqual(rec["fields"], {})


class ParseGsFieldsTests(unittest.TestCase):
    """The key=value scan is deliberately generic, so new engine tokens appear
    without a parser change; message punctuation must not ride along."""

    def test_trailing_message_punctuation_is_stripped(self):
        f = genSummary.parse_gs_fields("x [tns=-1.1 T=0.34]. (floor=0.001). a=b:")
        self.assertEqual(f, {"tns": "-1.1", "T": "0.34", "floor": "0.001", "a": "b"})

    def test_slash_bundles_and_exponents_survive_intact(self):
        f = genSummary.parse_gs_fields("flach_k=1.1/1.05/1.01 lambda_floor=1e-06")
        self.assertEqual(f, {"flach_k": "1.1/1.05/1.01", "lambda_floor": "1e-06"})

    def test_an_unknown_future_token_needs_no_parser_change(self):
        self.assertEqual(genSummary.parse_gs_fields("brand_new_knob=42"),
                         {"brand_new_knob": "42"})

    def test_the_record_name_makes_this_the_whole_parser(self):
        """The prose counts merge here, not in scan_global_sizing's caller, so
        re-parsing an ARCHIVED `raw` line reproduces exactly the `fields` the
        collector stored -- the summaries outlive the logs, so that has to hold."""
        f = genSummary.parse_gs_fields(RSZ_0451, "battery_stop")
        self.assertEqual(f["timing_iters"], "1")           # from the prose
        self.assertEqual(f["tns"], "0")                    # from key=value
        # and without the record name only the key=value half is parsed
        self.assertNotIn("timing_iters", genSummary.parse_gs_fields(RSZ_0451))


if __name__ == "__main__":
    unittest.main()
