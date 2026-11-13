"""VideoTransport behavior for the Soulear camera client."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .facade import SoulearCamera

import struct
import time

from .constants import VIDEO_HEADER_SIZE


class _VideoTransportMixin:
    def _process_video_transport_packet(
        self: SoulearCamera, src_ip: str, dst_ip: str, src_port: int, dst_port: int, payload: bytes
    ) -> None:
        """Validate a Soulear video datagram and forward current-stream packets.

        Parameters
        ----------
        src_ip : str
            Packet source IPv4 address.
        dst_ip : str
            Packet destination IPv4 address.
        src_port : int
            Packet source UDP port.
        dst_port : int
            Packet destination UDP port.
        payload : bytes
            Video transport header and fragment bytes.
        """
        if len(payload) < VIDEO_HEADER_SIZE:
            if self.debug and self.raw_packet_count <= 5:
                self.logger.debug(f"VIDEO reject: short packet len={len(payload)} from {src_ip}:{src_port}")
            return

        # Validate a Soulear video header rather than relying only on the
        # malformed UDP destination. Native receiver accepts types 1,2,5,6.
        stream_type = payload[0]
        if stream_type not in (1, 2, 5, 6):
            if self.debug and self.raw_packet_count <= 5:
                self.logger.debug(
                    f"VIDEO reject: type=0x{stream_type:02x} from {src_ip}:{src_port} "
                    f"to {dst_ip}:{dst_port} len={len(payload)} hdr={payload[:16].hex(' ')}"
                )
            return

        width = struct.unpack_from("<H", payload, 12)[0]
        height = struct.unpack_from("<H", payload, 14)[0]
        if not (16 <= width <= 4096 and 16 <= height <= 4096):
            if self.debug and self.raw_packet_count <= 5:
                self.logger.debug(
                    f"VIDEO reject: dimensions={width}x{height} from {src_ip}:{src_port} "
                    f"to {dst_ip}:{dst_port} len={len(payload)} hdr={payload[:16].hex(' ')}"
                )
            return

        # Diagnostic capture can see stale streams from earlier sessions too.
        # Only frames addressed to THIS client's current primary/secondary
        # receive ports are allowed into the JPEG/reassembly state machine.
        current_ports = {self.video_port, self.video2_port}
        current_ports.discard(0)
        if dst_port not in current_ports:
            self.stale_video_count += 1
            now = time.monotonic()
            # One summary per second (or immediately when the stale port changes)
            # is enough; previous versions could print thousands of lines.
            if self.debug and (now - self._last_stale_log_time >= 1.0 or dst_port != self._last_stale_port):
                self.logger.debug(
                    f"STALE VIDEO ignored: port={dst_port} {width}x{height}; "
                    f"count={self.stale_video_count}, "
                    f"current ports={sorted(current_ports)}"
                )
                self._last_stale_log_time = now
                self._last_stale_port = dst_port
            return

        self.video_packet_count += 1
        self.last_video_packet_time = time.monotonic()
        self.first_video_event.set()

        if self.debug and (self.video_packet_count <= 10 or self.video_packet_count % self.video_log_every == 0):
            self.logger.debug(
                f"VIDEO #{self.video_packet_count:6d}: "
                f"{src_ip}:{src_port} -> {dst_ip}:{dst_port} "
                f"len={len(payload)} hdr={payload[:16].hex(' ')}"
            )

        self.process_video_packet(payload)
