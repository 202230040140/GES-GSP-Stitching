import argparse
import csv
import json
import math
from pathlib import Path
from statistics import mean, median
from typing import Optional

from hd3d_metrics import PairPaths, evaluate_pair_paths


DEFAULT_METHOD = "ges_gsp"
DEFAULT_CSV_FIELDS = [
    "scene",
    "pair_id",
    "pair_name",
    "method",
    "status",
    "failure_reason",
    "mdr",
    "niqe",
    "psnr",
    "ssim",
    "lpips",
    "rmse",
    "runtime_seconds",
    "valid_ratio",
    "alignment_matcher",
    "alignment_matches",
    "alignment_inliers",
    "valid_mask_strategy",
    "lpips_max_side",
    "raw_path",
    "aligned_path",
    "valid_mask_path",
    "gt_path",
    "cpp_mdr",
    "cpp_warping_residual_avg",
    "cpp_warping_residual_sd",
    "gt_width",
    "gt_height",
]


def load_manifest(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_pair_paths(manifest_entry: dict, result_root: Path, method: str, work_root: Optional[Path]) -> PairPaths:
    final_pair_dir = Path(manifest_entry["final_pair_dir"])
    method_dir = final_pair_dir / method
    pair_name = manifest_entry["pair_name"]
    cpp_rmse_path = None
    cpp_residual_path = None
    if work_root is not None:
        debug_dir = work_root / "1_debugs" / f"{pair_name}-result"
        cpp_rmse_path = debug_dir / f"{pair_name}-RMSE-[DPS].txt"
        cpp_residual_path = debug_dir / f"{pair_name}-W_Residual-[DPS].txt"
    status_path = method_dir / "method_status.json"
    runtime_seconds = math.nan
    if status_path.exists():
        payload = json.loads(status_path.read_text(encoding="utf-8-sig"))
        try:
            runtime_seconds = float(payload.get("runtime_seconds", math.nan))
        except (TypeError, ValueError):
            runtime_seconds = math.nan
    return PairPaths(
        pair_name=pair_name,
        final_pair_dir=final_pair_dir,
        method_dir=method_dir,
        raw_path=method_dir / "raw.png",
        aligned_path=method_dir / "aligned_to_gt.png",
        valid_mask_path=method_dir / "valid_mask.png",
        gt_path=Path(manifest_entry["gt_path"]),
        cpp_rmse_path=cpp_rmse_path,
        cpp_residual_path=cpp_residual_path,
        runtime_seconds=runtime_seconds,
        status_path=status_path,
        metrics_path=method_dir / "metrics.json",
        stdout_path=method_dir / "run.log",
        stderr_path=method_dir / "error.log",
    )


def existing_csv_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def finite_mean(values: list[float]) -> float:
    finite = [value for value in values if math.isfinite(value)]
    return mean(finite) if finite else math.nan


def finite_median(values: list[float]) -> float:
    finite = [value for value in values if math.isfinite(value)]
    return median(finite) if finite else math.nan


def float_or_nan(value) -> float:
    if value is None or value == "":
        return math.nan
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def update_summary_csv(path: Path, rows: list[dict]) -> None:
    by_method: dict[str, list[dict]] = {}
    for row in rows:
        by_method.setdefault(row["method"], []).append(row)

    summary_rows = []
    rows_by_method = {}
    for method, method_rows in sorted(by_method.items()):
        total_runs = len(method_rows)
        successes = [row for row in method_rows if row.get("status") == "success"]
        failures = total_runs - len(successes)
        summary_rows.append(
            {
                "method": method,
                "total_runs": total_runs,
                "successes": len(successes),
                "failures": failures,
                "failure_rate": failures / total_runs if total_runs else math.nan,
                "mean_mdr": finite_mean([float_or_nan(row.get("mdr")) for row in successes]),
                "median_mdr": finite_median([float_or_nan(row.get("mdr")) for row in successes]),
                "mean_niqe": finite_mean([float_or_nan(row.get("niqe")) for row in successes]),
                "median_niqe": finite_median([float_or_nan(row.get("niqe")) for row in successes]),
                "mean_psnr": finite_mean([float_or_nan(row.get("psnr")) for row in successes]),
                "median_psnr": finite_median([float_or_nan(row.get("psnr")) for row in successes]),
                "mean_ssim": finite_mean([float_or_nan(row.get("ssim")) for row in successes]),
                "median_ssim": finite_median([float_or_nan(row.get("ssim")) for row in successes]),
                "mean_lpips": finite_mean([float_or_nan(row.get("lpips")) for row in successes]),
                "median_lpips": finite_median([float_or_nan(row.get("lpips")) for row in successes]),
                "mean_rmse": finite_mean([float_or_nan(row.get("rmse")) for row in successes]),
                "median_rmse": finite_median([float_or_nan(row.get("rmse")) for row in successes]),
                "mean_runtime": finite_mean([float_or_nan(row.get("runtime_seconds")) for row in successes]),
                "median_runtime": finite_median([float_or_nan(row.get("runtime_seconds")) for row in successes]),
            }
        )
        rows_by_method[method] = summary_rows[-1]

    ordered_summary = [rows_by_method[name] for name in METHOD_ORDER if name in rows_by_method]
    ordered_summary.extend(row for row in summary_rows if row["method"] not in METHOD_ORDER)
    summary_rows = ordered_summary

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summary_rows[0].keys()))
        writer.writeheader()
        for row in summary_rows:
            writer.writerow(
                {
                    key: (
                        f"{value:.5f}"
                        if isinstance(value, float) and math.isfinite(value)
                        else ("" if isinstance(value, float) else value)
                    )
                    for key, value in row.items()
                }
            )


METHOD_ORDER = ("traditional", "nis_depths", "depth_gsp", "obj_gsp", "ges_gsp")


def update_report(path: Path, summary_csv: Path, failures: list[dict]) -> None:
    with summary_csv.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    rows_by_method = {row["method"]: row for row in rows}
    ordered_rows = [rows_by_method[name] for name in METHOD_ORDER if name in rows_by_method]
    ordered_rows.extend(row for row in rows if row["method"] not in METHOD_ORDER)

    lines = [
        "# HD3D Two-View Stitching Report",
        "",
        "All scenes are aggregated together. MDR is the GT-alignment RANSAC reprojection RMSE in pixels. "
        "PSNR, SSIM, LPIPS, and image RMSE are computed between aligned output and GT within the valid mask.",
        "",
        "| Method | Success/Total | Failure Rate | Mean MDR | Mean NIQE | Mean PSNR | Mean SSIM | Mean LPIPS | Mean RMSE | Mean Runtime (s) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in ordered_rows:
        lines.append(
            f"| {row['method']} | {row['successes']}/{row['total_runs']} | {row['failure_rate']} | "
            f"{row['mean_mdr']} | {row['mean_niqe']} | {row['mean_psnr']} | {row['mean_ssim']} | "
            f"{row['mean_lpips']} | {row['mean_rmse']} | {row['mean_runtime']} |"
        )
    if failures:
        lines.extend(["", "## Failures", "", "| Scene | Pair | Method | Reason |", "|---|---|---|---|"])
        for item in failures:
            lines.append(f"| {item['scene']} | {item['pair_id']} | {item['method']} | {item['reason']} |")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def metrics_to_csv_row(metrics: dict) -> dict:
    row = {}
    for key in DEFAULT_CSV_FIELDS:
        value = metrics.get(key, "")
        if isinstance(value, float):
            row[key] = f"{value:.5f}" if math.isfinite(value) else ""
        else:
            row[key] = value
    return row


def evaluate_method_entry(
    manifest_entry: dict,
    method: str,
    result_root: Path,
    work_root: Optional[Path],
    skip_niqe: bool,
    skip_lpips: bool,
    device: str,
) -> dict:
    paths = build_pair_paths(manifest_entry, result_root, method, work_root)
    metrics = evaluate_pair_paths(
        paths,
        method=method,
        scene=manifest_entry["scene"],
        pair_id=manifest_entry["pair_id"],
        skip_niqe=skip_niqe,
        skip_lpips=skip_lpips,
        device=device,
    )
    paths.method_dir.mkdir(parents=True, exist_ok=True)
    paths.metrics_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    return metrics


def validate_baseline(
    manifest: list[dict],
    result_root: Path,
    method: str,
    pair_name: Optional[str],
    tolerance: float,
    skip_niqe: bool,
    skip_lpips: bool,
    device: str,
) -> None:
    csv_path = result_root / "per_pair_metrics.csv"
    existing = {(row["pair_name"], row["method"]): row for row in existing_csv_rows(csv_path)}
    entries = [entry for entry in manifest if pair_name is None or entry["pair_name"] == pair_name]
    if not entries:
        raise RuntimeError("No manifest entries selected for validation.")

    for entry in entries[: min(2, len(entries))]:
        paths = build_pair_paths(entry, result_root, method, work_root=None)
        metrics = evaluate_pair_paths(
            paths,
            method=method,
            scene=entry["scene"],
            pair_id=entry["pair_id"],
            skip_niqe=skip_niqe,
            skip_lpips=skip_lpips,
            device=device,
        )
        baseline = existing.get((entry["pair_name"], method))
        if baseline is None:
            raise RuntimeError(f"Missing baseline CSV row for {entry['pair_name']} / {method}")
        for key in ("psnr", "rmse"):
            expected = float_or_nan(baseline.get(key))
            actual = float_or_nan(metrics.get(key))
            if not math.isfinite(expected) or not math.isfinite(actual):
                continue
            if abs(expected - actual) > tolerance:
                raise RuntimeError(
                    f"Validation failed for {entry['pair_name']} {key}: expected={expected}, actual={actual}"
                )
        for key in ("mdr", "ssim", "lpips"):
            expected = float_or_nan(baseline.get(key))
            actual = float_or_nan(metrics.get(key))
            if math.isfinite(expected) and math.isfinite(actual):
                print(f"  note {entry['pair_name']} {key}: baseline={expected:.5f} recomputed={actual:.5f}")
    print(f"Validation passed for method={method} on {min(2, len(entries))} pair(s).")


def collect_failures(rows: list[dict], method: str) -> list[dict]:
    failures = []
    for row in rows:
        if row.get("method") != method or row.get("status") == "success":
            continue
        failures.append(
            {
                "scene": row.get("scene", ""),
                "pair_id": row.get("pair_id", ""),
                "method": method,
                "reason": row.get("failure_reason") or "unknown",
            }
        )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate HD3D stitching outputs and update CSV/report.")
    parser.add_argument("--result-root", default=r"D:\HD3D_Result")
    parser.add_argument("--manifest", default=r"D:\HD3D_Result\_work\manifest.json")
    parser.add_argument("--work-root", help="C++ output root for debug metrics (default: <result-root>/_work/<method>).")
    parser.add_argument("--method", default=DEFAULT_METHOD)
    parser.add_argument("--pair", action="append", help="Limit to one pair name; can be repeated.")
    parser.add_argument("--validate-method", help="Recompute metrics for an existing method and compare with per_pair_metrics.csv.")
    parser.add_argument("--validation-tolerance", type=float, default=0.15)
    parser.add_argument("--skip-niqe", action="store_true")
    parser.add_argument("--skip-lpips", action="store_true")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--update-summary", action="store_true")
    args = parser.parse_args()

    result_root = Path(args.result_root)
    manifest = load_manifest(Path(args.manifest))
    if args.pair:
        selected = set(args.pair)
        manifest = [entry for entry in manifest if entry["pair_name"] in selected]

    if args.validate_method:
        validate_baseline(
            manifest,
            result_root,
            args.validate_method,
            args.pair[0] if args.pair else None,
            args.validation_tolerance,
            skip_niqe=args.skip_niqe,
            skip_lpips=args.skip_lpips,
            device=args.device,
        )
        return 0

    work_root = Path(args.work_root) if args.work_root else result_root / "_work" / args.method
    new_rows = []
    for entry in manifest:
        new_rows.append(
            evaluate_method_entry(
                entry,
                args.method,
                result_root,
                work_root=work_root if args.method == "ges_gsp" else None,
                skip_niqe=args.skip_niqe,
                skip_lpips=args.skip_lpips,
                device=args.device,
            )
        )

    csv_path = result_root / "per_pair_metrics.csv"
    all_rows = [row for row in existing_csv_rows(csv_path) if row.get("method") != args.method]
    all_rows.extend(metrics_to_csv_row(metrics) for metrics in new_rows)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=DEFAULT_CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in all_rows:
            writer.writerow(row)

    if args.update_summary:
        summary_csv = result_root / "summary_all.csv"
        update_summary_csv(summary_csv, all_rows)
        failures = collect_failures(all_rows, args.method)
        update_report(result_root / "report.md", summary_csv, failures)

    print(f"Evaluated {len(new_rows)} pair(s) for method={args.method}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
