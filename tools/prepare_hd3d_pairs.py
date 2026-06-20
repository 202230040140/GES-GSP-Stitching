import argparse
import json
import os
import shutil
from pathlib import Path


IMAGE_EXTENSIONS = {".bmp", ".dib", ".jpeg", ".jpg", ".jpe", ".jp2", ".png", ".pbm", ".pgm", ".ppm", ".sr", ".ras", ".tiff", ".tif"}
PAIR_IDS = ("12", "13", "14", "23", "24", "34")
SCENES = (
    "Indoor_001", "Indoor_002", "Indoor_003", "Indoor_004", "Indoor_005", "Indoor_006", "Indoor_007",
    "Outdoor_001", "Outdoor_002", "Outdoor_003", "Outdoor_004", "Outdoor_005", "Outdoor_006",
)


def is_gt(name: str) -> bool:
    lower = name.lower()
    return lower.endswith("_gt.jpg") or lower.endswith("_gt.jpeg") or "_gt." in lower


def is_image(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS and not is_gt(path.name)


def discover_scenes(dataset_root: Path) -> list[str]:
    scenes = []
    for name in SCENES:
        if any((dataset_root / f"{name}_{idx}.jpg").exists() for idx in range(1, 5)):
            scenes.append(name)
    if len(scenes) != 13:
        found = sorted({path.name.rsplit("_", 1)[0] for path in dataset_root.iterdir() if is_image(path)})
        scenes = found
    return scenes


def scene_image(dataset_root: Path, scene: str, role: str) -> Path:
    return dataset_root / f"{scene}_{role}.jpg"


def make_graph_text(image_count: int) -> str:
    lines = [
        "{center_image_index | 0 | center image index}",
        "{center_image_rotation_angle | 0 | center image rotation angle}",
        f"{{images_count | {image_count} | images count}}",
    ]
    for index in range(1, image_count):
        lines.append(f"{{matching_graph_image_edges-{index} | {index - 1} | matching graph image edge {index}}}")
    return "\n".join(lines) + "\n"


def materialize_file(source: Path, dest: Path) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    try:
        os.link(source, dest)
        return "hardlink"
    except OSError:
        shutil.copy2(source, dest)
        return "copy"


def build_manifest(dataset_root: Path, result_root: Path) -> list[dict]:
    work_root = result_root / "_work"
    pairs_root = work_root / "pairs"
    graphs_root = work_root / "graphs"
    rows = []
    for scene in discover_scenes(dataset_root):
        gt_path = dataset_root / f"{scene}_gt.jpg"
        if not gt_path.exists():
            raise FileNotFoundError(f"Missing GT image: {gt_path}")
        for pair_id in PAIR_IDS:
            left_role, right_role = pair_id[0], pair_id[1]
            left_source = scene_image(dataset_root, scene, left_role)
            right_source = scene_image(dataset_root, scene, right_role)
            if not left_source.exists() or not right_source.exists():
                raise FileNotFoundError(f"Missing source images for {scene} pair {pair_id}")
            pair_name = f"{scene}_p{pair_id}"
            pair_dir = pairs_root / pair_name
            left_materialized_as = materialize_file(left_source, pair_dir / left_source.name)
            right_materialized_as = materialize_file(right_source, pair_dir / right_source.name)
            graph_file = graphs_root / pair_name / f"{pair_name}-STITCH-GRAPH.txt"
            graph_file.parent.mkdir(parents=True, exist_ok=True)
            graph_file.write_text(make_graph_text(2), encoding="utf-8")
            rows.append(
                {
                    "pair_name": pair_name,
                    "scene": scene,
                    "pair_id": pair_id,
                    "left_role": left_role,
                    "right_role": right_role,
                    "left_source": str(left_source),
                    "right_source": str(right_source),
                    "gt_path": str(gt_path),
                    "pair_dir": str(pair_dir),
                    "graph_file": str(graph_file),
                    "final_pair_dir": str(result_root / scene / f"pair_{pair_id}"),
                    "left_materialized_as": left_materialized_as,
                    "right_materialized_as": right_materialized_as,
                }
            )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare HD3D two-view pair folders and manifest for GES-GSP.")
    parser.add_argument("--dataset-root", default=r"D:\HD3D_Dataset")
    parser.add_argument("--result-root", default=r"D:\HD3D_Result")
    args = parser.parse_args()

    dataset_root = Path(args.dataset_root)
    result_root = Path(args.result_root)
    if not dataset_root.exists():
        raise FileNotFoundError(f"Dataset root does not exist: {dataset_root}")

    rows = build_manifest(dataset_root, result_root)
    work_root = result_root / "_work"
    work_root.mkdir(parents=True, exist_ok=True)
    (work_root / "manifest.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    (work_root / "datasets.txt").write_text("\n".join(row["pair_name"] for row in rows) + "\n", encoding="utf-8")
    print(f"Prepared {len(rows)} HD3D pairs under {work_root / 'pairs'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
