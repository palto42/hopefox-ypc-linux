"""Video behavior for the Soulear camera client."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .facade import SoulearCamera

import struct

from .constants import JPEG_EOI, JPEG_SOI, VIDEO_HEADER_SIZE


class _VideoMixin:
    def process_video_packet(self: SoulearCamera, payload: bytes) -> None:
        """Decode motion metadata, assemble JPEG fragments, and publish frames.

        Parameters
        ----------
        payload : bytes
            Soulear video datagram containing a 16-byte header and fragment data.
        """
        header = payload[:VIDEO_HEADER_SIZE]
        data = payload[VIDEO_HEADER_SIZE:]
        packet_sequence = header[1]
        frame_id = header[2]
        flags = header[3]
        last_fragment = bool(flags & 0x01)
        camera_angle = self._update_motion_metadata(header)
        width = struct.unpack_from("<H", header, 12)[0]
        height = struct.unpack_from("<H", header, 14)[0]

        completed_frame = self._collect_jpeg_fragment(
            data,
            packet_sequence,
            frame_id,
            last_fragment,
            width,
            height,
            camera_angle,
        )
        if completed_frame is None:
            return

        jpeg, completed_frame_id, completed_width, completed_height, completed_camera_angle = completed_frame
        if not jpeg.startswith(JPEG_SOI):
            self.dropped_frames += 1
            self.update_stats()
            return

        eoi = jpeg.rfind(JPEG_EOI)
        if eoi < 0:
            self.dropped_frames += 1
            self.logger.warning(f"Frame {completed_frame_id}: LAST without JPEG EOI")
            self.update_stats()
            return

        self.emit_jpeg(
            jpeg[: eoi + 2],
            completed_frame_id,
            completed_width,
            completed_height,
            camera_angle=completed_camera_angle,
        )
