import argparse
import csv
import math
import re
from pathlib import Path
from statistics import mean
from typing import Optional


CATEGORIES = ("OBJ-GSP", "AANAP", "APAP", "CAVE", "DFW", "DHW", "GES", "LPC", "REW", "SEAGULL", "SVA", "SPHP")

# Reference GES-GSP numbers from StitchBench / OBJ-GSP paper Table 1 (per category).
PAPER_TARGETS = {
    "OBJ-GSP": {"mdr": 1.12229, "niqe": 2.54906},
    "AANAP": {"mdr": 1.05930, "niqe": 2.74965},
    "APAP": {"mdr": 1.20123, "niqe": 3.39280},
    "CAVE": {"mdr": 0.89731, "niqe": 4.01565},
    "DFW": {"mdr": 0.97259, "niqe": 5.69104},
    "DHW": {"mdr": 1.00496, "niqe": 2.60825},
    "GES": {"mdr": 0.98288, "niqe": 3.70041},
    "LPC": {"mdr": 1.10622, "niqe": 3.23057},
    "REW": {"mdr": 1.08635, "niqe": 2.81480},
    "SEAGULL": {"mdr": 1.08296, "niqe": 4.08903},
    "SVA": {"mdr": 1.47813, "niqe": 6.96149},
    "SPHP": {"mdr": 1.07699, "niqe": 2.49712},
}


def category_for(dataset: str) -> Optional[str]:
    for category in CATEGORIES:
        if dataset.startswith(category):
            return category
    return None


def read_datasets(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def parse_rmse(path: Path) -> float:
    if not path.exists():
        return math.nan
    match = re.search(r"RMSE:\s*([-+0-9.eE]+)", path.read_text(encoding="utf-8", errors="ignore"))
    return float(match.group(1)) if match else math.nan


def parse_warping(path: Path) -> tuple[float, float]:
    if not path.exists():
        return math.nan, math.nan
    for line in reversed(path.read_text(encoding="utf-8", errors="ignore").splitlines()):
        parts = line.split()
        if len(parts) == 2:
            try:
                return float(parts[0]), float(parts[1])
            except ValueError:
                pass
    return math.nan, math.nan


def load_niqe_metric(device: str):
    import pyiqa

    return pyiqa.create_metric("niqe", device=device)


def compute_niqe(metric, image_path: Path) -> float:
    if not image_path.exists():
        return math.nan
    try:
        score = metric(str(image_path))
    except Exception:
        import cv2
        import numpy as np
        import torch
        from PIL import Image

        Image.MAX_IMAGE_PIXELS = None
        try:
            image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        except cv2.error:
            with Image.open(image_path) as pil_image:
                pil_image = pil_image.convert("RGB")
                max_side = 4096
                w, h = pil_image.size
                scale = min(1.0, max_side / max(w, h))
                if scale < 1.0:
                    pil_image = pil_image.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
                image = cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2BGR)
        if image is None:
            return math.nan
        h, w = image.shape[:2]
        max_side = 4096
        scale = min(1.0, max_side / max(w, h))
        if scale < 1.0:
            image = cv2.resize(image, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        tensor = torch.from_numpy(image).permute(2, 0, 1).unsqueeze(0)
        score = metric(tensor)
    return float(score.detach().cpu().item()) if hasattr(score, "detach") else float(score)


def finite_mean(values: list[float]) -> float:
    finite = [value for value in values if math.isfinite(value)]
    return mean(finite) if finite else math.nan


def format_float(value: float) -> str:
    return "" if not math.isfinite(value) else f"{value:.5f}"


def read_excluded_datasets(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def read_run_metadata(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    rows = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            dataset = row.get("dataset")
            if not dataset:
                continue
            rows[dataset] = row
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate GES-GSP StitchBench General results.")
    parser.add_argument("--experiment-root", default="experiments/stitchbench_general/ges_gsp")
    parser.add_argument("--datasets-file")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--skip-niqe", action="store_true")
    parser.add_argument("--result-suffix", default="GES-GSP_")
    args = parser.parse_args()

    experiment_root = Path(args.experiment_root)
    datasets_file = Path(args.datasets_file) if args.datasets_file else experiment_root / "datasets.txt"
    datasets = read_datasets(datasets_file)
    run_metadata = read_run_metadata(experiment_root / "run_metadata.csv")
    excluded = read_excluded_datasets(experiment_root / "excluded_datasets.txt")
    if not excluded:
        excluded = read_excluded_datasets(Path(__file__).resolve().parent / "excluded_datasets.txt")

    metric = None
    if not args.skip_niqe:
        metric = load_niqe_metric(args.device)

    per_pair_rows = []
    for dataset in datasets:
        category = category_for(dataset) or ""
        result_path = experiment_root / "0_results" / f"{dataset}-result" / f"{dataset}-{args.result_suffix}.png"
        debug_dir = experiment_root / "1_debugs" / f"{dataset}-result"
        rmse_path = debug_dir / f"{dataset}-RMSE-[DPS].txt"
        residual_path = debug_dir / f"{dataset}-W_Residual-[DPS].txt"
        meta = run_metadata.get(dataset, {})
        if dataset in excluded:
            run_mode = "excluded"
            status = "excluded"
        else:
            run_mode = meta.get("run_mode", "primary")
            status = "ok" if result_path.exists() else "missing_result"
        megapixel_limit = meta.get("megapixel_limit", "")

        residual_avg, residual_sd = parse_warping(residual_path)
        niqe = compute_niqe(metric, result_path) if metric is not None and status == "ok" else math.nan
        mdr_rmse = parse_rmse(rmse_path) if status == "ok" else math.nan
        per_pair_rows.append(
            {
                "dataset": dataset,
                "category": category,
                "run_mode": run_mode,
                "megapixel_limit": megapixel_limit,
                "result_image": str(result_path),
                "mdr_rmse": mdr_rmse,
                "warping_residual_avg": residual_avg if status == "ok" else math.nan,
                "warping_residual_sd": residual_sd if status == "ok" else math.nan,
                "niqe": niqe,
                "status": status,
            }
        )

    primary_rows = [
        row for row in per_pair_rows
        if row["status"] == "ok" and row["run_mode"] not in {"fallback", "excluded"}
    ]
    fallback_rows = [row for row in per_pair_rows if row["run_mode"] == "fallback" and row["status"] == "ok"]

    by_category_rows = []
    comparison_rows = []
    for category in CATEGORIES:
        all_rows = [row for row in per_pair_rows if row["category"] == category]
        rows = [row for row in primary_rows if row["category"] == category]
        mdr_values = [row["mdr_rmse"] for row in rows]
        niqe_values = [row["niqe"] for row in rows]
        valid_mdr_count = len([value for value in mdr_values if math.isfinite(value)])
        valid_niqe_count = len([value for value in niqe_values if math.isfinite(value)])
        mdr_mean = finite_mean(mdr_values)
        niqe_mean = finite_mean(niqe_values)
        target = PAPER_TARGETS[category]

        by_category_rows.append(
            {
                "category": category,
                "total_count": len(all_rows),
                "valid_mdr_count": valid_mdr_count,
                "valid_niqe_count": valid_niqe_count,
                "mdr_rmse_mean": mdr_mean,
                "warping_residual_avg_mean": finite_mean([row["warping_residual_avg"] for row in rows]),
                "warping_residual_sd_mean": finite_mean([row["warping_residual_sd"] for row in rows]),
                "niqe_mean": niqe_mean,
            }
        )
        comparison_rows.append(
            {
                "category": category,
                "total_count": len(all_rows),
                "valid_mdr_count": valid_mdr_count,
                "valid_niqe_count": valid_niqe_count,
                "paper_mdr": target["mdr"],
                "ours_mdr": mdr_mean,
                "paper_niqe": target["niqe"],
                "ours_niqe": niqe_mean,
            }
        )

    def write_csv(path: Path, rows: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            for row in rows:
                writer.writerow({key: format_float(value) if isinstance(value, float) else value for key, value in row.items()})

    write_csv(experiment_root / "per_pair.csv", per_pair_rows)
    write_csv(experiment_root / "by_category.csv", by_category_rows)
    write_csv(experiment_root / "paper_comparison.csv", comparison_rows)

    overall_mdr = finite_mean([row["mdr_rmse"] for row in per_pair_rows if row["status"] == "ok"])
    overall_niqe = finite_mean([row["niqe"] for row in per_pair_rows if row["status"] == "ok"])
    primary_mdr = finite_mean([row["mdr_rmse"] for row in primary_rows])
    primary_niqe = finite_mean([row["niqe"] for row in primary_rows])
    report_lines = [
        "# GES-GSP StitchBench General Report",
        "",
        "CVPR 2022 method on StitchBench General (100 pairs). Primary runs use 80 MP canvas limit;",
        "failed primary runs may retry once with a higher fallback limit.",
        "",
        f"- Overall MDR (all successful runs, n={len([r for r in per_pair_rows if r['status'] == 'ok'])}): **{format_float(overall_mdr)}**",
        f"- Overall NIQE (all successful runs): **{format_float(overall_niqe)}**",
        f"- Primary-only MDR (n={len(primary_rows)}, recommended benchmark): **{format_float(primary_mdr)}**",
        f"- Primary-only NIQE: **{format_float(primary_niqe)}**",
        "",
    ]
    if fallback_rows:
        report_lines.append(
            f"Fallback runs ({len(fallback_rows)}): "
            + ", ".join(row["dataset"] for row in fallback_rows)
            + ". These used a higher megapixel limit due to extreme warp and are excluded from primary-only averages."
        )
        report_lines.append("")
    excluded_rows = [row for row in per_pair_rows if row["status"] == "excluded"]
    if excluded_rows:
        report_lines.append(
            f"Excluded runs ({len(excluded_rows)}): "
            + ", ".join(row["dataset"] for row in excluded_rows)
            + ". Recorded as failed/skipped; excluded from all averages."
        )
        report_lines.append("")
    report_lines.extend(
        [
        "| Category | Valid/Total | Paper MDR | Ours MDR | Paper NIQE | Ours NIQE |",
        "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in comparison_rows:
        report_lines.append(
            "| {category} | {valid}/{total} | {paper_mdr:.5f} | {ours_mdr} | {paper_niqe:.5f} | {ours_niqe} |".format(
                category=row["category"],
                valid=row["valid_mdr_count"],
                total=row["total_count"],
                paper_mdr=row["paper_mdr"],
                ours_mdr=format_float(row["ours_mdr"]),
                paper_niqe=row["paper_niqe"],
                ours_niqe=format_float(row["ours_niqe"]),
            )
        )
    report_lines.extend(
        [
            "",
            "MDR is read from C++ RMSE debug output. NIQE uses pyiqa on stitched PNGs.",
            "Graph files are auto-generated from sorted filenames; multi-image sets use a linear chain.",
        ]
    )
    (experiment_root / "report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    print(f"Wrote evaluation files to {experiment_root}")
    print(f"Overall MDR={format_float(overall_mdr)} NIQE={format_float(overall_niqe)}")
    print(f"Primary-only MDR={format_float(primary_mdr)} NIQE={format_float(primary_niqe)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
