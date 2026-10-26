def _fmt(value: float | None) -> str:
    return f"{value:.2f}" if value is not None else "  - "


def _num(value: float | None) -> str:
    if value is None:
        return "-"
    if value and abs(value) < 1:
        return f"{value:.3g}"
    return f"{value:.2f}".rstrip("0").rstrip(".")


def summary_lines(report: dict) -> list[str]:
    lines = [f"\nexperiment {report['experiment_id']}  {report['name']} of {report['dataset']}"]
    scored = {bucket: value for bucket, value in report["buckets"].items() if value is not None}
    if scored:
        lines.append("buckets:")
        lines += [f"  {bucket:<14} {_fmt(value)}" for bucket, value in scored.items()]
    lines.append("evaluators:")
    for stat in report["evaluators"]:
        errored = f", {stat['errors']} errored" if stat.get("errors") else ""
        noun = "case" if stat["cases"] == 1 else "cases"
        counts = f"({stat['cases']} {noun}{errored})"
        lines.append(f"  {stat['name']:<40} {_fmt(stat['score'])}  {counts}")
    failing = [c for c in report["cases"] if c["overall"] is not None and c["overall"] < 0.8]
    if failing:
        lines.append("failing cases (< 0.80):")
        for case in failing:
            delta = f"  Δ{case['delta']:+.2f}" if case.get("delta") is not None else ""
            lines.append(f"  {case['case_id']:<48} {_fmt(case['overall'])}{delta}")
    errored_cases = [c for c in report["cases"] if c["errors"] or c["run_errors"]]
    if errored_cases:
        lines.append("errored cases:")
        for case in errored_cases:
            problems = [("run", error) for error in case["run_errors"]]
            problems += sorted(case["errors"].items())
            for name, error in problems:
                first_line = error.splitlines()[0][:120] if error else ""
                lines.append(f"  {case['case_id']:<48} {name}: {first_line}")
    if report["gauges"]:
        lines.append("gauges:")
        lines += [
            f"  {gauge['name']:<32} mean={_num(gauge['mean'])} median={_num(gauge['median'])}"
            for gauge in report["gauges"]
        ]
    return lines
