from evalkit.core.evaluators.base import PASS_THRESHOLD
from evalkit.core.scorecard import ANSWER_RUBRIC_DIAGNOSTICS

CHANGE_THRESHOLD = 0.05
SHOWN_DELTA = 0.005
SHOWN_GAUGE_CHANGE = 0.005


def _fmt(value: float | None) -> str:
    return f"{value:.2f}" if value is not None else "  - "


def _delta(base: float | None, value: float | None) -> float | None:
    return None if base is None or value is None else round(value - base, 6)


def _fmt_delta(base: float | None, value: float | None) -> str:
    delta = _delta(base, value)
    return f"  Δ{delta:+.2f}" if delta is not None and abs(delta) >= SHOWN_DELTA else ""


def _num(value: float | None) -> str:
    if value is None:
        return "-"
    if value and abs(value) < 1:
        return f"{value:.3g}"
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _fmt_gauge_delta(base: float | None, value: float | None) -> str:
    delta = _delta(base, value)
    if not delta or abs(delta) < SHOWN_GAUGE_CHANGE * max(abs(base or 0), abs(value or 0)):
        return ""
    return f"  Δmean {'+' if delta > 0 else ''}{_num(delta)}"


def _change(base: dict | None, case: dict | None) -> str:
    if case is None:
        return "not run"
    if base is None:
        return "new"
    delta = _delta(base["overall"], case["overall"])
    if delta is None:
        return "unscored"
    if delta >= CHANGE_THRESHOLD:
        return "improved"
    if delta <= -CHANGE_THRESHOLD:
        return "regressed"
    return "unchanged"


def _changed_cases(report: dict, base: dict) -> list[str]:
    before = {case["case_id"]: case for case in base["cases"]}
    after = {case["case_id"]: case for case in report["cases"]}
    groups: dict[str, list[str]] = {
        "regressed": [],
        "improved": [],
        "new": [],
        "unscored": [],
    }
    unchanged = 0
    not_run = 0
    for case_id in sorted(before.keys() | after.keys()):
        old, new = before.get(case_id), after.get(case_id)
        change = _change(old, new)
        if change == "unchanged":
            unchanged += 1
            continue
        if change == "not run":
            not_run += 1
            continue
        old_overall = old["overall"] if old else None
        new_overall = new["overall"] if new else None
        delta = _fmt_delta(old_overall, new_overall)
        groups[change].append(f"    {case_id:<46} {_fmt(old_overall)} → {_fmt(new_overall)}{delta}")
    lines = ["changed cases:"]
    for change, rows in groups.items():
        if rows:
            lines.append(f"  {change} ({len(rows)}):")
            lines += rows
    if len(lines) == 1:
        lines.append("  none")
    lines.append(f"  unchanged: {unchanged}")
    if not_run:
        lines.append(f"  not run here (in the baseline only): {not_run}")
    return lines


def _selected(report: dict) -> int:
    return max(report.get("selected_cases", 0), len(report["cases"]))


def _cases_text(report: dict) -> str:
    selected, ran = _selected(report), len(report["cases"])
    parts = [f"{selected}" if ran == selected else f"{ran} of {selected} ran"]
    if errored := report.get("errored_cases", 0):
        parts.append(
            f"{errored} errored (no run completed with an answer; left out of every score)"
        )
    if partly := report.get("partly_errored_cases", 0):
        parts.append(f"{partly} partly errored (scored only on the runs that completed)")
    return ", ".join(parts)


def _bucket_coverage(report: dict, bucket: str) -> str:
    total = _selected(report)
    covered = report.get("bucket_cases", {}).get(bucket, total)
    return f"  ({covered} of {total} cases)" if covered < total else ""


def summary_lines(report: dict, base: dict | None = None) -> list[str]:
    lines = [f"\nexperiment {report['experiment_id']}  {report['name']} of {report['dataset']}"]
    if base is not None:
        lines.append(f"vs experiment {base['experiment_id']}  {base['name']} of {base['dataset']}")
    lines.append(f"cases: {_cases_text(report)}")
    if base is not None:
        lines.append(f"baseline cases: {_cases_text(base)}")
    base_buckets = base["buckets"] if base else {}
    base_evaluators = {stat["name"]: stat for stat in base["evaluators"]} if base else {}
    scored = {bucket: value for bucket, value in report["buckets"].items() if value is not None}
    if scored:
        lines.append("buckets:")
        lines += [
            f"  {bucket:<14} {_fmt(value)}{_bucket_coverage(report, bucket)}"
            f"{_fmt_delta(base_buckets.get(bucket), value)}"
            for bucket, value in scored.items()
        ]
    lines.append("evaluators:")
    for stat in report["evaluators"]:
        errored = f", {stat['errors']} errored" if stat.get("errors") else ""
        noun = "case" if stat["cases"] == 1 else "cases"
        counts = f"({stat['cases']} {noun}{errored})"
        base_stat = base_evaluators.get(stat["name"])
        base_cases = (
            f", base {base_stat['cases']}"
            if base_stat and base_stat["cases"] != stat["cases"]
            else ""
        )
        counts = f"({stat['cases']} {noun}{errored}{base_cases})"
        delta = _fmt_delta(base_stat["score"] if base_stat else None, stat["score"])
        lines.append(f"  {stat['name']:<40} {_fmt(stat['score'])}  {counts}{delta}")
    if base is not None:
        lines += _changed_cases(report, base)
    failing = []
    for case in report["cases"]:
        low = {
            name: score
            for name, score in (case.get("scores") or {}).items()
            if name not in ANSWER_RUBRIC_DIAGNOSTICS
            and score is not None
            and score < PASS_THRESHOLD
        }
        overall_low = case["overall"] is not None and case["overall"] < PASS_THRESHOLD
        if overall_low or low:
            failing.append((case, low))
    if failing:
        lines.append(f"failing cases (overall or any evaluator < {PASS_THRESHOLD:.2f}):")
        for case, low in failing:
            delta = _fmt_delta(case.get("baseline_overall"), case["overall"])
            evaluators = ", ".join(f"{name} {_fmt(score)}" for name, score in sorted(low.items()))
            detail = f"  ({evaluators})" if evaluators else ""
            lines.append(f"  {case['case_id']:<48} {_fmt(case['overall'])}{delta}{detail}")
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
        base_gauges = {gauge["name"]: gauge["mean"] for gauge in base["gauges"]} if base else {}
        lines.append("gauges:")
        lines += [
            f"  {gauge['name']:<32} mean={_num(gauge['mean'])} median={_num(gauge['median'])}"
            f"{_fmt_gauge_delta(base_gauges.get(gauge['name']), gauge['mean'])}"
            for gauge in report["gauges"]
        ]
    return lines
