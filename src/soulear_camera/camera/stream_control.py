"""StreamControl behavior for the Soulear camera client."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .facade import SoulearCamera

import struct
import time

from .constants import (
    CAMERA_CONTROL_PORT,
    CAMERA_VIDEO_COMMAND_PORT,
    CMD_OPEN_VIDEO,
    CMD_OPEN_VIDEO_FORCE,
    CMD_SET_TIME,
    MAGIC,
)


class _StreamControlMixin:
    def set_camera_time(self: SoulearCamera) -> bool:
        """Set the camera clock to the local system time.

        Returns
        -------
        bool
            Whether the camera acknowledged the time command.

        """
        if not self.ensure_camera_connected():
            return False

        now = time.localtime()
        seq = self.next_sequence()
        packet = struct.pack(
            "<IHIHHBBBBB",
            MAGIC,
            seq,
            CMD_SET_TIME,
            7,
            now.tm_year,
            now.tm_mon,
            now.tm_mday,
            now.tm_hour,
            now.tm_min,
            now.tm_sec,
        )
        response = self.sync_transaction(
            packet,
            CAMERA_CONTROL_PORT,
            seq,
            timeout=0.5,
            retries=2,
            label="SET_TIME",
        )
        if response is not None:
            self.logger.info("SET_TIME: bestätigt")
            return True
        self.logger.warning("SET_TIME: no acknowledgement")
        return False

    def open_video_normal(self: SoulearCamera, reason: str = "initial") -> bool:
        """Request the normal camera video stream.

        Parameters
        ----------
        reason : str, optional
            Diagnostic label describing why the stream is being opened.

        Returns
        -------
        bool
            Whether the camera acknowledged the open request.

        """
        seq = self.next_sequence()

        # proOpenVideo uses subtype 2 with the primary dynamic receiver.
        # When status capability bit 5 is set, it uses subtype 4 and also
        # includes the second dynamic receiver. For 192.168.1.255 the native
        # function sends the 16-byte form.
        subtype = 4 if self.dual_stream else 2
        secondary = self.video2_port if self.dual_stream else 0

        packet = struct.pack(
            "<IHIHHH",
            MAGIC,
            seq,
            CMD_OPEN_VIDEO,
            subtype,
            self.video_port,
            secondary,
        )

        self.logger.debug(
            f"OPEN_VIDEO(normal/{reason}): seq={seq} subtype={subtype} primary={self.video_port} secondary={secondary}"
        )
        self.logger.debug(f"  TX: {packet.hex(' ')}")

        response = self.sync_transaction(
            packet,
            CAMERA_VIDEO_COMMAND_PORT,
            seq,
            timeout=0.12,
            retries=5,
            label="OPEN_VIDEO",
        )
        self.last_open_time = time.monotonic()
        self.normal_open_acked = response is not None

        if response is not None:
            self.logger.info(f"OPEN_VIDEO(normal): ACK ({len(response)} Bytes)")
            self.signals.status.emit("Normaler Videostream geöffnet")
            return True

        self.logger.warning("OPEN_VIDEO(normal): no acknowledgement")
        return False

    def open_video_force(self: SoulearCamera, reason: str = "fallback") -> bool:
        """Request the experimental force-open camera video path.

        Parameters
        ----------
        reason : str, optional
            Diagnostic label describing why the fallback is being tried.

        Returns
        -------
        bool
            Whether the camera acknowledged the open request.

        """
        seq = self.next_sequence()
        packet = struct.pack(
            "<IHIHH",
            MAGIC,
            seq,
            CMD_OPEN_VIDEO_FORCE,
            0,
            self.video_port,
        )
        self.logger.debug(f"OPEN_VIDEO_FORCE({reason}): seq={seq} video_port={self.video_port}")
        self.logger.debug(f"  TX: {packet.hex(' ')}")
        response = self.sync_transaction(
            packet,
            CAMERA_VIDEO_COMMAND_PORT,
            seq,
            timeout=0.12,
            retries=5,
            label="OPEN_VIDEO_FORCE",
        )
        self.force_open_used = True
        self.last_open_time = time.monotonic()
        if response is not None:
            self.logger.info(f"OPEN_VIDEO_FORCE: ACK ({len(response)} Bytes)")
            self.signals.status.emit("Force-Videopfad geöffnet")
            return True
        self.logger.warning("OPEN_VIDEO_FORCE: no acknowledgement")
        return False
