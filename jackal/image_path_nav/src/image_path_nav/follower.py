"""Forward-only pure-pursuit follower over an ordered waypoint list.

Independent of the image UI: feed (x,y) waypoints in a fixed frame and the
robot's current pose in that frame.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple
import math
import time


@dataclass
class FollowerConfig:
    linear_speed_mps: float = 0.1
    max_angular_speed_rps: float = 0.4
    goal_tolerance_m: float = 0.15
    lookahead_m: float = 0.35
    slowdown_distance_m: float = 0.4
    heading_align_rad: float = 0.6  # turn in place if |heading error| above this
    min_linear_when_aligned_mps: float = 0.05
    stale_odom_timeout_s: float = 0.5
    no_progress_timeout_s: float = 8.0
    no_progress_epsilon_m: float = 0.05
    control_rate_hz: float = 20.0


@dataclass
class TwistCommand:
    linear_x: float = 0.0
    angular_z: float = 0.0


@dataclass
class FollowerState:
    active: bool = False
    completed: bool = False
    cancelled: bool = False
    failed: bool = False
    message: str = 'idle'
    waypoint_index: int = 0
    waypoint_count: int = 0
    dist_to_waypoint_m: float = 0.0
    path_length_m: float = 0.0
    last_progress_time: float = field(default_factory=time.time)
    last_progress_pose: Optional[Tuple[float, float]] = None


def wrap_angle(a: float) -> float:
    while a > math.pi:
        a -= 2.0 * math.pi
    while a < -math.pi:
        a += 2.0 * math.pi
    return a


def yaw_from_quaternion(qx: float, qy: float, qz: float, qw: float) -> float:
    siny_cosp = 2.0 * (qw * qz + qx * qy)
    cosy_cosp = 1.0 - 2.0 * (qy * qy + qz * qz)
    return math.atan2(siny_cosp, cosy_cosp)


class PurePursuitFollower:
    def __init__(self, config: Optional[FollowerConfig] = None):
        self.config = config or FollowerConfig()
        self.waypoints: List[Tuple[float, float]] = []
        self.state = FollowerState()

    def set_path(self, waypoints_xy: Sequence[Tuple[float, float]]) -> None:
        """Replace the active path. Empty list cancels."""
        self.waypoints = [(float(x), float(y)) for x, y in waypoints_xy]
        self.state = FollowerState(
            active=len(self.waypoints) > 0,
            waypoint_count=len(self.waypoints),
            path_length_m=self._path_length(self.waypoints),
            last_progress_time=time.time(),
        )
        if not self.waypoints:
            self.state.cancelled = True
            self.state.message = 'cancelled (empty path)'
        else:
            self.state.message = f'following {len(self.waypoints)} waypoints'

    def cancel(self) -> None:
        self.waypoints = []
        self.state.active = False
        self.state.cancelled = True
        self.state.message = 'cancelled'
        self.state.waypoint_count = 0

    def _path_length(self, pts: Sequence[Tuple[float, float]]) -> float:
        total = 0.0
        for i in range(1, len(pts)):
            total += math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])
        return total

    def _advance_waypoints(self, x: float, y: float) -> None:
        cfg = self.config
        while self.state.waypoint_index < len(self.waypoints) - 1:
            wx, wy = self.waypoints[self.state.waypoint_index]
            if math.hypot(wx - x, wy - y) <= cfg.goal_tolerance_m:
                self.state.waypoint_index += 1
            else:
                break

    def _lookahead_point(self, x: float, y: float) -> Tuple[float, float]:
        cfg = self.config
        idx = self.state.waypoint_index
        # Walk forward along remaining polyline until lookahead distance.
        remaining = list(self.waypoints[idx:])
        if not remaining:
            return x, y
        if len(remaining) == 1:
            return remaining[0]

        traveled = 0.0
        px, py = x, y
        # Start from robot toward first remaining waypoint, then along segments.
        pts = [(px, py)] + remaining
        for i in range(1, len(pts)):
            x0, y0 = pts[i - 1]
            x1, y1 = pts[i]
            seg = math.hypot(x1 - x0, y1 - y0)
            if seg < 1e-9:
                continue
            if traveled + seg >= cfg.lookahead_m:
                t = (cfg.lookahead_m - traveled) / seg
                return x0 + t * (x1 - x0), y0 + t * (y1 - y0)
            traveled += seg
        return remaining[-1]

    def step(
        self,
        x: float,
        y: float,
        yaw: float,
        odom_age_s: float,
        now: Optional[float] = None,
    ) -> TwistCommand:
        """Compute one control step. Returns zero twist when inactive/failed."""
        now = time.time() if now is None else now
        cfg = self.config
        zero = TwistCommand()

        if not self.state.active or not self.waypoints:
            return zero

        if odom_age_s > cfg.stale_odom_timeout_s:
            self.state.active = False
            self.state.failed = True
            self.state.message = f'stale odometry ({odom_age_s:.2f}s)'
            return zero

        # Progress watchdog
        if self.state.last_progress_pose is None:
            self.state.last_progress_pose = (x, y)
            self.state.last_progress_time = now
        else:
            moved = math.hypot(x - self.state.last_progress_pose[0],
                               y - self.state.last_progress_pose[1])
            if moved >= cfg.no_progress_epsilon_m:
                self.state.last_progress_pose = (x, y)
                self.state.last_progress_time = now
            elif now - self.state.last_progress_time > cfg.no_progress_timeout_s:
                self.state.active = False
                self.state.failed = True
                self.state.message = 'no progress timeout'
                return zero

        self._advance_waypoints(x, y)
        gx, gy = self.waypoints[-1]
        dist_goal = math.hypot(gx - x, gy - y)
        self.state.dist_to_waypoint_m = dist_goal

        if self.state.waypoint_index >= len(self.waypoints) - 1 and dist_goal <= cfg.goal_tolerance_m:
            self.state.active = False
            self.state.completed = True
            self.state.message = 'completed'
            return zero

        lx, ly = self._lookahead_point(x, y)
        bearing = math.atan2(ly - y, lx - x)
        heading_error = wrap_angle(bearing - yaw)

        # Forward-only: rotate in place when heading error is large.
        if abs(heading_error) > cfg.heading_align_rad:
            w = max(-cfg.max_angular_speed_rps,
                    min(cfg.max_angular_speed_rps, 1.5 * heading_error))
            self.state.message = 'aligning heading'
            return TwistCommand(linear_x=0.0, angular_z=w)

        speed = cfg.linear_speed_mps
        if dist_goal < cfg.slowdown_distance_m:
            speed = max(
                cfg.min_linear_when_aligned_mps,
                cfg.linear_speed_mps * (dist_goal / cfg.slowdown_distance_m),
            )

        # Pure pursuit curvature for differential drive approximation.
        # lateral error in robot frame:
        dx = lx - x
        dy = ly - y
        local_y = -math.sin(yaw) * dx + math.cos(yaw) * dy
        lookahead = max(cfg.lookahead_m, 1e-3)
        curvature = 2.0 * local_y / (lookahead * lookahead)
        w = speed * curvature
        w = max(-cfg.max_angular_speed_rps, min(cfg.max_angular_speed_rps, w))

        self.state.message = (
            f'following wp {self.state.waypoint_index + 1}/{self.state.waypoint_count}'
        )
        return TwistCommand(linear_x=speed, angular_z=w)
