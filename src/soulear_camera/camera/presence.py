"""Presence behavior for the Soulear camera client."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .facade import SoulearCamera

import time

from .constants import (
    KEEPALIVE_CONTROL1,
    KEEPALIVE_CONTROL2,
    KEEPALIVE_VIDEO,
    PRESENCE_CONTROL1_PORT,
    PRESENCE_CONTROL2_DEST,
    PRESENCE_VIDEO_PORT,
)


class _PresenceMixin:
    def send_ne7_probe(self: SoulearCamera) -> None:
        """One-shot discovery/legacy-stream probe based on haxko/NE3-Scope.

        Related NE7 firmware sends 66 39 01 01 to broadcast ports 58090 and
        46526, then 86 06 01 to 52219 and 20 36 to 44506.
        Our native Soulear library uses a close variant (66 30 01 01), so this
        is sent only once as a compatibility probe.
        """
        if not self.keepalives_enabled:
            return
        control2_socket = self.presence_control2_socket
        control1_socket = self.presence_control1_socket
        video_socket = self.presence_video_socket
        if control2_socket is None or control1_socket is None or video_socket is None:
            return
        try:
            probe = bytes([0x66, 0x39, 0x01, 0x01])
            control2_socket.sendto(probe, (self.broadcast_ip, 58090))
            control2_socket.sendto(probe, (self.broadcast_ip, 46526))
            control1_socket.sendto(
                KEEPALIVE_CONTROL1,
                (self.camera_ip, PRESENCE_CONTROL1_PORT),
            )
            video_socket.sendto(
                KEEPALIVE_VIDEO,
                (self.camera_ip, PRESENCE_VIDEO_PORT),
            )
            self.logger.debug("NE7 probe: 66 39 01 01 -> :58090/:46526; 86 06 01 -> :52219; 20 36 -> :44506")
        except OSError as exc:
            self.logger.warning(f"NE7 probe failed: {exc}")

    def keepalive_loop(self: SoulearCamera) -> None:
        """Send periodic camera-presence datagrams while the client is running."""
        control2_socket = self.presence_control2_socket
        control1_socket = self.presence_control1_socket
        video_socket = self.presence_video_socket
        if control2_socket is None or control1_socket is None or video_socket is None:
            return
        start = time.monotonic()
        next_control = start + 4.0
        next_video = start + 6.0

        while self.running:
            now = time.monotonic()
            if now >= next_control:
                try:
                    control2_socket.sendto(
                        KEEPALIVE_CONTROL2,
                        (self.broadcast_ip, PRESENCE_CONTROL2_DEST),
                    )
                    control1_socket.sendto(
                        KEEPALIVE_CONTROL1,
                        (self.broadcast_ip, PRESENCE_CONTROL1_PORT),
                    )
                    self.logger.debug("PRESENCE: 66 30 01 01 -> :46526; 86 06 01 -> :52219")
                except OSError as exc:
                    self.logger.warning(f"PRESENCE control send failed: {exc}")
                    return
                next_control = now + 1.0

            if now >= next_video:
                try:
                    video_socket.sendto(
                        KEEPALIVE_VIDEO,
                        (self.broadcast_ip, PRESENCE_VIDEO_PORT),
                    )
                    self.logger.debug("PRESENCE: 20 36 -> :44506")
                except OSError as exc:
                    self.logger.warning(f"PRESENCE video send failed: {exc}")
                    return
                next_video = now + 3.0

            time.sleep(0.02)

    def online_monitor_loop(self: SoulearCamera) -> None:
        """Check camera availability periodically until the client stops."""
        # Do not collide with initial setup.
        time.sleep(2.0)
        while self.running:
            try:
                ok = self.camera_check_online(quiet=True)
                self.logger.debug(f"ONLINE monitor: {'ok' if ok else 'no response'}")
            except Exception as exc:
                self.logger.warning(f"ONLINE monitor failed: {exc}")
            for _ in range(30):
                if not self.running:
                    return
                time.sleep(0.1)
