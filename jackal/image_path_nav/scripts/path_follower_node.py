#!/usr/bin/env python3
"""Physical path follower. Independent of the image UI.

Default: disarmed and dry-run (publishes twists only on cmd_vel_dryrun).
Live motion requires BOTH allow_cmd_vel:=true (launch) AND armed:=true (service/UI).
Commands go to twist_mux external input (/cmd_vel) — never directly to the controller.
"""

from __future__ import annotations

import math
import os
import sys
import threading
import time

import rospy
from geometry_msgs.msg import Twist, PoseStamped
from nav_msgs.msg import Odometry, Path
from sensor_msgs.msg import Joy
from std_msgs.msg import Bool, Empty
from visualization_msgs.msg import Marker, MarkerArray

from image_path_nav.follower import (
    FollowerConfig,
    PurePursuitFollower,
    yaw_from_quaternion,
)
from image_path_nav.msg import ExecutionStatus
from image_path_nav.srv import FollowPath, FollowPathResponse
from image_path_nav.srv import SetArmed, SetArmedResponse


class PathFollowerNode:
    def __init__(self):
        self.allow_cmd_vel = rospy.get_param('~allow_cmd_vel', False)
        self.path_frame = rospy.get_param('~path_frame', 'odom')
        self.cmd_vel_topic = rospy.get_param('~cmd_vel_topic', '/cmd_vel')
        self.dryrun_topic = rospy.get_param('~cmd_vel_dryrun_topic', '/image_path_nav/cmd_vel_dryrun')
        self.odom_topic = rospy.get_param('~odom_topic', '/odometry/filtered')
        self.ui_heartbeat_timeout_s = float(rospy.get_param('~ui_heartbeat_timeout_s', 1.5))
        self.require_ui_heartbeat = bool(rospy.get_param('~require_ui_heartbeat', True))

        # Spektrum DXS: require autonomous mode (D-switch) via autonomy_arbiter/enabled.
        self.require_autonomy_enabled = bool(
            rospy.get_param('~require_autonomy_enabled', True)
        )
        self.autonomy_enabled_topic = rospy.get_param(
            '~autonomy_enabled_topic', '/autonomy_arbiter/enabled'
        )

        # Optional extra button deadman (e.g. PS4). Off for Spektrum by default.
        self.require_deadman = bool(rospy.get_param('~require_deadman', False))
        self.deadman_joy_topic = rospy.get_param('~deadman_joy_topic', '/joystick')
        self.deadman_button_index = int(rospy.get_param('~deadman_button_index', -1))
        self.deadman_timeout_s = float(rospy.get_param('~deadman_timeout_s', 0.35))
        self.deadman_hint = rospy.get_param(
            '~deadman_hint',
            'Spektrum D-switch in AUTONOMOUS',
        )

        cfg = FollowerConfig(
            linear_speed_mps=float(rospy.get_param('~linear_speed_mps', 0.1)),
            max_angular_speed_rps=float(rospy.get_param('~max_angular_speed_rps', 0.4)),
            goal_tolerance_m=float(rospy.get_param('~goal_tolerance_m', 0.15)),
            lookahead_m=float(rospy.get_param('~lookahead_m', 0.35)),
            slowdown_distance_m=float(rospy.get_param('~slowdown_distance_m', 0.4)),
            heading_align_rad=float(rospy.get_param('~heading_align_rad', 0.6)),
            stale_odom_timeout_s=float(rospy.get_param('~stale_odom_timeout_s', 0.5)),
            no_progress_timeout_s=float(rospy.get_param('~no_progress_timeout_s', 8.0)),
            control_rate_hz=float(rospy.get_param('~control_rate_hz', 20.0)),
        )
        self.follower = PurePursuitFollower(cfg)
        self.lock = threading.RLock()
        self.armed = False
        self.last_odom = None
        self.last_odom_time = 0.0
        self.last_ui_heartbeat = 0.0
        self.ui_controlled = False  # set true when execute came from UI path
        self.deadman_pressed = False
        self.last_deadman_time = 0.0
        self.autonomy_enabled = False
        self.last_autonomy_time = 0.0
        self.was_publishing_live = False
        self.message = 'idle (disarmed, dry-run)' if not self.allow_cmd_vel else 'idle (disarmed)'

        self.pub_cmd = rospy.Publisher(self.cmd_vel_topic, Twist, queue_size=1)
        self.pub_dry = rospy.Publisher(self.dryrun_topic, Twist, queue_size=1)
        self.pub_status = rospy.Publisher('~status', ExecutionStatus, queue_size=1, latch=True)
        self.pub_active_path = rospy.Publisher('~active_path', Path, queue_size=1, latch=True)
        self.pub_markers = rospy.Publisher('~markers', MarkerArray, queue_size=1, latch=True)

        rospy.Subscriber(self.odom_topic, Odometry, self._odom_cb, queue_size=1)
        rospy.Subscriber('~path', Path, self._path_cb, queue_size=1)
        rospy.Subscriber('/image_path_nav/path', Path, self._path_cb, queue_size=1)
        rospy.Subscriber('~cancel', Empty, self._cancel_cb, queue_size=1)
        rospy.Subscriber('~stop', Empty, self._cancel_cb, queue_size=1)
        rospy.Subscriber('~ui_heartbeat', Bool, self._heartbeat_cb, queue_size=1)
        rospy.Subscriber('~arm', Bool, self._arm_topic_cb, queue_size=1)
        rospy.Subscriber('~ui_execute', Path, self._ui_execute_cb, queue_size=1)
        rospy.Subscriber(
            self.autonomy_enabled_topic, Bool, self._autonomy_cb, queue_size=1
        )
        if self.require_deadman and self.deadman_button_index >= 0:
            rospy.Subscriber(self.deadman_joy_topic, Joy, self._joy_cb, queue_size=1)

        rospy.Service('~follow_path', FollowPath, self._follow_srv)
        rospy.Service('~set_armed', SetArmed, self._arm_srv)
        rospy.Service('/image_path_nav/follow_path', FollowPath, self._follow_srv)
        rospy.Service('/image_path_nav/set_armed', SetArmed, self._arm_srv)

        rate_hz = cfg.control_rate_hz
        self.timer = rospy.Timer(rospy.Duration(1.0 / rate_hz), self._control_tick)
        self.status_timer = rospy.Timer(rospy.Duration(0.2), self._status_tick)

        rospy.logwarn(
            'image_path_nav follower started: allow_cmd_vel=%s cmd_vel=%s '
            'require_autonomy=%s require_button_deadman=%s (%s) '
            '(UNOBSTRUCTED-FLOOR PROTOTYPE — no obstacle avoidance)',
            self.allow_cmd_vel, self.cmd_vel_topic,
            self.require_autonomy_enabled, self.require_deadman, self.deadman_hint,
        )
        if not self.allow_cmd_vel:
            rospy.logwarn('DRY-RUN mode: twists published only on %s', self.dryrun_topic)
        if self.require_autonomy_enabled:
            rospy.logwarn(
                'LIVE motion requires Spektrum AUTONOMOUS (topic %s true)',
                self.autonomy_enabled_topic,
            )

    def _odom_cb(self, msg: Odometry):
        with self.lock:
            self.last_odom = msg
            self.last_odom_time = time.time()

    def _autonomy_cb(self, msg: Bool):
        with self.lock:
            self.autonomy_enabled = bool(msg.data)
            self.last_autonomy_time = time.time()

    def _joy_cb(self, msg: Joy):
        pressed = (
            self.deadman_button_index >= 0
            and len(msg.buttons) > self.deadman_button_index
            and bool(msg.buttons[self.deadman_button_index])
        )
        with self.lock:
            self.deadman_pressed = pressed
            if pressed:
                self.last_deadman_time = time.time()

    def _button_deadman_ok(self) -> bool:
        if not self.require_deadman or self.deadman_button_index < 0:
            return True
        if self.deadman_pressed and (time.time() - self.last_deadman_time) <= self.deadman_timeout_s:
            return True
        if self.last_deadman_time > 0 and (time.time() - self.last_deadman_time) <= self.deadman_timeout_s:
            return True
        return False

    def _deadman_ok(self) -> bool:
        """True when Spektrum autonomy (and optional button) allow live motion."""
        if self.require_autonomy_enabled and not self.autonomy_enabled:
            return False
        return self._button_deadman_ok()

    def _heartbeat_cb(self, msg: Bool):
        if msg.data:
            self.last_ui_heartbeat = time.time()

    def _arm_topic_cb(self, msg: Bool):
        self._set_armed(bool(msg.data), source='topic')

    def _ui_execute_cb(self, msg: Path):
        ok, message = self.mark_ui_execute(msg)
        if not ok:
            rospy.logwarn('ui_execute rejected: %s', message)

    def _cancel_cb(self, _msg):
        with self.lock:
            self.follower.cancel()
            self.armed = False
            self.ui_controlled = False
            self.message = 'stopped and disarmed'
        self._publish_zero(force_live=True)
        rospy.logwarn('Path follower cancelled')

    def _path_cb(self, msg: Path):
        self._load_path(msg, execute=self.armed, ui_controlled=False)

    def _follow_srv(self, req):
        ok, msg, count, length = self._load_path(
            req.path, execute=bool(req.execute), ui_controlled=False
        )
        state = self._state_string()
        return FollowPathResponse(
            success=ok, message=msg, state=state,
            waypoint_count=count, path_length_m=length,
        )

    def _arm_srv(self, req):
        ok, message = self._set_armed(bool(req.armed), source='service')
        return SetArmedResponse(
            success=ok, message=message, armed=self.armed, allow_cmd_vel=self.allow_cmd_vel
        )

    def _set_armed(self, armed: bool, source: str = ''):
        with self.lock:
            self.armed = armed
            if not armed:
                self.follower.cancel()
                self.ui_controlled = False
                self.message = 'disarmed'
            elif not self.allow_cmd_vel:
                # Soft-arm: follower may run, but twists stay on dryrun topic only.
                self.message = 'armed (DRY-RUN — twists on cmd_vel_dryrun only, no live motion)'
            else:
                self.message = 'armed (LIVE cmd_vel enabled — waiting for execute)'
            rospy.logwarn('armed=%s allow_cmd_vel=%s (%s)', self.armed, self.allow_cmd_vel, source)
        if not armed:
            self._publish_zero(force_live=True)
        return True, self.message

    def _load_path(self, path_msg: Path, execute: bool, ui_controlled: bool):
        with self.lock:
            if path_msg is None or len(path_msg.poses) == 0:
                self.follower.cancel()
                self.ui_controlled = False
                self.message = 'path cleared'
                self._publish_active_path([])
                return True, self.message, 0, 0.0

            frame = path_msg.header.frame_id or self.path_frame
            if frame and frame != self.path_frame:
                self.message = f'path frame {frame} != {self.path_frame}'
                rospy.logerr(self.message)
                return False, self.message, 0, 0.0

            pts = []
            for ps in path_msg.poses:
                pts.append((ps.pose.position.x, ps.pose.position.y))

            # Replace active path entirely (no mixing).
            if execute:
                if not self.armed:
                    self.message = 'path accepted for preview; not executing (disarmed)'
                    self.follower.set_path([])  # do not start
                    # Still store as preview via active_path publish below
                    self._publish_active_path(pts)
                    return True, self.message, len(pts), self.follower.state.path_length_m
                self.follower.set_path(pts)
                self.ui_controlled = ui_controlled
                self.message = f'executing {len(pts)} waypoints'
            else:
                self.follower.set_path([])
                self.message = f'preview {len(pts)} waypoints (not executing)'
            self._publish_active_path(pts)
            length = 0.0
            for i in range(1, len(pts)):
                length += math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])
            return True, self.message, len(pts), length

    def mark_ui_execute(self, path_msg: Path):
        """Called by web node to execute a path under UI heartbeat supervision."""
        with self.lock:
            if not self.armed:
                return False, 'disarmed'
            pts = [(p.pose.position.x, p.pose.position.y) for p in path_msg.poses]
            if not pts:
                return False, 'empty path'
            self.follower.set_path(pts)
            self.ui_controlled = True
            self.last_ui_heartbeat = time.time()
            mode = 'LIVE' if self.allow_cmd_vel else 'DRY-RUN'
            self.message = f'UI executing {len(pts)} waypoints ({mode})'
            self._publish_active_path(pts)
            return True, self.message

    def _publish_active_path(self, pts):
        path = Path()
        path.header.stamp = rospy.Time.now()
        path.header.frame_id = self.path_frame
        for x, y in pts:
            ps = PoseStamped()
            ps.header = path.header
            ps.pose.position.x = x
            ps.pose.position.y = y
            ps.pose.orientation.w = 1.0
            path.poses.append(ps)
        self.pub_active_path.publish(path)

        ma = MarkerArray()
        clear = Marker()
        clear.action = Marker.DELETEALL
        ma.markers.append(clear)
        if pts:
            line = Marker()
            line.header = path.header
            line.ns = 'active_path'
            line.id = 1
            line.type = Marker.LINE_STRIP
            line.action = Marker.ADD
            line.scale.x = 0.04
            line.color.r = 0.1
            line.color.g = 0.8
            line.color.b = 0.2
            line.color.a = 1.0
            line.pose.orientation.w = 1.0
            for x, y in pts:
                from geometry_msgs.msg import Point
                pt = Point(x=x, y=y, z=0.05)
                line.points.append(pt)
            ma.markers.append(line)
            for i, (x, y) in enumerate(pts):
                m = Marker()
                m.header = path.header
                m.ns = 'waypoints'
                m.id = 100 + i
                m.type = Marker.SPHERE
                m.action = Marker.ADD
                m.scale.x = m.scale.y = m.scale.z = 0.08
                m.color.r = 1.0
                m.color.g = 0.5
                m.color.b = 0.0
                m.color.a = 1.0
                m.pose.position.x = x
                m.pose.position.y = y
                m.pose.position.z = 0.05
                m.pose.orientation.w = 1.0
                ma.markers.append(m)
        self.pub_markers.publish(ma)

    def _publish_zero(self, force_live: bool = False):
        tw = Twist()
        self.pub_dry.publish(tw)
        if self.allow_cmd_vel and (self.was_publishing_live or force_live):
            # Zero the mux input if we may have been commanding; do not spam forever.
            self.pub_cmd.publish(tw)
            self.was_publishing_live = False

    def _state_string(self) -> str:
        with self.lock:
            if self.follower.state.failed:
                return 'error'
            if self.follower.state.completed:
                return 'completed'
            if self.follower.state.active and self.armed:
                if self.allow_cmd_vel and not self._deadman_ok():
                    return 'waiting_deadman'
                return 'following'
            if self.armed:
                return 'armed'
            if self.follower.state.cancelled and 'stop' in self.message:
                return 'stopping'
            return 'idle'

    def _control_tick(self, _evt):
        with self.lock:
            # UI heartbeat loss while UI-controlled -> stop + disarm
            if (
                self.ui_controlled
                and self.follower.state.active
                and self.require_ui_heartbeat
            ):
                age = time.time() - self.last_ui_heartbeat
                if self.last_ui_heartbeat <= 0.0 or age > self.ui_heartbeat_timeout_s:
                    self.follower.cancel()
                    self.armed = False
                    self.ui_controlled = False
                    self.message = 'UI heartbeat lost; stopped and disarmed'
                    rospy.logerr(self.message)
                    self._publish_zero(force_live=True)
                    return

            if not self.follower.state.active:
                # Keep publishing zeros on dryrun; clear live if we were commanding.
                tw = Twist()
                self.pub_dry.publish(tw)
                if self.was_publishing_live:
                    self._publish_zero(force_live=True)
                return

            if self.last_odom is None:
                self.follower.state.active = False
                self.follower.state.failed = True
                self.message = 'no odometry'
                self._publish_zero(force_live=True)
                return

            odom = self.last_odom
            odom_age = time.time() - self.last_odom_time
            p = odom.pose.pose.position
            q = odom.pose.pose.orientation
            yaw = yaw_from_quaternion(q.x, q.y, q.z, q.w)
            cmd = self.follower.step(p.x, p.y, yaw, odom_age_s=odom_age)

            tw = Twist()
            tw.linear.x = cmd.linear_x
            tw.angular.z = cmd.angular_z
            self.pub_dry.publish(tw)

            deadman_ok = self._deadman_ok()
            if self.armed and self.allow_cmd_vel and deadman_ok and self.follower.state.active:
                self.pub_cmd.publish(tw)
                self.was_publishing_live = True
                self.message = self.follower.state.message
            else:
                if self.was_publishing_live:
                    self.pub_cmd.publish(Twist())
                    self.was_publishing_live = False
                if self.follower.state.active and self.armed and self.allow_cmd_vel and not deadman_ok:
                    if self.require_autonomy_enabled and not self.autonomy_enabled:
                        self.message = (
                            'waiting for Spektrum AUTONOMOUS '
                            '(D-switch) — flip back to stop'
                        )
                    else:
                        self.message = f'waiting for deadman — {self.deadman_hint}'
                else:
                    self.message = self.follower.state.message

            if self.follower.state.completed or self.follower.state.failed:
                self.armed = False
                self.ui_controlled = False
                if self.was_publishing_live:
                    self._publish_zero(force_live=True)

    def _status_tick(self, _evt):
        msg = ExecutionStatus()
        with self.lock:
            msg.state = self._state_string()
            msg.armed = self.armed
            msg.allow_cmd_vel = self.allow_cmd_vel
            msg.deadman_required = bool(
                self.require_autonomy_enabled or self.require_deadman
            )
            msg.deadman_held = self._deadman_ok()
            if self.require_autonomy_enabled:
                mode = 'AUTONOMOUS' if self.autonomy_enabled else 'not autonomous'
                msg.deadman_hint = f'Spektrum D-switch AUTONOMOUS ({mode})'
            else:
                msg.deadman_hint = self.deadman_hint
            msg.odom_ready = (
                self.last_odom is not None
                and (time.time() - self.last_odom_time) < 1.0
            )
            msg.message = self.message
            msg.path_length_m = self.follower.state.path_length_m
            msg.waypoint_index = self.follower.state.waypoint_index
            msg.waypoint_count = self.follower.state.waypoint_count
            msg.dist_to_waypoint_m = self.follower.state.dist_to_waypoint_m
            if self.last_ui_heartbeat > 0:
                msg.ui_heartbeat_age_s = time.time() - self.last_ui_heartbeat
            else:
                msg.ui_heartbeat_age_s = -1.0
        # camera/tf/capture filled by web node on its own status; leave false here
        self.pub_status.publish(msg)


def main():
    rospy.init_node('image_path_follower')
    PathFollowerNode()
    rospy.spin()


if __name__ == '__main__':
    main()
