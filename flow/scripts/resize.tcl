utl::set_metrics_stage "placeopt__{}"
source $::env(SCRIPTS_DIR)/load.tcl
erase_non_stage_variables place
load_design 3_3_place_gp.odb 2_floorplan.sdc
source_step_tcl PRE RESIZE

log_cmd estimate_parasitics -placement

set instance_count_before [sta::network_leaf_instance_count]
set pin_count_before [sta::network_leaf_pin_count]

set_dont_use $::env(DONT_USE_CELLS)

if { [env_var_exists_and_non_empty EARLY_SIZING_CAP_RATIO] } {
  log_cmd set_opt_config -set_early_sizing_cap_ratio $env(EARLY_SIZING_CAP_RATIO)
}

if { [env_var_exists_and_non_empty SWAP_ARITH_OPERATORS] } {
  # Enable sanity checker until replace_arith_modules becomes stable
  set_debug_level ODB replace_design_check_sanity 1
  replace_arith_modules
  global_placement -incremental
}

repair_design_helper

if { $::env(ENABLE_PLACE_REPAIR_TIMING) } {
  # Repair timing using placement parasitics.
  puts "Repair setup and hold violations..."
  log_cmd estimate_parasitics -placement

  # Wall time of THIS repair_timing call, as a metric so it lands per run in
  # metadata-summary.json. It is the in-tool timer (t_tool): the stage's own
  # elapsed_seconds also covers repair_design, the parasitics estimates and the
  # reports, and the whole-flow total is dominated by host load. cts.tcl and
  # global_route.tcl call the same helper and are deliberately NOT timed -- they
  # do identical downstream work whatever this call did, so timing them would add
  # noise, not signal. Arm-independent: emitted for every run that reaches this
  # call, injected or not, so a stock run is measured on exactly the same channel
  # as a sized one. (The enclosing ENABLE_PLACE_REPAIR_TIMING gate is no exception
  # to that -- when it is 0 there is no call here to time in the first place.)
  #
  # The metric name must NOT end in `__elapsed_seconds`: genSummary derives the
  # flow's stage list from every key carrying that suffix, so such a name would
  # mint a bogus stage. `clock` is auto-loaded from TCL_LIBRARY and is not
  # guaranteed in a hermetic environment (the same caveat log_cmd carries), so a
  # missing `clock` costs the metric rather than erroring the stage. Tcl exposes
  # no monotonic clock, so a wall-clock step mid-call lands in the value verbatim;
  # left unclamped deliberately -- an obviously bogus number is easier to notice
  # and drop than a plausible 0 manufactured to hide one.
  set has_clock [expr { [info commands clock] ne "" }]
  if { $has_clock } {
    set repair_timing_start_ms [clock milliseconds]
  }

  repair_timing_helper

  if { $has_clock } {
    utl::metric_float "repair_timing__wall_seconds" \
      [expr { ([clock milliseconds] - $repair_timing_start_ms) / 1000.0 }]
  }
}

# The machine that RAN THE FLOW. genSummary may summarize somewhere else entirely
# (the summaries are collected from a bucket), so its own hostname answers a
# different question; captured here it is unambiguous. Best-effort provenance for
# post-hoc runtime stratification -- never a scoring input.
#
# OPT-IN, and it has to be: a metric emitted here reaches metadata.json, so an
# always-on hostname would be written into every regenerated metadata golden --
# committing contributor machine names and churning the goldens for everyone. With
# it off, genSummary falls back to its own hostname and stamps host.source
# "gensummary" instead of "flow", so the ambiguity stays visible rather than hidden.
if { $::env(RECORD_FLOW_HOST) } {
  utl::metric "host" [info hostname]
}

# post report

puts "Floating nets: "
report_floating_nets

report_metrics 3 "resizer" true false

puts "Instance count before $instance_count_before, after [sta::network_leaf_instance_count]"
puts "Pin count before $pin_count_before, after [sta::network_leaf_pin_count]"

source_step_tcl POST RESIZE

orfs_write_db $::env(RESULTS_DIR)/3_4_place_resized.odb
