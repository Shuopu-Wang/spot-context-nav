"""Pixel-to-3D projection helpers (no ROS dependency).

Depth is expected as float meters (ZED 32FC1) aligned to the RGB image.
Back-projection uses the pinhole model in the optical frame (x right, y down, z forward).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple
import math

import numpy as np


@dataclass
class ProjectedPoint:
    u: int
    v: int
    status: str  # ok|invalid_depth|out_of_range|not_floor|oob|gap
    camera_xyz: Optional[Tuple[float, float, float]] = None  # optical frame meters
    path_xyz: Optional[Tuple[float, float, float]] = None    # path/odom frame meters
    depth_m: Optional[float] = None


@dataclass
class ProjectionResult:
    points: List[ProjectedPoint] = field(default_factory=list)
    accepted_xyz: List[Tuple[float, float, float]] = field(default_factory=list)
    success: bool = False
    message: str = ''

    @property
    def accepted_count(self) -> int:
        return len(self.accepted_xyz)

    @property
    def rejected_count(self) -> int:
        return sum(1 for p in self.points if p.status != 'ok')


def display_to_image_pixel(
    click_x: float,
    click_y: float,
    display_w: float,
    display_h: float,
    image_w: int,
    image_h: int,
    letterbox: bool = True,
) -> Optional[Tuple[int, int]]:
    """Map a click in a possibly letterboxed/resized display into original image pixels.

    Assumes the image is scaled uniformly to fit inside the display element
    and centered (CSS object-fit: contain). Returns None if the click lands
    in the letterbox margin.
    """
    if display_w <= 0 or display_h <= 0 or image_w <= 0 or image_h <= 0:
        return None

    if not letterbox:
        u = int(round(click_x * image_w / display_w))
        v = int(round(click_y * image_h / display_h))
    else:
        scale = min(display_w / image_w, display_h / image_h)
        drawn_w = image_w * scale
        drawn_h = image_h * scale
        offset_x = (display_w - drawn_w) * 0.5
        offset_y = (display_h - drawn_h) * 0.5
        if click_x < offset_x or click_x > offset_x + drawn_w:
            return None
        if click_y < offset_y or click_y > offset_y + drawn_h:
            return None
        u = int(round((click_x - offset_x) / scale))
        v = int(round((click_y - offset_y) / scale))

    if u < 0 or v < 0 or u >= image_w or v >= image_h:
        return None
    return u, v


def sample_depth_m(depth: np.ndarray, u: int, v: int, window: int = 0) -> Optional[float]:
    """Return depth in meters at (u,v).

    Default window=0 requires a valid center pixel (does not invent depth from
    neighbors). window>0 allows a small median fill only when the center is
    invalid — still returns None if the neighborhood has no valid samples.
    """
    h, w = depth.shape[:2]
    if u < 0 or v < 0 or u >= w or v >= h:
        return None
    center = float(depth[v, u])
    if math.isfinite(center) and center > 0.0:
        return center
    if window <= 0:
        return None
    u0 = max(0, u - window)
    u1 = min(w, u + window + 1)
    v0 = max(0, v - window)
    v1 = min(h, v + window + 1)
    patch = depth[v0:v1, u0:u1].astype(np.float64).reshape(-1)
    valid = patch[np.isfinite(patch) & (patch > 0.0)]
    if valid.size == 0:
        return None
    return float(np.median(valid))


def backproject_optical(
    u: int,
    v: int,
    depth_m: float,
    fx: float,
    fy: float,
    cx: float,
    cy: float,
) -> Tuple[float, float, float]:
    """Pinhole back-project into camera optical frame (x right, y down, z forward)."""
    x = (u - cx) * depth_m / fx
    y = (v - cy) * depth_m / fy
    z = depth_m
    return x, y, z


def transform_points(
    points_xyz: Sequence[Tuple[float, float, float]],
    rotation_3x3: np.ndarray,
    translation_3: np.ndarray,
) -> List[Tuple[float, float, float]]:
    """Apply T: p_out = R * p_in + t."""
    out = []
    for x, y, z in points_xyz:
        p = rotation_3x3.dot(np.array([x, y, z], dtype=np.float64)) + translation_3
        out.append((float(p[0]), float(p[1]), float(p[2])))
    return out


def quaternion_to_rotation_matrix(qx: float, qy: float, qz: float, qw: float) -> np.ndarray:
    n = math.sqrt(qx * qx + qy * qy + qz * qz + qw * qw)
    if n < 1e-12:
        return np.eye(3)
    qx, qy, qz, qw = qx / n, qy / n, qz / n, qw / n
    return np.array([
        [1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qz * qw), 2 * (qx * qz + qy * qw)],
        [2 * (qx * qy + qz * qw), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qx * qw)],
        [2 * (qx * qz - qy * qw), 2 * (qy * qz + qx * qw), 1 - 2 * (qx * qx + qy * qy)],
    ], dtype=np.float64)


def project_pixel_path(
    us: Sequence[int],
    vs: Sequence[int],
    depth: np.ndarray,
    fx: float,
    fy: float,
    cx: float,
    cy: float,
    rotation_cam_to_path: np.ndarray,
    translation_cam_to_path: np.ndarray,
    min_depth_m: float = 0.3,
    max_depth_m: float = 8.0,
    floor_z_center_m: Optional[float] = None,
    floor_z_tolerance_m: float = 0.25,
    max_gap_m: float = 1.5,
    depth_window: int = 0,
    require_all_valid: bool = False,
) -> ProjectionResult:
    """Project ordered image pixels to a path in the target frame.

    Floor check (flat-ground prototype): after transforming to path_frame, the
    point's Z must lie near floor_z_center_m (default: median Z of accepted
    candidates). Points that fail depth/floor checks are rejected with an
    explicit status — never silently snapped to the floor.
    """
    result = ProjectionResult()
    if len(us) != len(vs):
        result.message = 'u/v length mismatch'
        return result
    if len(us) == 0:
        result.message = 'empty pixel path'
        return result

    h, w = depth.shape[:2]
    cam_pts: List[ProjectedPoint] = []

    for u, v in zip(us, vs):
        pt = ProjectedPoint(u=int(u), v=int(v), status='ok')
        if u < 0 or v < 0 or u >= w or v >= h:
            pt.status = 'oob'
            cam_pts.append(pt)
            continue
        d = sample_depth_m(depth, int(u), int(v), window=depth_window)
        if d is None or not math.isfinite(d):
            pt.status = 'invalid_depth'
            cam_pts.append(pt)
            continue
        if d < min_depth_m or d > max_depth_m:
            pt.status = 'out_of_range'
            pt.depth_m = d
            cam_pts.append(pt)
            continue
        xyz = backproject_optical(int(u), int(v), d, fx, fy, cx, cy)
        pt.depth_m = d
        pt.camera_xyz = xyz
        cam_pts.append(pt)

    # Transform valid camera points
    valid_idx = [i for i, p in enumerate(cam_pts) if p.camera_xyz is not None]
    if not valid_idx:
        result.points = cam_pts
        result.message = 'no valid depth samples'
        return result

    cam_xyz = [cam_pts[i].camera_xyz for i in valid_idx]  # type: ignore
    path_xyz = transform_points(cam_xyz, rotation_cam_to_path, translation_cam_to_path)
    for i, xyz in zip(valid_idx, path_xyz):
        cam_pts[i].path_xyz = xyz

    zs = [cam_pts[i].path_xyz[2] for i in valid_idx]  # type: ignore
    z_ref = floor_z_center_m if floor_z_center_m is not None else float(np.median(zs))

    accepted: List[Tuple[float, float, float]] = []
    last_accepted: Optional[Tuple[float, float, float]] = None
    for p in cam_pts:
        if p.path_xyz is None:
            continue
        if abs(p.path_xyz[2] - z_ref) > floor_z_tolerance_m:
            p.status = 'not_floor'
            p.path_xyz = None
            continue
        if last_accepted is not None:
            gap = math.dist(last_accepted[:2], p.path_xyz[:2])
            if gap > max_gap_m:
                p.status = 'gap'
                p.path_xyz = None
                continue
        # Flatten Z to reference for driving on assumed-flat floor (keep check above).
        flat = (p.path_xyz[0], p.path_xyz[1], z_ref)
        p.path_xyz = flat
        p.status = 'ok'
        accepted.append(flat)
        last_accepted = flat

    result.points = cam_pts
    result.accepted_xyz = accepted

    if require_all_valid and any(p.status != 'ok' for p in cam_pts):
        result.success = False
        result.message = 'one or more points rejected; require_all_valid=true'
        result.accepted_xyz = []
        return result

    if len(accepted) == 0:
        result.success = False
        result.message = 'all points rejected (depth/floor/gap)'
        return result

    if len(accepted) < len(cam_pts):
        result.success = True
        result.message = (
            f'projected {len(accepted)}/{len(cam_pts)} points; '
            f'rejected={sum(1 for p in cam_pts if p.status != "ok")}'
        )
    else:
        result.success = True
        result.message = f'projected {len(accepted)} points'
    return result


def resample_polyline(
    points_xy: Sequence[Tuple[float, float]],
    spacing_m: float,
) -> List[Tuple[float, float]]:
    """Resample an ordered polyline at approximately spacing_m without cutting corners."""
    if len(points_xy) == 0:
        return []
    if len(points_xy) == 1 or spacing_m <= 0:
        return [(float(p[0]), float(p[1])) for p in points_xy]

    out: List[Tuple[float, float]] = [(float(points_xy[0][0]), float(points_xy[0][1]))]
    carry = 0.0
    for i in range(1, len(points_xy)):
        x0, y0 = out[-1] if carry == 0.0 and i == 1 else (points_xy[i - 1][0], points_xy[i - 1][1])
        x0, y0 = points_xy[i - 1]
        x1, y1 = points_xy[i]
        seg_dx = x1 - x0
        seg_dy = y1 - y0
        seg_len = math.hypot(seg_dx, seg_dy)
        if seg_len < 1e-9:
            continue
        dist = spacing_m - carry
        while dist <= seg_len:
            t = dist / seg_len
            out.append((float(x0 + t * seg_dx), float(y0 + t * seg_dy)))
            dist += spacing_m
        carry = seg_len - (dist - spacing_m)
    # Always keep the true endpoint so the robot stops on the last click.
    ex, ey = float(points_xy[-1][0]), float(points_xy[-1][1])
    if math.hypot(out[-1][0] - ex, out[-1][1] - ey) > 1e-3:
        out.append((ex, ey))
    return out


def path_length_m(points_xy: Sequence[Tuple[float, float]]) -> float:
    total = 0.0
    for i in range(1, len(points_xy)):
        total += math.hypot(points_xy[i][0] - points_xy[i - 1][0],
                            points_xy[i][1] - points_xy[i - 1][1])
    return total
