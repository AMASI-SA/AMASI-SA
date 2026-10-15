"""Acceptance arithmetic must never turn an exceeded budget into PASS."""
import importlib.util
from pathlib import Path


def module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "benchmark_observability_linux.py"
    spec = importlib.util.spec_from_file_location("linux_benchmark", path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def pair(cpu=.05, rss=1024):
    return [dict(repeat=0, mode=mode, cpu_seconds=.1 + extra_cpu,
                 wall_seconds=10., rss_steady_median_bytes=10000000 + extra_rss,
                 rss_process_highwater_bytes=11000000 + extra_rss,
                 rss_sampled_peak_bytes=10500000 + extra_rss)
            for mode, extra_cpu, extra_rss in (("disabled", 0, 0), ("enabled", cpu, rss))]


def test_budget_reports_measured_only():
    result = module().summarize(pair(), 1)
    assert result["measured_budget_result"] == "PASS_BOUNDED_WORKLOAD"
    assert result["production_overhead_acceptance"] == "LIMITATION"
    assert abs(result["worst_pair_cpu_allocated_percent"] - .5) < .000001


def test_cpu_fail_not_hidden_by_memory_pass():
    assert module().summarize(pair(cpu=.11), 1)["measured_budget_result"] == "FAIL"


def test_memory_fail_not_hidden_by_cpu_pass():
    assert module().summarize(pair(rss=8 * 1024**2 + 1), 1)["measured_budget_result"] == "FAIL"


def test_worst_pair_not_median_determines_result():
    samples = pair()
    samples += [dict(s, repeat=1) for s in pair(cpu=.2)]
    assert module().summarize(samples, 1)["measured_budget_result"] == "FAIL"


def test_disabled_vs_legacy_has_independent_gate():
    samples = [dict(s, mode="legacy" if s["mode"] == "disabled" else "disabled")
               for s in pair(cpu=.2)]
    result = module().summarize(samples, 1, "legacy", "disabled")
    assert result["baseline_mode"] == "legacy"
    assert result["measured_mode"] == "disabled"
    assert result["measured_budget_result"] == "FAIL"


def test_negative_pair_delta_preserved_as_noise_not_zeroed():
    result = module().summarize(pair(cpu=-.05, rss=-1024), 1)
    assert result["worst_pair_cpu_allocated_percent"] < 0
    assert result["worst_pair_rss_bytes"] == -1024
