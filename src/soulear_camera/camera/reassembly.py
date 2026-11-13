"""Reassembly behavior for the Soulear camera client."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .facade import SoulearCamera

import socket
import struct
import time
from collections.abc import Sequence

from .constants import CAMERA_CONTROL_PORT, CMD_NACK, JPEG_SOI, MAGIC, MAX_JPEG_SIZE


class _ReassemblyMixin:
    def _collect_jpeg_fragment(
        self: SoulearCamera,
        data: bytes,
        packet_sequence: int,
        frame_id: int,
        last_fragment: bool,
        width: int,
        height: int,
        camera_angle: float | None,
    ) -> tuple[bytes, int | None, int, int, float | None] | None:
        """Append one fragment and return metadata when a frame is complete.

        Parameters
        ----------
        data : bytes
            JPEG data carried by the current video packet.
        packet_sequence : int
            Eight-bit sequence number from the video header.
        frame_id : int
            Camera frame identifier.
        last_fragment : bool
            Whether the packet marks the end of the frame.
        width : int
            Transport-header width in pixels.
        height : int
            Transport-header height in pixels.
        camera_angle : float | None
            Relative rotation angle captured for this frame.

        Returns
        -------
        tuple[bytes, int | None, int, int, float | None] | None
            Completed JPEG, frame ID, dimensions, and angle; ``None`` if more
            fragments are required or the frame is dropped.
        """
        with self.jpeg_lock:
            soi = data.find(JPEG_SOI)

            if soi >= 0:
                if self.collecting_jpeg and self.jpeg_buffer:
                    self.dropped_frames += 1
                    self.logger.debug("JPEG: vorherigen unvollständigen Frame verworfen")

                self.jpeg_buffer.clear()
                self.jpeg_buffer.extend(data[soi:])
                self.collecting_jpeg = True
                self.current_frame_id = frame_id
                self.expected_packet_sequence = (packet_sequence + 1) & 0xFF
                self.current_width = width
                self.current_height = height
                self.current_camera_angle = camera_angle
                if self.debug and (self.frame_count < 5 or self.frame_count % self.jpeg_log_every == 0):
                    self.logger.debug(f"JPEG START frame={frame_id} seq={packet_sequence} {width}x{height}")
            else:
                if not self.collecting_jpeg:
                    return

                if frame_id != self.current_frame_id:
                    self.dropped_frames += 1
                    self.logger.debug(f"JPEG frame jump {self.current_frame_id}->{frame_id}; drop")
                    self.reset_jpeg_locked()
                    self.update_stats()
                    return

                if self.expected_packet_sequence is not None and packet_sequence != self.expected_packet_sequence:
                    missing = self.sequence_gap(self.expected_packet_sequence, packet_sequence)
                    self.report_packet_loss(frame_id, self.expected_packet_sequence, packet_sequence, missing)
                    if missing:
                        self.send_nack(missing)
                    self.dropped_frames += 1
                    self.reset_jpeg_locked()
                    self.update_stats()
                    return

                self.jpeg_buffer.extend(data)
                self.expected_packet_sequence = (packet_sequence + 1) & 0xFF

            if len(self.jpeg_buffer) > MAX_JPEG_SIZE:
                self.dropped_frames += 1
                self.logger.warning("JPEG exceeded 5 MiB; frame dropped")
                self.reset_jpeg_locked()
                self.update_stats()
                return

            if not last_fragment:
                return None

            jpeg = bytes(self.jpeg_buffer)
            completed_frame_id = self.current_frame_id
            completed_width = self.current_width
            completed_height = self.current_height
            completed_camera_angle = getattr(self, "current_camera_angle", None)
            self.reset_jpeg_locked()
        return jpeg, completed_frame_id, completed_width, completed_height, completed_camera_angle

    def report_packet_loss(
        self: SoulearCamera, frame_id: int, expected: int, received: int, missing: list[int]
    ) -> None:
        """Rate-limit packet-loss warnings while preserving a periodic summary."""
        self._packet_loss_events += len(missing)
        now = time.monotonic()
        if now - self._last_packet_loss_log_time < 10.0:
            return
        self.logger.warning(
            f"Video packet loss: {self._packet_loss_events} missing packets; latest frame={frame_id}, expected={expected}, received={received}"
        )
        self._packet_loss_events = 0
        self._last_packet_loss_log_time = now

    def reset_jpeg_locked(self: SoulearCamera) -> None:
        """Clear JPEG assembly state while holding ``jpeg_lock``."""
        self.jpeg_buffer.clear()
        self.collecting_jpeg = False
        self.current_frame_id = None
        self.expected_packet_sequence = None
        self.current_width = 0
        self.current_height = 0

    @staticmethod
    def sequence_gap(expected: int, received: int) -> list[int]:
        """List missing eight-bit packet sequence values before ``received``.

        Parameters
        ----------
        expected : int
            Sequence number expected next.
        received : int
            Sequence number observed in the incoming packet.

        Returns
        -------
        list[int]
            Up to 64 sequence numbers missing between the two values.
        """
        result = []
        value = expected & 0xFF
        for _ in range(64):
            if value == received:
                break
            result.append(value)
            value = (value + 1) & 0xFF
        return result

    def send_nack(self: SoulearCamera, missing_sequences: Sequence[int]) -> None:
        """Send a NACK requesting retransmission of missing packet sequences.

        Parameters
        ----------
        missing_sequences : Sequence[int]
            Missing eight-bit sequence values to include in the request.
        """
        missing = bytes(missing_sequences[:116])
        seq = self.next_sequence()
        packet = struct.pack("<IHIH", MAGIC, seq, CMD_NACK, len(missing)) + missing
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.sendto(packet, (self.camera_ip, CAMERA_CONTROL_PORT))
            sock.close()
            self.nack_count += 1
            self.logger.debug(f"NACK #{self.nack_count}: protocol-seq={seq} missing={list(missing)}")
        except OSError as exc:
            self.logger.debug(f"NACK error: {exc}")
