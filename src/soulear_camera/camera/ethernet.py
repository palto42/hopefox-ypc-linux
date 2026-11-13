"""Ethernet behavior for the Soulear camera client."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .facade import SoulearCamera

import socket
import struct

from .constants import MAGIC, PRESENCE_VIDEO_PORT


class _EthernetMixin:
    def process_ethernet_frame(self: SoulearCamera, frame: bytes) -> None:
        """Parse an Ethernet frame and dispatch its camera UDP payload.

        Parameters
        ----------
        frame : bytes
            Ethernet frame containing an IPv4/UDP datagram.
        """
        packet = self._extract_udp_packet(frame)
        if packet is None:
            return

        src_ip, dst_ip, src_port, dst_port, payload = packet
        self.raw_packet_count += 1

        if self.decode_gyro_52219(src_port, dst_port, payload):
            return
        if self._handle_control_packet(src_ip, dst_ip, src_port, dst_port, payload):
            return
        if src_port == PRESENCE_VIDEO_PORT and dst_port == PRESENCE_VIDEO_PORT:
            self.process_legacy_44506_packet(payload)
            return
        self._process_video_transport_packet(src_ip, dst_ip, src_port, dst_port, payload)

    def _extract_udp_packet(self: SoulearCamera, frame: bytes) -> tuple[str, str, int, int, bytes] | None:
        """Validate Ethernet/IPv4/UDP headers and extract a camera datagram.

        Parameters
        ----------
        frame : bytes
            Candidate Ethernet frame.

        Returns
        -------
        tuple[str, str, int, int, bytes] | None
            Source IP, destination IP, source port, destination port, and payload;
            ``None`` when the frame is malformed or is not from this camera.
        """
        if len(frame) < 14 + 20 + 8:
            return None
        if frame[12:14] != b"\x08\x00":
            return None

        ip_offset = 14
        version_ihl = frame[ip_offset]
        if version_ihl >> 4 != 4:
            return None
        ihl = (version_ihl & 0x0F) * 4
        if ihl < 20 or len(frame) < ip_offset + ihl + 8:
            return None
        if frame[ip_offset + 9] != 17:
            return None

        src_ip = socket.inet_ntoa(frame[ip_offset + 12 : ip_offset + 16])
        dst_ip = socket.inet_ntoa(frame[ip_offset + 16 : ip_offset + 20])
        if src_ip != self.camera_ip:
            return None

        udp_offset = ip_offset + ihl
        src_port, dst_port, udp_len = struct.unpack_from("!HHH", frame, udp_offset)
        if udp_len < 8:
            return None

        payload_offset = udp_offset + 8
        payload = frame[payload_offset : payload_offset + udp_len - 8]
        if not payload:
            return None
        return src_ip, dst_ip, src_port, dst_port, payload

    def _handle_control_packet(
        self: SoulearCamera, src_ip: str, dst_ip: str, src_port: int, dst_port: int, payload: bytes
    ) -> bool:
        """Decode a notify or control datagram and consume recognized packets.

        Parameters
        ----------
        src_ip : str
            Source IPv4 address for diagnostics.
        dst_ip : str
            Destination IPv4 address for diagnostics.
        src_port : int
            Source UDP port passed to the notify decoder.
        dst_port : int
            Destination UDP port passed to the notify decoder.
        payload : bytes
            Datagram payload to inspect.

        Returns
        -------
        bool
            True when the payload is a control packet and should not be parsed as video.
        """
        if len(payload) >= 4 and payload[:4] == struct.pack("<I", MAGIC):
            if self.decode_notify_packet(src_port, dst_port, payload):
                return True

            self.control_packet_count += 1
            self.logger.debug(
                f"RAW CONTROL #{self.control_packet_count}: "
                f"{src_ip}:{src_port} -> {dst_ip}:{dst_port} "
                f"len={len(payload)} {payload[:24].hex(' ')}"
            )
            return True
        return False
