"""Publishing behavior for the Soulear camera client."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .facade import SoulearCamera

import time


class _PublishingMixin:
    def emit_jpeg(
        self: SoulearCamera,
        jpeg: bytes,
        frame_id: int | str | None,
        width: int,
        height: int,
        camera_angle: float | None = None,
    ) -> None:
        """Publish one complete JPEG.

        width/height are from the Soulear 16-byte transport header. The client
        separately parses the JPEG SOF marker because this camera advertises
        640x480 in the transport header while the actual JPEG is 480x480.

        Parameters
        ----------
        jpeg : bytes
            Complete encoded JPEG frame.
        frame_id : int | str | None
            Camera frame identifier or legacy stream identifier.
        width : int
            Width advertised by the transport header.
        height : int
            Height advertised by the transport header.
        camera_angle : float | None, optional
            Relative angle associated with this frame.
        """
        self.frame_count += 1
        self.last_jpeg = jpeg
        self.last_jpeg_angle = camera_angle

        transport_resolution = (int(width), int(height))
        self.last_transport_resolution = transport_resolution
        jpeg_width, jpeg_height = self.jpeg_dimensions(jpeg)
        jpeg_resolution = (jpeg_width, jpeg_height) if jpeg_width > 0 and jpeg_height > 0 else transport_resolution

        self.last_jpeg_resolution = jpeg_resolution

        # Real receive rate over approximately one-second windows.
        now = time.monotonic()
        if self.fps_window_start is None:
            self.fps_window_start = now
            self.fps_window_frames = 1
        else:
            self.fps_window_frames += 1
            elapsed = now - self.fps_window_start
            if elapsed >= 1.0:
                self.fps_average = self.fps_window_frames / elapsed
                self.fps_window_frames = 0
                self.fps_window_start = now

        if jpeg_resolution != transport_resolution:
            resolution_text = (
                f"JPEG={jpeg_resolution[0]}x{jpeg_resolution[1]} "
                f"(Transport={transport_resolution[0]}x{transport_resolution[1]})"
            )
        else:
            resolution_text = f"{jpeg_resolution[0]}x{jpeg_resolution[1]}"

        resolution_changed = jpeg_resolution != self._last_logged_jpeg_resolution
        if resolution_changed:
            self.logger.info(f"Video stream active: {resolution_text}")
            self._last_logged_jpeg_resolution = jpeg_resolution
        elif self.frame_count <= 10 or self.frame_count % self.jpeg_log_every == 0:
            self.logger.debug(
                f"JPEG #{self.frame_count}: frame={frame_id} "
                f"{len(jpeg):,} Bytes {resolution_text} "
                f"RX_FPS={self.fps_average:.1f}"
            )

        # Recording gets the dimensions of the JPEG that is actually written.
        self._record_jpeg(
            jpeg,
            jpeg_resolution[0],
            jpeg_resolution[1],
            camera_angle,
        )

        #  enqueue complete JPEGs for the fixed-rate preview player.
        # deque(maxlen=12) guarantees bounded latency even if the camera bursts.
        with self.gui_frame_lock:
            self.gui_frame_queue.append((jpeg, camera_angle))

        if self.camera_recording:
            self.signals.status.emit(
                f"Recording - JPEG {jpeg_resolution[0]}x{jpeg_resolution[1]} "
                f"(Transport {transport_resolution[0]}x{transport_resolution[1]}) "
                f"- Frame {self.record_frame_count}"
            )
        else:
            if jpeg_resolution != transport_resolution:
                self.signals.status.emit(
                    f"Live - JPEG {jpeg_resolution[0]}x{jpeg_resolution[1]} "
                    f"(Transport {transport_resolution[0]}x{transport_resolution[1]})"
                )
            else:
                self.signals.status.emit(f"Live - {jpeg_resolution[0]}x{jpeg_resolution[1]}")

        self.update_stats()

    def update_stats(self: SoulearCamera) -> None:
        """Publish current frame, packet, orientation, LED, and recording statistics."""
        jpeg_res = (
            f"{self.last_jpeg_resolution[0]}x{self.last_jpeg_resolution[1]}" if self.last_jpeg_resolution else "?"
        )
        transport_res = (
            f"{self.last_transport_resolution[0]}x{self.last_transport_resolution[1]}"
            if self.last_transport_resolution
            else "?"
        )
        self.signals.stats.emit(
            f"Frames: {self.frame_count}    "
            f"Video-Pakete: {self.video_packet_count}    "
            f"Verworfen: {self.dropped_frames}    "
            f"NACKs: {self.nack_count}    "
            f"RX-FPS: {self.fps_average:.1f}    "
            f"JPEG: {jpeg_res}    "
            f"Transport: {transport_res}    "
            f"Preview: 20 Hz buffered    "
            f"Angle: {self.last_camera_angle:.1f}° "
            f"{'AUTO' if self.auto_rotate else 'off'}    "
            f"LED: {self.led_status if self.led_status is not None else '?'}    "
            f"REC: {'ON' if self.camera_recording else 'off'}"
        )
