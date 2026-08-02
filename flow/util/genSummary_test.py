#!/usr/bin/env python3
"""Tests for genSummary.py's failure classification.

The subject is `scan_failure(logs_dir)`, which is the only source of the
`failure.reason` recorded in every metadata-summary.json -- and therefore the only
thing downstream analysis can use to tell "this run was REFUSED by the global-route
congestion gate" (recoverable by re-running with GLOBAL_ROUTE_ALLOW_CONGESTION=1)
from "this run crashed" (not recoverable). It reads a directory of `*.log` files,
so the fixtures below are synthetic logs written to a temp dir -- no ORFS run
needed.

Run either way::

    python3 flow/util/genSummary_test.py
    bazel test //flow/util:genSummary_test
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

import genSummary


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


if __name__ == "__main__":
    unittest.main()
