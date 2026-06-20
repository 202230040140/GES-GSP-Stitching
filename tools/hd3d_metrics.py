import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from skimage.metrics import structural_similarity


LPIPS_MAX_SIDE = 1024
VALID_MASK_STRATEGY = "edge_connected_black_canvas"
ALIGNMENT_MATCHER = "sift"


@dataclass
class PairPaths:
    pair_name: str
    final_pair_dir: Path
    method_dir: Path
    raw_path: Path
    aligned_path: Path
    valid_mask_path: Path
    gt_path: Path
    cpp_rmse_path: Optional[Path]
    cpp_residual_path: Optional[Path]
    runtime_seconds: float
    status_path: Path
    metrics_path: Path
    stdout_path: Path
    stderr_path: Path


def parse_rmse(path: Path) -> float:
    if path is None or not path.exists():
        return math.nan
    match = re.search(r"RMSE:\s*([-+0-9.eE]+)", path.read_text(encoding="utf-8", errors="ignore"))
    return float(match.group(1)) if match else math.nan


def parse_warping(path: Path) -> tuple[float, float]:
    if path is None or not path.exists():
        return math.nan, math.nan
    for line in reversed(path.read_text(encoding="utf-8", errors="ignore").splitlines()):
        parts = line.split()
        if len(parts) == 2:
            try:
                return float(parts[0]), float(parts[1])
            except ValueError:
                pass
    return math.nan, math.nan


def load_image_bgr(path: Path) -> np.ndarray:
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise FileNotFoundError(f"Failed to read image: {path}")
    return image


def resize_max_side(image: np.ndarray, max_side: int) -> np.ndarray:
    height, width = image.shape[:2]
    scale = min(1.0, max_side / max(height, width))
    if scale >= 1.0:
        return image
    new_size = (int(width * scale), int(height * scale))
    return cv2.resize(image, new_size, interpolation=cv2.INTER_AREA)


def edge_connected_black_canvas_mask(image_bgr: np.ndarray, black_threshold: int = 8) -> np.ndarray:
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    non_black = (gray > black_threshold).astype(np.uint8)
    black = (1 - non_black).astype(np.uint8) * 255
    height, width = black.shape
    flood = black.copy()
    mask = np.zeros((height + 2, width + 2), np.uint8)
    for seed in range(width):
        if flood[0, seed] == 255:
            cv2.floodFill(flood, mask, (seed, 0), 0)
        if flood[height - 1, seed] == 255:
            cv2.floodFill(flood, mask, (seed, height - 1), 0)
    for seed in range(height):
        if flood[seed, 0] == 255:
            cv2.floodFill(flood, mask, (0, seed), 0)
        if flood[seed, width - 1] == 255:
            cv2.floodFill(flood, mask, (width - 1, seed), 0)
    edge_black = (flood > 0).astype(np.uint8)
    return (non_black & (1 - edge_black)).astype(np.uint8)


def sift_homography(raw_bgr: np.ndarray, gt_bgr: np.ndarray) -> tuple[Optional[np.ndarray], dict]:
    sift = cv2.SIFT_create()
    raw_gray = cv2.cvtColor(raw_bgr, cv2.COLOR_BGR2GRAY)
    gt_gray = cv2.cvtColor(gt_bgr, cv2.COLOR_BGR2GRAY)
    keypoints_raw, descriptors_raw = sift.detectAndCompute(raw_gray, None)
    keypoints_gt, descriptors_gt = sift.detectAndCompute(gt_gray, None)
    stats = {
        "alignment_matcher": ALIGNMENT_MATCHER,
        "alignment_matches": 0,
        "alignment_inliers": 0,
    }
    if descriptors_raw is None or descriptors_gt is None or len(keypoints_raw) < 4 or len(keypoints_gt) < 4:
        return None, stats

    matcher = cv2.BFMatcher(cv2.NORM_L2, crossCheck=False)
    knn_matches = matcher.knnMatch(descriptors_raw, descriptors_gt, k=2)
    good = []
    for pair in knn_matches:
        if len(pair) != 2:
            continue
        first, second = pair
        if first.distance < 0.75 * second.distance:
            good.append(first)
    stats["alignment_matches"] = len(good)
    if len(good) < 4:
        return None, stats

    src_pts = np.float32([keypoints_raw[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst_pts = np.float32([keypoints_gt[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    homography, inlier_mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
    if homography is None or inlier_mask is None:
        return None, stats

    inliers = inlier_mask.ravel().astype(bool)
    stats["alignment_inliers"] = int(inliers.sum())
    if stats["alignment_inliers"] < 4:
        return None, stats

    stats["mdr"] = compute_reprojection_rmse(src_pts, dst_pts, homography, inliers)
    return homography, stats


def compute_reprojection_rmse(
    src_pts: np.ndarray,
    dst_pts: np.ndarray,
    homography: np.ndarray,
    inliers: np.ndarray,
) -> float:
    projected = cv2.perspectiveTransform(src_pts[inliers], homography)
    target = dst_pts[inliers]
    errors = np.linalg.norm(projected - target, axis=2)
    return float(np.sqrt(np.mean(errors ** 2)))


def align_to_gt(raw_bgr: np.ndarray, gt_bgr: np.ndarray) -> tuple[np.ndarray, dict]:
    homography, stats = sift_homography(raw_bgr, gt_bgr)
    height, width = gt_bgr.shape[:2]
    if homography is None:
        aligned = cv2.resize(raw_bgr, (width, height), interpolation=cv2.INTER_LINEAR)
        stats["mdr"] = math.nan
        return aligned, stats
    aligned = cv2.warpPerspective(raw_bgr, homography, (width, height), flags=cv2.INTER_LINEAR)
    return aligned, stats


def compute_image_rmse(gt: np.ndarray, pred: np.ndarray, mask: np.ndarray) -> float:
    valid = mask.astype(bool)
    if valid.sum() == 0:
        return math.nan
    diff = gt[valid].astype(np.float64) - pred[valid].astype(np.float64)
    return float(np.sqrt(np.mean(diff ** 2)))


def compute_psnr_ssim(gt_bgr: np.ndarray, pred_bgr: np.ndarray, mask: np.ndarray) -> tuple[float, float]:
    valid = mask.astype(bool)
    if valid.sum() == 0:
        return math.nan, math.nan
    diff = gt_bgr[valid].astype(np.float64) - pred_bgr[valid].astype(np.float64)
    mse = float(np.mean(diff ** 2))
    psnr = float(10.0 * np.log10((255.0 ** 2) / mse)) if mse > 0 else math.inf
    gt_rgb = cv2.cvtColor(gt_bgr, cv2.COLOR_BGR2RGB)
    pred_rgb = cv2.cvtColor(pred_bgr, cv2.COLOR_BGR2RGB)
    ssim = float(
        structural_similarity(gt_rgb, pred_rgb, channel_axis=2, data_range=255, mask=valid)
    )
    return psnr, ssim


_niqe_metric = None
_lpips_metric = None


def load_niqe_metric(device: str = "cpu"):
    global _niqe_metric
    if _niqe_metric is None:
        import pyiqa

        _niqe_metric = pyiqa.create_metric("niqe", device=device)
    return _niqe_metric


def load_lpips_metric(device: str = "cpu"):
    global _lpips_metric
    if _lpips_metric is None:
        import pyiqa

        _lpips_metric = pyiqa.create_metric("lpips", device=device)
    return _lpips_metric


def compute_niqe(image_bgr: np.ndarray, device: str = "cpu") -> float:
    metric = load_niqe_metric(device)
    image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    import torch

    tensor = torch.from_numpy(image_rgb).permute(2, 0, 1).unsqueeze(0)
    score = metric(tensor)
    return float(score.detach().cpu().item()) if hasattr(score, "detach") else float(score)


def compute_lpips(gt_bgr: np.ndarray, pred_bgr: np.ndarray, device: str = "cpu") -> float:
    metric = load_lpips_metric(device)
    gt_small = resize_max_side(gt_bgr, LPIPS_MAX_SIDE)
    pred_small = resize_max_side(pred_bgr, LPIPS_MAX_SIDE)
    gt_rgb = cv2.cvtColor(gt_small, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    pred_rgb = cv2.cvtColor(pred_small, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    import torch

    gt_tensor = torch.from_numpy(gt_rgb).permute(2, 0, 1).unsqueeze(0)
    pred_tensor = torch.from_numpy(pred_rgb).permute(2, 0, 1).unsqueeze(0)
    score = metric(pred_tensor, gt_tensor)
    return float(score.detach().cpu().item()) if hasattr(score, "detach") else float(score)


def evaluate_pair_paths(
    paths: PairPaths,
    method: str,
    scene: str,
    pair_id: str,
    skip_niqe: bool = False,
    skip_lpips: bool = False,
    device: str = "cpu",
) -> dict:
    if not paths.raw_path.exists():
        return {
            "scene": scene,
            "pair_id": pair_id,
            "pair_name": paths.pair_name,
            "method": method,
            "status": "failed",
            "failure_reason": f"Missing raw image: {paths.raw_path}",
            "mdr": math.nan,
            "niqe": math.nan,
            "psnr": math.nan,
            "ssim": math.nan,
            "lpips": math.nan,
            "rmse": math.nan,
            "runtime_seconds": paths.runtime_seconds,
            "valid_ratio": math.nan,
            "alignment_matcher": ALIGNMENT_MATCHER,
            "alignment_matches": 0,
            "alignment_inliers": 0,
            "valid_mask_strategy": VALID_MASK_STRATEGY,
            "lpips_max_side": LPIPS_MAX_SIDE,
            "raw_path": str(paths.raw_path),
            "aligned_path": str(paths.aligned_path),
            "valid_mask_path": str(paths.valid_mask_path),
            "gt_path": str(paths.gt_path),
            "cpp_mdr": math.nan,
            "cpp_warping_residual_avg": math.nan,
            "cpp_warping_residual_sd": math.nan,
            "gt_width": 0,
            "gt_height": 0,
        }

    raw_bgr = load_image_bgr(paths.raw_path)
    gt_bgr = load_image_bgr(paths.gt_path)
    aligned_bgr, align_stats = align_to_gt(raw_bgr, gt_bgr)
    paths.method_dir.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(paths.aligned_path), aligned_bgr)

    valid_mask = edge_connected_black_canvas_mask(aligned_bgr)
    cv2.imwrite(str(paths.valid_mask_path), valid_mask * 255)
    valid_ratio = float(valid_mask.mean())

    psnr, ssim = compute_psnr_ssim(gt_bgr, aligned_bgr, valid_mask)
    rmse = compute_image_rmse(gt_bgr, aligned_bgr, valid_mask)
    niqe = compute_niqe(raw_bgr, device=device) if not skip_niqe else math.nan
    lpips = compute_lpips(gt_bgr, aligned_bgr, device=device) if not skip_lpips else math.nan

    cpp_mdr = parse_rmse(paths.cpp_rmse_path) if paths.cpp_rmse_path else math.nan
    cpp_avg, cpp_sd = parse_warping(paths.cpp_residual_path) if paths.cpp_residual_path else (math.nan, math.nan)

    height, width = gt_bgr.shape[:2]
    return {
        "scene": scene,
        "pair_id": pair_id,
        "pair_name": paths.pair_name,
        "method": method,
        "status": "success",
        "failure_reason": "",
        "mdr": align_stats.get("mdr", math.nan),
        "niqe": niqe,
        "psnr": psnr,
        "ssim": ssim,
        "lpips": lpips,
        "rmse": rmse,
        "runtime_seconds": paths.runtime_seconds,
        "valid_ratio": valid_ratio,
        "alignment_matcher": align_stats.get("alignment_matcher", ALIGNMENT_MATCHER),
        "alignment_matches": align_stats.get("alignment_matches", 0),
        "alignment_inliers": align_stats.get("alignment_inliers", 0),
        "valid_mask_strategy": VALID_MASK_STRATEGY,
        "lpips_max_side": LPIPS_MAX_SIDE,
        "raw_path": str(paths.raw_path),
        "aligned_path": str(paths.aligned_path),
        "valid_mask_path": str(paths.valid_mask_path),
        "gt_path": str(paths.gt_path),
        "cpp_mdr": cpp_mdr,
        "cpp_warping_residual_avg": cpp_avg,
        "cpp_warping_residual_sd": cpp_sd,
        "gt_width": width,
        "gt_height": height,
    }
