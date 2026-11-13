"""Transaction behavior for the Soulear camera client."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .facade import SoulearCamera

import socket
import struct
from contextlib import suppress

from .constants import CAMERA_CONTROL_PORT, CMD_CHECK_ONLINE, MAGIC


class _TransactionMixin:
    def next_sequence(self: SoulearCamera) -> int:
        """Return the next nonzero 16-bit protocol sequence number.

        Returns
        -------
        int
            Sequence number reserved under the sequence lock.
        """
        with self.seq_lock:
            value = self.protocol_sequence
            self.protocol_sequence = (self.protocol_sequence + 1) & 0xFFFF
            if self.protocol_sequence == 0:
                self.protocol_sequence = 1
            return value

    def sync_transaction(
        self: SoulearCamera,
        packet: bytes,
        destination_port: int,
        expected_seq: int,
        timeout: float = 0.15,
        retries: int = 4,
        label: str = "COMMAND",
    ) -> bytes | None:
        """Send a command and wait for a matching protocol response.

        Parameters
        ----------
        packet : bytes
            Serialized command datagram.
        destination_port : int
            Camera UDP port receiving the command.
        expected_seq : int
            Sequence number required in the response.
        timeout : float, optional
            Timeout in seconds for each receive attempt.
        retries : int, optional
            Maximum number of send attempts.
        label : str, optional
            Diagnostic name for the transaction.

        Returns
        -------
        bytes | None
            Matching response, or ``None`` after all attempts time out.

        """
        # cameraRecordStatusSync uses a temporary UDP socket for the sync
        # exchange. Reproduce that behavior rather than retaining a command
        # socket between calls.
        with self.command_lock:
            sock = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.bind((self.local_ip, 0))
                sock.settimeout(timeout)
                local_port = sock.getsockname()[1]

                self.logger.debug(
                    f"{label}: local UDP :{local_port} -> {self.camera_ip}:{destination_port}  {packet.hex(' ')}"
                )

                for attempt in range(1, retries + 1):
                    try:
                        sock.sendto(packet, (self.camera_ip, destination_port))
                        response, address = sock.recvfrom(8192)
                    except TimeoutError:
                        self.logger.debug(f"{label}: timeout {attempt}/{retries}")
                        continue

                    if len(response) < 6:
                        self.logger.debug(f"{label}: short response {response.hex(' ')}")
                        continue

                    magic, seq = struct.unpack_from("<IH", response, 0)
                    if magic != MAGIC or seq != expected_seq:
                        self.logger.debug(f"{label}: unrelated response from {address}: magic=0x{magic:08x} seq={seq}")
                        continue

                    self.logger.debug(
                        f"{label}: ACK/RX {len(response)} bytes from "
                        f"{address[0]}:{address[1]}  {response[:32].hex(' ')}"
                    )
                    return response
            except OSError as exc:
                self.logger.warning("%s UDP transaction failed: %s", label, exc)
                return None
            finally:
                if sock is not None:
                    with suppress(OSError):
                        sock.close()

        return None

    def camera_check_online(self: SoulearCamera, quiet: bool = False) -> bool:
        """Query camera availability and cache a valid capability block.

        Parameters
        ----------
        quiet : bool, optional
            Suppress routine status messages when true.

        Returns
        -------
        bool
            Whether the camera returned a matching response.

        """
        seq = self.next_sequence()
        packet = struct.pack("<IHIH", MAGIC, seq, CMD_CHECK_ONLINE, 0)
        response = self.sync_transaction(
            packet,
            CAMERA_CONTROL_PORT,
            seq,
            timeout=0.12,
            retries=3 if quiet else 6,
            label="CHECK_ONLINE",
        )
        self.camera_connected = response is not None

        if response is None:
            if not quiet:
                self.logger.warning("CHECK_ONLINE: no response; will still try opening video")
            return False

        if not quiet:
            self.logger.info(f"CHECK_ONLINE: Kamera antwortet ({len(response)} Bytes)")

        # Native cameraCheckOnline expects a 0x80-byte status block after a
        # 12-byte protocol header. Preserve it when available.
        if len(response) >= 12:
            payload_len = struct.unpack_from("<H", response, 10)[0]
            if payload_len == 0x80 and len(response) >= 12 + payload_len:
                self.status_payload = response[12 : 12 + payload_len]
                self.capabilities_byte = self.status_payload[124]
                self.dual_stream = bool(self.capabilities_byte & 0x20)
                if not quiet:
                    self.logger.info(
                        "CHECK_ONLINE: 128-Byte-Status erhalten, "
                        f"cap[124]=0x{self.capabilities_byte:02x}, "
                        f"dual-stream={'ja' if self.dual_stream else 'nein'}, "
                        f"time-sync={'ja' if self.capabilities_byte & 0x10 else 'nein'}"
                    )
        return True

    def ensure_camera_connected(self: SoulearCamera) -> bool:
        """Verify camera availability before an explicit camera command."""
        if not self.running:
            self.camera_connected = False
            self.logger.warning("Camera command skipped: client is not running")
            return False
        self.camera_connected = self.camera_check_online(quiet=True)
        if not self.camera_connected:
            self.logger.warning("Camera command skipped: camera is not connected")
            return False
        return self.camera_connected
