"""Diagnostics behavior for the Soulear camera client."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .facade import SoulearCamera

import struct

from .constants import MAGIC, PRESENCE_CONTROL1_PORT


class _DiagnosticsMixin:
    def decode_gyro_52219(self: SoulearCamera, src_port: int, dst_port: int, payload: bytes) -> bool:
        """Decode the related NE7/Soulear gyro packet on UDP 52219.

        NE3-Scope/client2.py uses:
            struct.unpack(">BxBxBxxxxxxxxxxxxxHxxxx", data)

        The resulting values are x, y, z and a big-endian angle in degrees.

        Parameters
        ----------
        src_port : int
            Packet source UDP port.
        dst_port : int
            Packet destination UDP port; retained for a uniform decoder interface.
        payload : bytes
            UDP payload to inspect.

        Returns
        -------
        bool
            True when the packet is recognized as a gyro packet.
        """
        if src_port != PRESENCE_CONTROL1_PORT:
            return False
        if len(payload) < 25:
            return False

        try:
            x, y, z, angle = struct.unpack(
                ">BxBxBxxxxxxxxxxxxxHxxxx",
                payload[:25],
            )
        except struct.error:
            return False

        self.gyro_packet_count += 1
        self.gyro_xyz = (x, y, z)

        if 0 <= angle <= 359:
            old = self.last_camera_angle
            # Keep the Soulear per-frame angle as primary. 52219 is only
            # diagnostic/compatibility data if this firmware ever sends it.
            if old is None:
                self.last_camera_angle = angle
            if self.debug and (old is None or abs(angle - old) >= 2 or self.gyro_packet_count <= 5):
                self.logger.debug(
                    f"GYRO52219 #{self.gyro_packet_count}: x={x} y={y} z={z} angle={angle}° raw={payload[:25].hex(' ')}"
                )
        else:
            self.logger.debug(
                f"GYRO52219 #{self.gyro_packet_count}: angle={angle} außerhalb 0..359, raw={payload[:25].hex(' ')}"
            )

        return True

    def decode_notify_packet(self: SoulearCamera, src_port: int, dst_port: int, payload: bytes) -> bool:
        """Decode the observed UDP/10007 command-9 notify envelope.

        Type 2 contains status/bit fields. It does not carry width/height and
        is not used as a resolution/open-video command by this client.

        Parameters
        ----------
        src_port : int
            Packet source UDP port; retained for a uniform decoder interface.
        dst_port : int
            Packet destination UDP port; retained for a uniform decoder interface.
        payload : bytes
            Candidate command notification payload.

        Returns
        -------
        bool
            True when the packet is a recognized notify envelope.
        """
        if len(payload) < 13:
            return False

        magic = struct.unpack_from("<I", payload, 0)[0]
        if magic != MAGIC:
            return False

        command = struct.unpack_from("<I", payload, 6)[0]
        if command != 0x00000009:
            return False

        declared_len = struct.unpack_from("<H", payload, 10)[0]
        body = payload[12 : 12 + declared_len]
        if not body:
            return True

        self.notify_packet_count += 1
        notify_type = body[0]

        if notify_type == 2 and len(body) >= 5:
            # These bit extractions are taken directly from the native parser.
            status_word = struct.unpack_from("<H", body, 1)[0]
            bit0 = status_word & 0x1
            bits1_7 = (status_word >> 1) & 0x7F
            bit8 = (status_word >> 8) & 0x1
            bits9_15 = (status_word >> 9) & 0x7F
            field3 = body[3]
            field4 = struct.unpack_from("<b", body, 4)[0]

            extra = ""
            if len(body) >= 17:
                word9 = struct.unpack_from("<H", body, 9)[0]
                byte11 = body[11]
                byte16 = body[16]
                extra = f" ext9=0x{word9:04x} ext11=0x{byte11:02x} ext16=0x{byte16:02x}"

            self.logger.debug(
                f"NOTIFY #{self.notify_packet_count} cmd=0x00000009 "
                f"type=2 len={declared_len} "
                f"status=0x{status_word:04x} "
                f"b0={bit0} bits1_7={bits1_7} b8={bit8} "
                f"bits9_15={bits9_15} field3={field3} field4={field4}"
                f"{extra}"
            )
        else:
            self.logger.debug(
                f"NOTIFY #{self.notify_packet_count} cmd=0x00000009 "
                f"type={notify_type} len={declared_len} "
                f"body={body[:32].hex(' ')}"
            )

        return True
