#!/usr/bin/env python3
"""Capture / project / web UI node for image-space path navigation.

Keeps RGB, depth, camera_info, and capture-time TF together. Projection never
reinterprets frozen pixels against a live moving image.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np
import rospy
import tf2_ros
from cv_bridge import CvBridge
from flask import Flask, Response, jsonify, request, send_from_directory
from geometry_msgs.msg import PoseStamped, TransformStamped
from nav_msgs.msg import Path
from sensor_msgs.msg import CameraInfo, Image, CompressedImage
from std_msgs.msg import Bool, Empty
from visualization_msgs.msg import Marker, MarkerArray
import message_filters

PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from image_path_nav.projection import (
    display_to_image_pixel,
    path_length_m,
    project_pixel_path,
    quaternion_to_rotation_matrix,
    resample_polyline,
)
from image_path_nav.msg import ExecutionStatus, PixelPath
from image_path_nav.srv import (
    CaptureFrame,
    CaptureFrameResponse,
    ProjectPixelPath,
    ProjectPixelPathResponse,
    FollowPath,
    SetArmed,
)


@dataclass
class CaptureBundle:
    capture_id: str
    stamp: rospy.Time
    optical_frame: str
    path_frame: str
    rgb_bgr: np.ndarray          # HxWx3 uint8 BGR
    depth_m: np.ndarray          # HxW float32 meters
    fx: float
    fy: float
    cx: float
    cy: float
    # transform optical -> path_frame at capture time
    rotation: np.ndarray
    translation: np.ndarray
    transform_ok: bool
    transform_error: str = ''


class ImagePathNavNode:
    def __init__(self):
        self.bridge = CvBridge()
        self.lock = threading.RLock()

        self.rgb_topic = rospy.get_param(
            '~rgb_topic', '/zed2i/zed_node/rgb/image_rect_color'
        )
        self.depth_topic = rospy.get_param(
            '~depth_topic', '/zed2i/zed_node/depth/depth_registered'
        )
        self.camera_info_topic = rospy.get_param(
            '~camera_info_topic', '/zed2i/zed_node/rgb/camera_info'
        )
        self.path_frame = rospy.get_param('~path_frame', 'odom')
        self.optical_frame_override = rospy.get_param('~optical_frame', '')
        self.min_depth_m = float(rospy.get_param('~min_depth_m', 0.3))
        self.max_depth_m = float(rospy.get_param('~max_depth_m', 8.0))
        self.floor_z_tolerance_m = float(rospy.get_param('~floor_z_tolerance_m', 0.35))
        self.max_gap_m = float(rospy.get_param('~max_gap_m', 1.5))
        self.resample_spacing_m = float(rospy.get_param('~resample_spacing_m', 0.15))
        self.require_all_valid = bool(rospy.get_param('~require_all_valid', False))
        self.web_host = rospy.get_param('~web_host', '127.0.0.1')
        self.web_port = int(rospy.get_param('~web_port', 8080))
        self.jpeg_quality = int(rospy.get_param('~jpeg_quality', 80))

        self.tf_buffer = tf2_ros.Buffer(cache_time=rospy.Duration(30.0))
        self.tf_listener = tf2_ros.TransformListener(self.tf_buffer)

        self.latest_rgb: Optional[Image] = None
        self.latest_depth: Optional[Image] = None
        self.latest_info: Optional[CameraInfo] = None
        self.capture: Optional[CaptureBundle] = None
        self.last_path = Path()
        self.last_projection_meta = {}
        self.follower_status = ExecutionStatus()
        self.camera_ready = False
        self.tf_ready = False

        self.pub_path = rospy.Publisher('/image_path_nav/path', Path, queue_size=1, latch=True)
        self.pub_markers = rospy.Publisher(
            '/image_path_nav/projection_markers', MarkerArray, queue_size=1, latch=True
        )
        self.pub_status = rospy.Publisher(
            '/image_path_nav/status', ExecutionStatus, queue_size=1, latch=True
        )
        self.pub_live_jpeg = rospy.Publisher(
            '/image_path_nav/live/compressed', CompressedImage, queue_size=1
        )
        self.pub_capture_jpeg = rospy.Publisher(
            '/image_path_nav/capture/compressed', CompressedImage, queue_size=1, latch=True
        )
        self.pub_heartbeat = rospy.Publisher(
            '/image_path_follower/ui_heartbeat', Bool, queue_size=1
        )
        self.pub_cancel = rospy.Publisher('/image_path_follower/cancel', Empty, queue_size=1)
        self.pub_ui_execute = rospy.Publisher(
            '/image_path_follower/ui_execute', Path, queue_size=1
        )

        # Approximate sync of rgb+depth; camera_info cached separately.
        self.rgb_sub = message_filters.Subscriber(self.rgb_topic, Image)
        self.depth_sub = message_filters.Subscriber(self.depth_topic, Image)
        self.sync = message_filters.ApproximateTimeSynchronizer(
            [self.rgb_sub, self.depth_sub], queue_size=10, slop=0.05
        )
        self.sync.registerCallback(self._sync_cb)
        rospy.Subscriber(self.camera_info_topic, CameraInfo, self._info_cb, queue_size=1)
        rospy.Subscriber(
            '/image_path_follower/status', ExecutionStatus, self._follower_status_cb, queue_size=1
        )

        rospy.Service('/image_path_nav/capture', CaptureFrame, self._capture_srv)
        rospy.Service('/image_path_nav/project', ProjectPixelPath, self._project_srv)

        self._start_web()
        rospy.Timer(rospy.Duration(0.2), self._status_tick)
        rospy.Timer(rospy.Duration(0.5), self._tf_check_tick)
        rospy.loginfo(
            'image_path_nav UI on http://%s:%d (SSH: -L %d:127.0.0.1:%d)',
            self.web_host, self.web_port, self.web_port, self.web_port,
        )

    def _info_cb(self, msg: CameraInfo):
        self.latest_info = msg

    def _follower_status_cb(self, msg: ExecutionStatus):
        self.follower_status = msg

    def _sync_cb(self, rgb: Image, depth: Image):
        with self.lock:
            self.latest_rgb = rgb
            self.latest_depth = depth
            self.camera_ready = True
        # Publish a downsized live JPEG for the UI (~5 Hz capped by timer below would be better)
        # Keep light: only encode if someone is listening OR periodically via status.
        try:
            if self.pub_live_jpeg.get_num_connections() > 0:
                bgr = self._ros_to_bgr(rgb)
                self._publish_jpeg(self.pub_live_jpeg, bgr, rgb.header.stamp)
        except Exception as exc:
            rospy.logwarn_throttle(5.0, 'live jpeg encode failed: %s', exc)

    def _ros_to_bgr(self, msg: Image) -> np.ndarray:
        if msg.encoding in ('bgra8', 'rgba8'):
            img = self.bridge.imgmsg_to_cv2(msg, desired_encoding=msg.encoding)
            return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR if msg.encoding == 'bgra8'
                                else cv2.COLOR_RGBA2BGR)
        if msg.encoding in ('bgr8', 'rgb8'):
            img = self.bridge.imgmsg_to_cv2(msg, desired_encoding=msg.encoding)
            return img if msg.encoding == 'bgr8' else cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        # Fallback
        img = self.bridge.imgmsg_to_cv2(msg, desired_encoding='bgr8')
        return img

    def _ros_to_depth_m(self, msg: Image) -> np.ndarray:
        if msg.encoding == '32FC1':
            return np.frombuffer(msg.data, dtype=np.float32).reshape(msg.height, msg.width).copy()
        if msg.encoding == '16UC1':
            raw = np.frombuffer(msg.data, dtype=np.uint16).reshape(msg.height, msg.width)
            return raw.astype(np.float32) * 0.001
        raise ValueError(f'unsupported depth encoding {msg.encoding}')

    def _publish_jpeg(self, pub, bgr: np.ndarray, stamp: rospy.Time):
        ok, buf = cv2.imencode('.jpg', bgr, [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality])
        if not ok:
            return
        msg = CompressedImage()
        msg.header.stamp = stamp
        msg.header.frame_id = 'capture'
        msg.format = 'jpeg'
        msg.data = buf.tobytes()
        pub.publish(msg)

    def _depth_validity_overlay(self, bgr: np.ndarray, depth_m: np.ndarray) -> np.ndarray:
        """Tint valid depth green and invalid magenta on top of RGB (same thresholds as projection)."""
        if bgr.shape[:2] != depth_m.shape[:2]:
            raise ValueError('rgb/depth size mismatch for overlay')
        valid = (
            np.isfinite(depth_m)
            & (depth_m >= self.min_depth_m)
            & (depth_m <= self.max_depth_m)
        )
        out = bgr.copy()
        # Dim base image slightly, then colorize masks.
        out = (out.astype(np.float32) * 0.45).astype(np.uint8)
        green = np.zeros_like(out)
        green[:, :, 1] = 220
        magenta = np.zeros_like(out)
        magenta[:, :, 0] = 200
        magenta[:, :, 2] = 200
        out[valid] = cv2.addWeighted(out, 0.35, green, 0.65, 0)[valid]
        out[~valid] = cv2.addWeighted(out, 0.45, magenta, 0.55, 0)[~valid]
        # Legend bar
        cv2.rectangle(out, (8, 8), (210, 48), (20, 20, 20), -1)
        cv2.putText(
            out, 'green=valid depth', (14, 28),
            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 255, 180), 1, cv2.LINE_AA,
        )
        cv2.putText(
            out, 'magenta=invalid', (14, 44),
            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 160, 255), 1, cv2.LINE_AA,
        )
        pct = 100.0 * float(valid.mean())
        cv2.putText(
            out, f'{pct:.0f}% valid  [{self.min_depth_m:.1f},{self.max_depth_m:.1f}]m',
            (8, out.shape[0] - 10),
            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (230, 230, 230), 1, cv2.LINE_AA,
        )
        return out

    def _encode_jpeg(self, bgr: np.ndarray) -> Optional[bytes]:
        ok, buf = cv2.imencode('.jpg', bgr, [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality])
        return buf.tobytes() if ok else None

    def _lookup_optical_to_path(self, optical_frame: str, stamp: rospy.Time):
        try:
            # Prefer exact stamp; fall back to latest within 0.2s
            try:
                tf = self.tf_buffer.lookup_transform(
                    self.path_frame, optical_frame, stamp, rospy.Duration(0.2)
                )
            except Exception:
                tf = self.tf_buffer.lookup_transform(
                    self.path_frame, optical_frame, rospy.Time(0), rospy.Duration(0.5)
                )
            t = tf.transform.translation
            q = tf.transform.rotation
            R = quaternion_to_rotation_matrix(q.x, q.y, q.z, q.w)
            T = np.array([t.x, t.y, t.z], dtype=np.float64)
            return R, T, True, ''
        except Exception as exc:
            return np.eye(3), np.zeros(3), False, str(exc)

    def capture_frame(self) -> CaptureFrameResponse:
        with self.lock:
            rgb = self.latest_rgb
            depth = self.latest_depth
            info = self.latest_info
        if rgb is None or depth is None:
            return CaptureFrameResponse(
                success=False, message='no synchronized rgb/depth yet',
                capture_id='', image_width=0, image_height=0,
                capture_stamp=rospy.Time(0), optical_frame='', path_frame=self.path_frame,
            )
        if info is None:
            return CaptureFrameResponse(
                success=False, message='missing camera_info — cannot project safely',
                capture_id='', image_width=0, image_height=0,
                capture_stamp=rospy.Time(0), optical_frame='', path_frame=self.path_frame,
            )
        if rgb.width != depth.width or rgb.height != depth.height:
            return CaptureFrameResponse(
                success=False,
                message=f'rgb/depth size mismatch {rgb.width}x{rgb.height} vs {depth.width}x{depth.height}',
                capture_id='', image_width=0, image_height=0,
                capture_stamp=rospy.Time(0), optical_frame='', path_frame=self.path_frame,
            )

        optical = self.optical_frame_override or rgb.header.frame_id
        R, T, ok, err = self._lookup_optical_to_path(optical, rgb.header.stamp)
        if not ok:
            return CaptureFrameResponse(
                success=False,
                message=f'capture-time TF {self.path_frame} <- {optical} unavailable: {err}',
                capture_id='', image_width=0, image_height=0,
                capture_stamp=rgb.header.stamp, optical_frame=optical, path_frame=self.path_frame,
            )

        try:
            bgr = self._ros_to_bgr(rgb)
            depth_m = self._ros_to_depth_m(depth)
        except Exception as exc:
            return CaptureFrameResponse(
                success=False, message=f'image convert failed: {exc}',
                capture_id='', image_width=0, image_height=0,
                capture_stamp=rgb.header.stamp, optical_frame=optical, path_frame=self.path_frame,
            )

        K = info.K
        bundle = CaptureBundle(
            capture_id=str(uuid.uuid4())[:8],
            stamp=rgb.header.stamp,
            optical_frame=optical,
            path_frame=self.path_frame,
            rgb_bgr=bgr,
            depth_m=depth_m,
            fx=float(K[0]), fy=float(K[4]), cx=float(K[2]), cy=float(K[5]),
            rotation=R, translation=T,
            transform_ok=True,
        )
        with self.lock:
            self.capture = bundle
        self._publish_jpeg(self.pub_capture_jpeg, bgr, rgb.header.stamp)
        rospy.loginfo(
            'Captured %s %dx%d stamp=%.3f frame=%s',
            bundle.capture_id, bgr.shape[1], bgr.shape[0],
            bundle.stamp.to_sec(), optical,
        )
        return CaptureFrameResponse(
            success=True,
            message=f'captured {bundle.capture_id}',
            capture_id=bundle.capture_id,
            image_width=bgr.shape[1],
            image_height=bgr.shape[0],
            capture_stamp=bundle.stamp,
            optical_frame=optical,
            path_frame=self.path_frame,
        )

    def _capture_srv(self, _req):
        return self.capture_frame()

    def project_pixels(self, pixel_path: PixelPath, publish_result: bool = True) -> ProjectPixelPathResponse:
        with self.lock:
            cap = self.capture
        if cap is None:
            return ProjectPixelPathResponse(
                success=False, message='no capture — call capture first',
                path=Path(), point_statuses=[], accepted_count=0, rejected_count=0,
            )
        if pixel_path.capture_id and pixel_path.capture_id != cap.capture_id:
            return ProjectPixelPathResponse(
                success=False,
                message=f'capture_id mismatch: request={pixel_path.capture_id} current={cap.capture_id}',
                path=Path(), point_statuses=[], accepted_count=0, rejected_count=0,
            )
        if pixel_path.image_width and pixel_path.image_width != cap.rgb_bgr.shape[1]:
            return ProjectPixelPathResponse(
                success=False,
                message=f'image_width mismatch {pixel_path.image_width} != {cap.rgb_bgr.shape[1]}',
                path=Path(), point_statuses=[], accepted_count=0, rejected_count=0,
            )
        if pixel_path.image_height and pixel_path.image_height != cap.rgb_bgr.shape[0]:
            return ProjectPixelPathResponse(
                success=False,
                message=f'image_height mismatch {pixel_path.image_height} != {cap.rgb_bgr.shape[0]}',
                path=Path(), point_statuses=[], accepted_count=0, rejected_count=0,
            )
        if not cap.transform_ok:
            return ProjectPixelPathResponse(
                success=False, message=f'missing capture TF: {cap.transform_error}',
                path=Path(), point_statuses=[], accepted_count=0, rejected_count=0,
            )
        if cap.fx == 0 or cap.fy == 0:
            return ProjectPixelPathResponse(
                success=False, message='invalid camera intrinsics (fx/fy=0)',
                path=Path(), point_statuses=[], accepted_count=0, rejected_count=0,
            )

        result = project_pixel_path(
            us=list(pixel_path.u),
            vs=list(pixel_path.v),
            depth=cap.depth_m,
            fx=cap.fx, fy=cap.fy, cx=cap.cx, cy=cap.cy,
            rotation_cam_to_path=cap.rotation,
            translation_cam_to_path=cap.translation,
            min_depth_m=self.min_depth_m,
            max_depth_m=self.max_depth_m,
            floor_z_tolerance_m=self.floor_z_tolerance_m,
            max_gap_m=self.max_gap_m,
            require_all_valid=self.require_all_valid,
        )

        path = Path()
        path.header.stamp = cap.stamp
        path.header.frame_id = cap.path_frame
        statuses = [p.status for p in result.points]

        if result.success and result.accepted_xyz:
            xy = [(p[0], p[1]) for p in result.accepted_xyz]
            resampled = resample_polyline(xy, self.resample_spacing_m)
            z_ref = result.accepted_xyz[0][2]
            for x, y in resampled:
                ps = PoseStamped()
                ps.header = path.header
                ps.pose.position.x = x
                ps.pose.position.y = y
                ps.pose.position.z = z_ref
                ps.pose.orientation.w = 1.0
                path.poses.append(ps)

        if publish_result:
            self.pub_path.publish(path)
            self._publish_projection_markers(path, result, cap)
            self.last_path = path
            self.last_projection_meta = {
                'message': result.message,
                'statuses': statuses,
                'accepted': result.accepted_count,
                'rejected': result.rejected_count,
                'capture_id': cap.capture_id,
                'length_m': path_length_m([(p.pose.position.x, p.pose.position.y) for p in path.poses]),
            }

        return ProjectPixelPathResponse(
            success=result.success,
            message=result.message,
            path=path,
            point_statuses=statuses,
            accepted_count=result.accepted_count,
            rejected_count=result.rejected_count,
        )

    def _project_srv(self, req):
        return self.project_pixels(req.pixel_path, publish_result=req.publish_result)

    def _publish_projection_markers(self, path: Path, result, cap: CaptureBundle):
        ma = MarkerArray()
        clear = Marker()
        clear.action = Marker.DELETEALL
        ma.markers.append(clear)
        if path.poses:
            line = Marker()
            line.header = path.header
            line.ns = 'projected_path'
            line.id = 1
            line.type = Marker.LINE_STRIP
            line.action = Marker.ADD
            line.scale.x = 0.05
            line.color.r = 0.2
            line.color.g = 0.6
            line.color.b = 1.0
            line.color.a = 1.0
            line.pose.orientation.w = 1.0
            from geometry_msgs.msg import Point
            for ps in path.poses:
                line.points.append(Point(
                    x=ps.pose.position.x, y=ps.pose.position.y, z=ps.pose.position.z + 0.05
                ))
            ma.markers.append(line)
        self.pub_markers.publish(ma)

    def _tf_check_tick(self, _evt):
        optical = self.optical_frame_override
        with self.lock:
            if self.latest_rgb is not None:
                optical = optical or self.latest_rgb.header.frame_id
        if not optical:
            self.tf_ready = False
            return
        try:
            self.tf_buffer.lookup_transform(
                self.path_frame, optical, rospy.Time(0), rospy.Duration(0.1)
            )
            self.tf_ready = True
        except Exception:
            self.tf_ready = False

    def _status_tick(self, _evt):
        msg = ExecutionStatus()
        fs = self.follower_status
        msg.state = fs.state or 'idle'
        msg.armed = fs.armed
        msg.allow_cmd_vel = fs.allow_cmd_vel
        msg.deadman_required = fs.deadman_required
        msg.deadman_held = fs.deadman_held
        msg.deadman_hint = fs.deadman_hint or ''
        msg.camera_ready = self.camera_ready
        msg.odom_ready = fs.odom_ready
        msg.tf_ready = self.tf_ready
        with self.lock:
            msg.capture_ready = self.capture is not None
            msg.capture_id = self.capture.capture_id if self.capture else ''
        msg.message = fs.message or ''
        msg.path_length_m = fs.path_length_m
        msg.waypoint_index = fs.waypoint_index
        msg.waypoint_count = fs.waypoint_count
        msg.dist_to_waypoint_m = fs.dist_to_waypoint_m
        msg.ui_heartbeat_age_s = fs.ui_heartbeat_age_s
        self.pub_status.publish(msg)

    # ---------------- Web UI ----------------
    def _start_web(self):
        web_dir = os.path.join(PKG_ROOT, 'web')
        app = Flask(__name__, static_folder=web_dir, static_url_path='')
        node = self

        @app.route('/')
        def index():
            return send_from_directory(web_dir, 'index.html')

        @app.route('/api/status')
        def api_status():
            node.pub_heartbeat.publish(Bool(data=True))
            fs = node.follower_status
            with node.lock:
                cap = node.capture
                live = node.latest_rgb is not None
            return jsonify({
                'camera_ready': node.camera_ready,
                'odom_ready': bool(fs.odom_ready),
                'tf_ready': node.tf_ready,
                'capture_ready': cap is not None,
                'capture_id': cap.capture_id if cap else '',
                'image_width': int(cap.rgb_bgr.shape[1]) if cap is not None else 0,
                'image_height': int(cap.rgb_bgr.shape[0]) if cap is not None else 0,
                'live': live,
                'armed': bool(fs.armed),
                'allow_cmd_vel': bool(fs.allow_cmd_vel),
                'deadman_required': bool(getattr(fs, 'deadman_required', True)),
                'deadman_held': bool(getattr(fs, 'deadman_held', False)),
                'deadman_hint': getattr(fs, 'deadman_hint', '') or 'Spektrum D-switch in AUTONOMOUS',
                'state': fs.state or 'idle',
                'message': fs.message or '',
                'waypoint_index': int(fs.waypoint_index),
                'waypoint_count': int(fs.waypoint_count),
                'path_length_m': float(fs.path_length_m),
                'projection': node.last_projection_meta,
                'prototype_label': 'UNOBSTRUCTED-FLOOR PROTOTYPE — no obstacle avoidance',
            })

        @app.route('/api/live.jpg')
        def api_live():
            with node.lock:
                rgb = node.latest_rgb
            if rgb is None:
                return Response(status=503)
            bgr = node._ros_to_bgr(rgb)
            ok, buf = cv2.imencode('.jpg', bgr, [int(cv2.IMWRITE_JPEG_QUALITY), node.jpeg_quality])
            if not ok:
                return Response(status=500)
            return Response(buf.tobytes(), mimetype='image/jpeg')

        @app.route('/api/capture.jpg')
        def api_capture_jpg():
            with node.lock:
                cap = node.capture
            if cap is None:
                return Response(status=404)
            ok, buf = cv2.imencode(
                '.jpg', cap.rgb_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), node.jpeg_quality]
            )
            if not ok:
                return Response(status=500)
            return Response(buf.tobytes(), mimetype='image/jpeg')

        @app.route('/api/depth_valid.jpg')
        def api_depth_valid_jpg():
            """Valid-depth overlay. Prefer frozen capture; fall back to live pair."""
            source = request.args.get('source', 'auto')  # auto|capture|live
            try:
                with node.lock:
                    cap = node.capture
                    rgb_msg = node.latest_rgb
                    depth_msg = node.latest_depth
                if source in ('auto', 'capture') and cap is not None:
                    overlay = node._depth_validity_overlay(cap.rgb_bgr, cap.depth_m)
                elif source in ('auto', 'live') and rgb_msg is not None and depth_msg is not None:
                    bgr = node._ros_to_bgr(rgb_msg)
                    depth_m = node._ros_to_depth_m(depth_msg)
                    overlay = node._depth_validity_overlay(bgr, depth_m)
                else:
                    return Response(status=404)
                data = node._encode_jpeg(overlay)
                if data is None:
                    return Response(status=500)
                return Response(data, mimetype='image/jpeg')
            except Exception as exc:
                rospy.logwarn('depth_valid overlay failed: %s', exc)
                return Response(status=500)

        @app.route('/api/capture', methods=['POST'])
        def api_capture():
            resp = node.capture_frame()
            return jsonify({
                'success': resp.success,
                'message': resp.message,
                'capture_id': resp.capture_id,
                'image_width': resp.image_width,
                'image_height': resp.image_height,
                'optical_frame': resp.optical_frame,
                'path_frame': resp.path_frame,
                'capture_stamp': resp.capture_stamp.to_sec() if resp.success else 0.0,
            })

        @app.route('/api/project', methods=['POST'])
        def api_project():
            data = request.get_json(force=True, silent=True) or {}
            # Support display-space clicks with letterbox correction.
            display = data.get('display')
            pixels = data.get('pixels')  # [{u,v}, ...] in image space
            us, vs = [], []
            with node.lock:
                cap = node.capture
            if cap is None:
                return jsonify({'success': False, 'message': 'no capture'}), 400

            if pixels:
                for p in pixels:
                    us.append(int(p['u']))
                    vs.append(int(p['v']))
            elif display:
                dw = float(display.get('width', 0))
                dh = float(display.get('height', 0))
                for p in display.get('points', []):
                    mapped = display_to_image_pixel(
                        float(p['x']), float(p['y']), dw, dh,
                        cap.rgb_bgr.shape[1], cap.rgb_bgr.shape[0], letterbox=True,
                    )
                    if mapped is None:
                        return jsonify({
                            'success': False,
                            'message': 'click landed in letterbox margin or out of bounds',
                        }), 400
                    us.append(mapped[0])
                    vs.append(mapped[1])
            else:
                return jsonify({'success': False, 'message': 'provide pixels or display points'}), 400

            pp = PixelPath()
            pp.capture_id = data.get('capture_id', cap.capture_id)
            pp.image_width = cap.rgb_bgr.shape[1]
            pp.image_height = cap.rgb_bgr.shape[0]
            pp.u = us
            pp.v = vs
            pp.capture_stamp = cap.stamp
            resp = node.project_pixels(pp, publish_result=True)
            path_pts = [
                {'x': p.pose.position.x, 'y': p.pose.position.y, 'z': p.pose.position.z}
                for p in resp.path.poses
            ]
            return jsonify({
                'success': resp.success,
                'message': resp.message,
                'accepted_count': resp.accepted_count,
                'rejected_count': resp.rejected_count,
                'point_statuses': list(resp.point_statuses),
                'image_pixels': [{'u': u, 'v': v} for u, v in zip(us, vs)],
                'path': path_pts,
                'path_frame': node.path_frame,
                'length_m': path_length_m([(p['x'], p['y']) for p in path_pts]),
            })

        @app.route('/api/arm', methods=['POST'])
        def api_arm():
            data = request.get_json(force=True, silent=True) or {}
            armed = bool(data.get('armed', False))
            try:
                rospy.wait_for_service('/image_path_nav/set_armed', timeout=2.0)
                proxy = rospy.ServiceProxy('/image_path_nav/set_armed', SetArmed)
                resp = proxy(armed)
                return jsonify({
                    'success': resp.success,
                    'message': resp.message,
                    'armed': resp.armed,
                    'allow_cmd_vel': resp.allow_cmd_vel,
                })
            except Exception as exc:
                return jsonify({'success': False, 'message': str(exc)}), 500

        @app.route('/api/execute', methods=['POST'])
        def api_execute():
            """Start following the last projected path under UI heartbeat supervision."""
            node.pub_heartbeat.publish(Bool(data=True))
            if node.last_path is None or len(node.last_path.poses) == 0:
                return jsonify({'success': False, 'message': 'no projected path'}), 400
            # Ask the follower directly (status topic can lag the arm service).
            try:
                rospy.wait_for_service('/image_path_nav/follow_path', timeout=2.0)
                follow = rospy.ServiceProxy('/image_path_nav/follow_path', FollowPath)
                resp = follow(node.last_path, True)
                if not resp.success:
                    return jsonify({
                        'success': False,
                        'message': resp.message,
                        'state': resp.state,
                    }), 400
                # Also mark UI-supervised so heartbeat loss stops motion.
                node.pub_ui_execute.publish(node.last_path)
                mode = 'LIVE' if node.follower_status.allow_cmd_vel else 'DRY-RUN'
                return jsonify({
                    'success': True,
                    'message': f'{resp.message} (UI-supervised, {mode})',
                    'state': resp.state,
                    'waypoint_count': resp.waypoint_count,
                    'path_length_m': resp.path_length_m,
                    'allow_cmd_vel': bool(node.follower_status.allow_cmd_vel),
                })
            except Exception as exc:
                return jsonify({'success': False, 'message': str(exc)}), 500

        @app.route('/api/stop', methods=['POST'])
        def api_stop():
            node.pub_cancel.publish(Empty())
            try:
                rospy.wait_for_service('/image_path_nav/set_armed', timeout=2.0)
                proxy = rospy.ServiceProxy('/image_path_nav/set_armed', SetArmed)
                resp = proxy(False)
                return jsonify({
                    'success': True,
                    'message': resp.message or 'stop requested; disarmed',
                    'armed': resp.armed,
                })
            except Exception as exc:
                return jsonify({
                    'success': True,
                    'message': f'cancel published; disarm service error: {exc}',
                })

        @app.route('/api/heartbeat', methods=['POST'])
        def api_heartbeat():
            node.pub_heartbeat.publish(Bool(data=True))
            return jsonify({'ok': True})

        t = threading.Thread(
            target=lambda: app.run(
                host=node.web_host, port=node.web_port,
                threaded=True, use_reloader=False,
            ),
            daemon=True,
        )
        t.start()


def main():
    rospy.init_node('image_path_nav')
    ImagePathNavNode()
    rospy.spin()


if __name__ == '__main__':
    main()
