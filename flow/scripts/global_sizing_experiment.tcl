# Global sizing experiments, selected by EXPERIMENT_SETTING.
#
# detail_place.tcl sources this file after detailed placement and before
# report_metrics, so the detailed-placement metrics record the design after
# sizing. Each setting is {set_global_sizing_config arguments} {greedy}:
# the arguments are empty when the setting runs no global sizing, and greedy
# is 1 when OpenROAD's greedy sizing follows (setup repair with cell-swap
# moves only, then power recovery). A setting with neither runs nothing here.

# --- settings table (generated; do not edit by hand) ---
set gs_experiment_settings [dict create \
  S1-0 [list {-preset rsz_baseline} 0] \
  S1-N1 [list {-preset rsz_baseline -init_mode min_size} 0] \
  S1-N2 [list {-preset rsz_baseline -init_mode max_size} 0] \
  S1-N3 [list {-preset rsz_baseline -init_mode min_size_fixviol} 0] \
  S1-N4 [list {-preset rsz_baseline -init_mode random -init_seed 1} 0] \
  S1-N5 [list {-preset rsz_baseline -init_mode average} 0] \
  S1-D1 [list {-preset rsz_baseline -lambda_seed constant} 0] \
  S1-D2 [list {-preset rsz_baseline -lambda_seed state_adaptive} 0] \
  S1-D3 [list {-preset rsz_baseline -lambda_seed estimation_loop} 0] \
  S1-E1 [list {-preset rsz_baseline -lambda_update flach_slack_scaling} 0] \
  S1-E2 [list {-preset rsz_baseline -lambda_update chen_subgradient} 0] \
  S1-E4 [list {-preset rsz_baseline -lambda_update sharma_cexp} 0] \
  S1-E5 [list {-preset rsz_baseline -lambda_update reimann_dwns -reimann_setpoint s_init} 0] \
  S1-F1 [list {-preset rsz_baseline -sweep_engine gauss_seidel_topo} 0] \
  S1-F2a [list {-preset rsz_baseline -sweep_engine gauss_seidel_topo -traversal
    reverse_topo} 0] \
  S1-F2b [list {-preset rsz_baseline -sweep_engine gauss_seidel_topo -traversal
    criticality_sorted} 0] \
  S1-F2c [list {-preset rsz_baseline -sweep_engine gauss_seidel_topo -gs_refresh
    gs_incremental} 0] \
  S1-G1 [list {-preset rsz_baseline -downsize_guard local_slack_veto} 0] \
  S1-G2 [list {-preset rsz_baseline -downsize_guard none} 0] \
  S1-H1 [list {-preset rsz_baseline -upsize_hysteresis 0.0} 0] \
  S1-I1 [list {-preset rsz_baseline -max_iterations 40} 0] \
  S1-I2 [list {-preset rsz_baseline -max_iterations 80} 0] \
  S1-I3 [list {-preset rsz_baseline -max_iterations 160} 0] \
  S1-K1 [list {-preset rsz_baseline -termination stagnation_windows} 0] \
  S1-K2 [list {-preset rsz_baseline -termination threshold_battery} 0] \
  S1-O1 [list {-preset rsz_baseline -move_set sharma_fast_olr} 0] \
  S1-O2 [list {-preset rsz_baseline -move_set mangiras_size_step} 0] \
  S1-R1 [list {-preset rsz_baseline -best_tracker none} 0] \
  S1-R2 [list {-preset rsz_baseline -best_tracker flach_dominance} 0] \
  S1-R3 [list {-preset rsz_baseline -best_tracker reimann_score} 0] \
  S1-T1 [list {-preset rsz_baseline -timing_bias 12} 0] \
  S1-T2 [list {-preset rsz_baseline -timing_bias 1} 0] \
  S1-A1 [list {-preset rsz_baseline -timing_scale livramento_alpha} 0] \
  S1-B1 [list {-preset rsz_baseline -cost_fanout_slew 1} 0] \
  S1-B2 [list {-preset rsz_baseline -cost_global_phi 1} 0] \
  S1-B3 [list {-preset rsz_baseline -cost_upstream_load 0} 0] \
  S1-V1 [list {-preset rsz_baseline -output_drc_veto relative} 0] \
  S1-M1 [list {-preset rsz_baseline -mu_policy seed_once} 0] \
  S1-M2 [list {-preset rsz_baseline -mu_policy update_as_lambda} 0] \
  S1-M3 [list {-preset rsz_baseline -mu_policy endpoint_lambda} 0] \
  S1-M4 [list {-preset rsz_baseline -mu_policy endpoint_ratio} 0] \
  S1-M5 [list {-preset rsz_baseline -mu_policy endpoint_additive} 0] \
  S1-G3 [list {-preset rsz_baseline -downsize_guard local_slack_veto -near_met_gate_frac
    0.01} 0] \
  S1-Q1 [list {-preset rsz_baseline -size_registers 0} 0] \
  S1-P1 [list {-preset rsz_baseline -power_objective total} 0] \
  S1-B4 [list {-preset rsz_baseline -timing_cost per_arc} 0] \
  S1-L1 [list {-preset rsz_baseline -max_inner_sweeps 10} 0] \
  S1-E7 [list {-preset rsz_baseline -lambda_update sharma_arc_slack -termination
    threshold_battery -power_phase_filter 1} 0] \
  S2-A [list {} 0] \
  S2-S [list {-preset rsz_baseline} 0] \
  S2-Chen [list {-preset chen_partial} 0] \
  S2-Tennakoon [list {-preset tennakoon_partial} 0] \
  S2-Livramento [list {-preset livramento_partial} 0] \
  S2-Flach [list {-preset flach_partial} 0] \
  S2-Sharma [list {-preset sharma_seq_partial} 0] \
  S2-Reimann [list {-preset reimann_partial} 0] \
  S2-Mangiras [list {-preset mangiras_partial} 0] \
  S2-Chinnery [list {-preset chinnery_partial} 0] \
  S2-Cc [list {} 1] \
  S2-D-stock [list {-preset rsz_baseline} 1]]
# --- end settings table ---

# The sub-block builds of the BLOCKS designs run detail_place.tcl too. The
# experiment runs only in the top-level build, so every setting shares the
# sub-blocks' state.
set gs_experiment_sub_blocks {
  aes-block_aes_rcon aes-block_aes_sbox riscv32i-mock-sram_fakeram7_256x32
  uart-blocks_uart_rx i2c-gpio-expander_I2cDeviceCtrl
}

proc run_global_sizing_experiment { setting } {
  global gs_experiment_settings gs_experiment_sub_blocks
  if { ![dict exists $gs_experiment_settings $setting] } {
    utl::error FLW 26 "Unknown EXPERIMENT_SETTING \"$setting\"."
  }
  lassign [dict get $gs_experiment_settings $setting] gs_args greedy
  if { [lsearch -exact $gs_experiment_sub_blocks $::env(DESIGN_NICKNAME)] >= 0 } {
    utl::info FLW 27 "EXPERIMENT_SETTING $setting: skipped in the sub-block\
      build $::env(DESIGN_NICKNAME)."
    return
  }
  if { $gs_args eq "" && !$greedy } {
    utl::info FLW 28 "EXPERIMENT_SETTING $setting: no sizing at this step."
    return
  }
  utl::info FLW 29 "EXPERIMENT_SETTING $setting."

  # ORFS applies the platform dont-use list in every step that sizes cells
  # except this one, and set_dont_use is not stored in the ODB.
  if { [env_var_exists_and_non_empty DONT_USE_CELLS] } {
    set_dont_use $::env(DONT_USE_CELLS)
  }

  # In-tool timer around all sizing at this step. `clock` is not guaranteed in
  # a hermetic environment, so a missing `clock` costs the metric rather than
  # erroring the stage. The metric name must not end in __elapsed_seconds:
  # genSummary derives the stage list from that suffix.
  set has_clock [expr { [info commands clock] ne "" }]
  if { $has_clock } { set gs_start_ms [clock milliseconds] }

  if { $gs_args ne "" } {
    log_cmd set_global_sizing_config {*}$gs_args
    repair_timing_helper -setup -phases "GLOBAL_SIZING"
  }
  if { $greedy } {
    # Called directly, not through repair_timing_helper, so no environment
    # variable can change the move list. Only the platform's footprint rule is
    # passed on, as the helpers (and so the global sizing call above) pass it.
    set footprint {}
    append_env_var footprint MATCH_CELL_FOOTPRINT -match_cell_footprint 0
    log_cmd repair_timing -setup -sequence "vt_swap sizeup size_down_fanout" \
      -skip_pin_swap -skip_gate_cloning -skip_buffering -skip_buffer_removal \
      {*}$footprint -verbose
    log_cmd repair_timing -recover_power 100 {*}$footprint -verbose
  }

  if { $has_clock } {
    utl::metric_float "repair_timing__wall_seconds" \
      [expr { ([clock milliseconds] - $gs_start_ms) / 1000.0 }]
  }
}
