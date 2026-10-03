proc report_puts { out } {
  upvar 1 when when
  upvar 1 filename filename
  set fileId [open $filename a]
  puts $fileId $out
  close $fileId
}

# Emit the size of the max slew, capacitance and fanout violations: per check,
# the sum over violating pins of (value - limit) / limit. Unitless, 0 when nothing
# violates. erc_text is the output of `report_check_types -max_slew
# -max_capacitance -max_fanout -violators`; with -violators every violating pin
# is listed (-max_count only applies without it), one row per pin, worst over
# scenes and rise/fall, so the sums use the same limits as the violation counts.
# counts maps each check to its STA violation count. A check whose parsed row
# count differs is warned about and not emitted, so a change in the report
# layout reads as a missing metric, never as zero.
proc report_drv_excess_metrics { erc_text counts } {
  set num {-?[0-9]+(?:\.[0-9]*)?(?:[eE][-+]?[0-9]+)?}
  # Row: pin limit value slack (VIOLATED). The slack is optional because STA
  # prints a value of exactly -1 as blank (a fanout slack of -1 does show up
  # blank). \s+ also spans a line split between a long pin name and its numbers.
  set row_re "(\\S+)\\s+($num)\\s+($num)(?:\\s+$num)?\\s+\\(VIOLATED\\)"
  set headers [regexp -all -inline -indices -line \
    {^max (slew|capacitance|fanout)$} $erc_text]
  set sums [dict create slew 0.0 capacitance 0.0 fanout 0.0]
  set rows [dict create slew 0 capacitance 0 fanout 0]
  set n [llength $headers]
  for { set i 0 } { $i < $n } { incr i 2 } {
    set start [expr { [lindex $headers $i 1] + 1 }]
    set check [string range $erc_text {*}[lindex $headers [expr { $i + 1 }]]]
    if { $i + 2 < $n } {
      set end [expr { [lindex $headers [expr { $i + 2 }] 0] - 1 }]
    } else {
      set end end
    }
    set section [string range $erc_text $start $end]
    foreach { _ pin limit value } [regexp -all -inline $row_re $section] {
      if { $limit > 0 } {
        # double(): fanout rows are integers, and Tcl would divide them as such.
        set excess [expr { double($value - $limit) / $limit }]
        dict set sums $check [expr { [dict get $sums $check] + $excess }]
        dict incr rows $check
      }
    }
  }
  foreach { check metric } { slew max_slew capacitance max_cap fanout max_fanout } {
    set count [dict get $counts $check]
    set parsed [dict get $rows $check]
    if { $parsed != $count } {
      utl::warn FLW 25 "max $check: parsed $parsed violating pins from\
        report_check_types but STA counts $count; timing__drv__${metric}_excess\
        not emitted."
      continue
    }
    utl::metric_float "timing__drv__${metric}_excess" [dict get $sums $check]
  }
}

proc report_metrics { stage when { include_erc true } { include_clock_skew true } } {
  if { $::env(SKIP_REPORT_METRICS) } {
    return
  }
  puts "Report metrics stage $stage, $when..."
  set filename $::env(REPORTS_DIR)/${stage}_[string map {" " "_"} $when].rpt
  set fileId [open $filename w]
  close $fileId
  report_puts "\n=========================================================================="
  report_puts "$when report_tns"
  report_puts "--------------------------------------------------------------------------"
  report_tns >> $filename
  report_tns_metric
  report_tns_metric -hold

  report_puts "\n=========================================================================="
  report_puts "$when report_wns"
  report_puts "--------------------------------------------------------------------------"
  report_wns >> $filename

  report_puts "\n=========================================================================="
  report_puts "$when report_worst_slack"
  report_puts "--------------------------------------------------------------------------"
  report_worst_slack >> $filename
  report_worst_slack_metric
  report_worst_slack_metric -hold

  utl::metric_int "timing__path__endpoint__count" [sta::endpoint_count]

  report_puts "\n=========================================================================="
  report_puts "$when report_clock_min_period"
  report_puts "--------------------------------------------------------------------------"
  report_clock_min_period -include_port_paths >> $filename
  report_fmax_metric

  if { $include_clock_skew && $::env(REPORT_CLOCK_SKEW) } {
    report_puts "\n=========================================================================="
    report_puts "$when report_clock_skew"
    report_puts "--------------------------------------------------------------------------"
    report_clock_skew >> $filename
    report_clock_skew_metric
    report_clock_skew_metric -hold
  }

  report_puts "\n=========================================================================="
  report_puts "$when report_checks -path_delay min"
  report_puts "--------------------------------------------------------------------------"
  report_checks -path_delay min -fields {slew cap input net fanout} \
    -format full_clock_expanded >> $filename

  report_puts "\n=========================================================================="
  report_puts "$when report_checks -path_delay max"
  report_puts "--------------------------------------------------------------------------"
  report_checks -path_delay max -fields {slew cap input net fanout} \
    -format full_clock_expanded >> $filename

  report_puts "\n=========================================================================="
  report_puts "$when report_checks -unconstrained"
  report_puts "--------------------------------------------------------------------------"
  report_checks -unconstrained -fields {slew cap input net fanout} \
    -format full_clock_expanded >> $filename

  if { $include_erc } {
    report_puts "\n=========================================================================="
    report_puts "$when report_check_types -max_slew -max_cap -max_fanout -violators"
    report_puts "--------------------------------------------------------------------------"
    # Run the check once and keep its text: it goes to the report file as before
    # and its per-pin rows give the violation sizes (report_drv_excess_metrics).
    # -digits 6 because the default 2 rounds a slew in ns to 10 ps, too coarse
    # for (value - limit) / limit.
    sta::redirect_string_begin
    set rc [catch {
      report_check_types -max_slew -max_capacitance -max_fanout -violators -digits 6
    } msg opts]
    set erc_text [sta::redirect_string_end]
    # -digits sets the report digits globally; put the default back.
    sta::set_report_path_digits $::sta_report_default_digits
    if { $rc } {
      return -options $opts $msg
    }
    set fileId [open $filename a]
    puts -nonewline $fileId $erc_text
    close $fileId

    report_erc_metrics

    report_puts "\n=========================================================================="
    report_puts "$when max_slew_check_slack"
    report_puts "--------------------------------------------------------------------------"
    report_puts "[sta::max_slew_check_slack]"

    report_puts "\n=========================================================================="
    report_puts "$when max_slew_check_limit"
    report_puts "--------------------------------------------------------------------------"
    report_puts "[sta::max_slew_check_limit]"

    if { [sta::max_slew_check_limit] < 1e30 } {
      report_puts "\n=========================================================================="
      report_puts "$when max_slew_check_slack_limit"
      report_puts "--------------------------------------------------------------------------"
      report_puts [format "%.4f" [sta::max_slew_check_slack_limit]]
    }

    report_puts "\n=========================================================================="
    report_puts "$when max_fanout_check_slack"
    report_puts "--------------------------------------------------------------------------"
    report_puts "[sta::max_fanout_check_slack]"

    report_puts "\n=========================================================================="
    report_puts "$when max_fanout_check_limit"
    report_puts "--------------------------------------------------------------------------"
    report_puts "[sta::max_fanout_check_limit]"

    if { [sta::max_fanout_check_limit] < 1e30 } {
      report_puts "\n=========================================================================="
      report_puts "$when max_fanout_check_slack_limit"
      report_puts "--------------------------------------------------------------------------"
      report_puts [format "%.4f" [sta::max_fanout_check_slack_limit]]
    }

    report_puts "\n=========================================================================="
    report_puts "$when max_capacitance_check_slack"
    report_puts "--------------------------------------------------------------------------"
    report_puts "[sta::max_capacitance_check_slack]"

    report_puts "\n=========================================================================="
    report_puts "$when max_capacitance_check_limit"
    report_puts "--------------------------------------------------------------------------"
    report_puts "[sta::max_capacitance_check_limit]"

    if { [sta::max_capacitance_check_limit] < 1e30 } {
      report_puts "\n=========================================================================="
      report_puts "$when max_capacitance_check_slack_limit"
      report_puts "--------------------------------------------------------------------------"
      report_puts [format "%.4f" [sta::max_capacitance_check_slack_limit]]
    }

    report_puts "\n=========================================================================="
    report_puts "$when max_slew_violation_count"
    report_puts "--------------------------------------------------------------------------"
    set max_slew_count [sta::max_slew_violation_count]
    report_puts "max slew violation count $max_slew_count"

    report_puts "\n=========================================================================="
    report_puts "$when max_fanout_violation_count"
    report_puts "--------------------------------------------------------------------------"
    set max_fanout_count [sta::max_fanout_violation_count]
    report_puts "max fanout violation count $max_fanout_count"

    report_puts "\n=========================================================================="
    report_puts "$when max_cap_violation_count"
    report_puts "--------------------------------------------------------------------------"
    set max_cap_count [sta::max_capacitance_violation_count]
    report_puts "max cap violation count $max_cap_count"

    report_drv_excess_metrics $erc_text [dict create slew $max_slew_count \
      capacitance $max_cap_count fanout $max_fanout_count]

    report_puts "\n=========================================================================="
    report_puts "$when setup_violation_count"
    report_puts "--------------------------------------------------------------------------"
    report_puts "setup violation count [sta::endpoint_violation_count max]"

    report_puts "\n=========================================================================="
    report_puts "$when hold_violation_count"
    report_puts "--------------------------------------------------------------------------"
    report_puts "hold violation count [sta::endpoint_violation_count min]"

    set critical_path [lindex [find_timing_paths -sort_by_slack] 0]
    if { $critical_path != "" } {
      set path_delay [sta::format_time [[$critical_path path] arrival] 4]
      set path_slack [sta::format_time [[$critical_path path] slack] 4]
    } else {
      set path_delay -1
      set path_slack 0
    }

    if { [llength [all_registers]] != 0 } {
      report_puts "\n=========================================================================="
      report_puts "$when report_checks -path_delay max reg to reg"
      report_puts "--------------------------------------------------------------------------"
      report_checks -path_delay max -from [all_registers] -to [all_registers] \
        -format full_clock_expanded >> $filename
      report_puts "\n=========================================================================="
      report_puts "$when report_checks -path_delay min reg to reg"
      report_puts "--------------------------------------------------------------------------"
      report_checks -path_delay min -from [all_registers] -to [all_registers] \
        -format full_clock_expanded >> $filename

      set inp_to_reg_critical_path \
        [lindex [find_timing_paths -path_delay max -from [all_inputs] -to [all_registers]] 0]
      if { $inp_to_reg_critical_path != "" } {
        set target_clock_latency_max \
          [sta::format_time [$inp_to_reg_critical_path target_clk_delay] 4]
      } else {
        set target_clock_latency_max 0
      }


      set inp_to_reg_critical_path [lindex \
        [find_timing_paths -path_delay min -from [all_inputs] -to [all_registers]] 0]
      if { $inp_to_reg_critical_path != "" } {
        set target_clock_latency_min \
          [sta::format_time [$inp_to_reg_critical_path target_clk_delay] 4]
        set source_clock_latency [sta::format_time [$inp_to_reg_critical_path source_clk_latency] 4]
      } else {
        set target_clock_latency_min 0
        set source_clock_latency 0
      }

      report_puts "\n=========================================================================="
      report_puts "$when critical path target clock latency max path"
      report_puts "--------------------------------------------------------------------------"
      report_puts "$target_clock_latency_max"

      report_puts "\n=========================================================================="
      report_puts "$when critical path target clock latency min path"
      report_puts "--------------------------------------------------------------------------"
      report_puts "$target_clock_latency_min"

      report_puts "\n=========================================================================="
      report_puts "$when critical path source clock latency min path"
      report_puts "--------------------------------------------------------------------------"
      report_puts "$source_clock_latency"
    } else {
      puts "No registers in design"
    }
    # end if all_registers

    report_puts "\n=========================================================================="
    report_puts "$when critical path delay"
    report_puts "--------------------------------------------------------------------------"
    report_puts "$path_delay"

    report_puts "\n=========================================================================="
    report_puts "$when critical path slack"
    report_puts "--------------------------------------------------------------------------"
    report_puts "$path_slack"

    report_puts "\n=========================================================================="
    report_puts "$when slack div critical path delay"
    report_puts "--------------------------------------------------------------------------"
    if { $path_delay != 0.0 } {
      report_puts "[format "%4f" [expr $path_slack / $path_delay * 100]]"
    } else {
      report_puts "N/A (0 delay)"
    }
  }

  report_puts "\n=========================================================================="
  report_puts "$when report_power"
  report_puts "--------------------------------------------------------------------------"
  if { [env_var_exists_and_non_empty CORNERS] } {
    foreach corner $::env(CORNERS) {
      report_puts "Corner: $corner"
      report_power -corner $corner >> $filename
      report_power_metric -corner $corner
    }
    unset corner
  } else {
    report_power >> $filename
    report_power_metric
  }

  # TODO these only work to stdout, whereas we want to append to the $filename
  puts "\n=========================================================================="
  puts "$when report_design_area"
  puts "--------------------------------------------------------------------------"
  report_design_area
  report_design_area_metrics
}
