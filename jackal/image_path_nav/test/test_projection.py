#!/usr/bin/env python3
"""Unit tests for projection + display mapping + follower (no ROS master required)."""

from __future__ import annotations

import math
import os
import sys
import unittest

import numpy as np

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src')
sys.path.insert(0, ROOT)

from image_path_nav.projection import (
    backproject_optical,
    display_to_image_pixel,
    path_length_m,
    project_pixel_path,
    resample_polyline,
)
from image_path_nav.follower import FollowerConfig, PurePursuitFollower


class TestDisplayMapping(unittest.TestCase):
    def test_letterbox_center(self):
        # Image 400x200 in display 400x400 -> letterbox top/bottom 100px each
        mapped = display_to_image_pixel(200, 200, 400, 400, 400, 200, letterbox=True)
        self.assertEqual(mapped, (200, 100))

    def test_letterbox_margin_rejected(self):
        self.assertIsNone(display_to_image_pixel(10, 10, 400, 400, 400, 200, letterbox=True))

    def test_stretch_mode(self):
        mapped = display_to_image_pixel(100, 50, 200, 100, 400, 200, letterbox=False)
        self.assertEqual(mapped, (200, 100))


class TestBackproject(unittest.TestCase):
    def test_optical_units_meters(self):
        # Principal point, depth 2m -> (0,0,2)
        x, y, z = backproject_optical(320, 240, 2.0, fx=500, fy=500, cx=320, cy=240)
        self.assertAlmostEqual(x, 0.0, places=6)
        self.assertAlmostEqual(y, 0.0, places=6)
        self.assertAlmostEqual(z, 2.0, places=6)

    def test_offset_pixel(self):
        x, y, z = backproject_optical(820, 240, 2.0, fx=500, fy=500, cx=320, cy=240)
        self.assertAlmostEqual(x, 2.0, places=6)  # (500px * 2 / 500)
        self.assertAlmostEqual(z, 2.0, places=6)


class TestProjectPath(unittest.TestCase):
    def _flat_depth(self, h, w, depth):
        d = np.full((h, w), depth, dtype=np.float32)
        return d

    def test_identity_transform_floor(self):
        # Optical frame aligned with path frame for simplicity: points with z=depth
        # Use a transform that maps optical (x right,y down,z fwd) -> a frame where
        # floor Z is constant. Here identity: "floor" check uses median Z.
        depth = self._flat_depth(100, 100, 3.0)
        # Punch invalid hole
        depth[50, 50] = np.nan
        R = np.eye(3)
        t = np.zeros(3)
        res = project_pixel_path(
            us=[40, 50, 60],
            vs=[50, 50, 50],
            depth=depth,
            fx=100, fy=100, cx=50, cy=50,
            rotation_cam_to_path=R,
            translation_cam_to_path=t,
            min_depth_m=0.3,
            max_depth_m=8.0,
            floor_z_tolerance_m=0.5,
        )
        self.assertTrue(res.success)
        self.assertEqual(res.points[1].status, 'invalid_depth')
        self.assertEqual(res.accepted_count, 2)

    def test_out_of_range_rejected(self):
        depth = self._flat_depth(50, 50, 12.0)
        res = project_pixel_path(
            us=[25], vs=[25], depth=depth,
            fx=50, fy=50, cx=25, cy=25,
            rotation_cam_to_path=np.eye(3),
            translation_cam_to_path=np.zeros(3),
            max_depth_m=8.0,
        )
        self.assertFalse(res.success)
        self.assertEqual(res.points[0].status, 'out_of_range')

    def test_not_floor_rejected(self):
        depth = self._flat_depth(50, 50, 2.0)
        # Second point much closer -> different path Z under identity (Z=depth)
        depth[10, 25] = 0.5
        res = project_pixel_path(
            us=[25, 25], vs=[40, 10], depth=depth,
            fx=50, fy=50, cx=25, cy=25,
            rotation_cam_to_path=np.eye(3),
            translation_cam_to_path=np.zeros(3),
            floor_z_tolerance_m=0.2,
            # Force reference to first point's Z by providing floor_z_center
            floor_z_center_m=2.0,
        )
        self.assertEqual(res.points[0].status, 'ok')
        self.assertEqual(res.points[1].status, 'not_floor')

    def test_require_all_valid(self):
        depth = self._flat_depth(50, 50, 2.0)
        depth[25, 25] = np.nan
        res = project_pixel_path(
            us=[10, 25], vs=[25, 25], depth=depth,
            fx=50, fy=50, cx=25, cy=25,
            rotation_cam_to_path=np.eye(3),
            translation_cam_to_path=np.zeros(3),
            require_all_valid=True,
        )
        self.assertFalse(res.success)
        self.assertEqual(res.accepted_count, 0)


class TestResample(unittest.TestCase):
    def test_preserves_endpoint(self):
        pts = [(0.0, 0.0), (1.0, 0.0)]
        out = resample_polyline(pts, 0.3)
        self.assertAlmostEqual(out[0][0], 0.0)
        self.assertAlmostEqual(out[-1][0], 1.0)
        self.assertGreater(len(out), 2)

    def test_no_corner_cut(self):
        pts = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]
        out = resample_polyline(pts, 0.25)
        # Must pass near the corner (1,0)
        near_corner = any(math.hypot(x - 1.0, y - 0.0) < 1e-6 for x, y in out) or any(
            abs(x - 1.0) < 0.01 and abs(y) < 0.26 for x, y in out
        )
        self.assertTrue(near_corner)
        self.assertAlmostEqual(out[-1][0], 1.0)
        self.assertAlmostEqual(out[-1][1], 1.0)
        self.assertAlmostEqual(path_length_m(out), path_length_m(pts), places=2)


class TestFollower(unittest.TestCase):
    def test_stop_at_goal(self):
        f = PurePursuitFollower(FollowerConfig(goal_tolerance_m=0.1, linear_speed_mps=0.1))
        f.set_path([(0.0, 0.0)])
        cmd = f.step(x=0.0, y=0.0, yaw=0.0, odom_age_s=0.0)
        self.assertEqual(cmd.linear_x, 0.0)
        self.assertTrue(f.state.completed)

    def test_turns_left_for_left_target(self):
        f = PurePursuitFollower(FollowerConfig(
            goal_tolerance_m=0.05, lookahead_m=0.5, heading_align_rad=0.2,
            linear_speed_mps=0.1, max_angular_speed_rps=0.5,
        ))
        f.set_path([(0.0, 1.0)])  # left of robot facing +x
        cmd = f.step(x=0.0, y=0.0, yaw=0.0, odom_age_s=0.0)
        self.assertGreater(cmd.angular_z, 0.0)

    def test_cancel(self):
        f = PurePursuitFollower()
        f.set_path([(1.0, 0.0), (2.0, 0.0)])
        f.cancel()
        cmd = f.step(0.0, 0.0, 0.0, 0.0)
        self.assertEqual(cmd.linear_x, 0.0)
        self.assertTrue(f.state.cancelled)

    def test_stale_odom(self):
        f = PurePursuitFollower(FollowerConfig(stale_odom_timeout_s=0.2))
        f.set_path([(1.0, 0.0)])
        cmd = f.step(0.0, 0.0, 0.0, odom_age_s=1.0)
        self.assertEqual(cmd.linear_x, 0.0)
        self.assertTrue(f.state.failed)

    def test_path_replacement_no_mix(self):
        f = PurePursuitFollower()
        f.set_path([(1.0, 0.0), (2.0, 0.0), (3.0, 0.0)])
        f.state.waypoint_index = 2
        f.set_path([(0.0, 1.0)])
        self.assertEqual(f.state.waypoint_index, 0)
        self.assertEqual(len(f.waypoints), 1)
        self.assertAlmostEqual(f.waypoints[0][1], 1.0)

    def test_follows_multivway_not_just_endpoint(self):
        f = PurePursuitFollower(FollowerConfig(
            goal_tolerance_m=0.15, lookahead_m=0.3, heading_align_rad=1.0,
            linear_speed_mps=0.1,
        ))
        # Path goes up then right; robot at origin facing +x should initially
        # pursue toward first segment (not jump to final (1,1) only).
        f.set_path([(0.0, 1.0), (1.0, 1.0)])
        cmd = f.step(0.0, 0.0, yaw=math.pi / 2, odom_age_s=0.0)
        self.assertGreater(cmd.linear_x, 0.0)
        self.assertEqual(f.state.waypoint_index, 0)


if __name__ == '__main__':
    unittest.main()
