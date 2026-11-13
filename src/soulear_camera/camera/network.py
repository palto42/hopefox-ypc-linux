"""Network behavior for the Soulear camera client."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .facade import SoulearCamera

import socket
import struct
import threading

from .constants import PRESENCE_CONTROL1_PORT, PRESENCE_CONTROL2_PORT, PRESENCE_VIDEO_PORT


class _NetworkMixin:
    def make_dynamic_socket(self: SoulearCamera) -> socket.socket:
        """Create a timed UDP socket bound to an ephemeral local port.

        Returns
        -------
        socket.socket
            Bound datagram socket for receiving camera traffic.

        Raises
        ------
        OSError
            If socket creation or binding fails.
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.local_ip, 0))
        sock.settimeout(0.2)
        return sock

    def make_bound_broadcast_socket(self: SoulearCamera, port: int) -> socket.socket:
        """Create a broadcast-enabled UDP socket bound to ``port``.

        Parameters
        ----------
        port : int
            Local UDP port to bind.

        Returns
        -------
        socket.socket
            Bound broadcast datagram socket.

        Raises
        ------
        OSError
            If socket creation or binding fails.
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind((self.local_ip, port))
        sock.settimeout(0.2)
        return sock

    def create_native_sockets(self: SoulearCamera) -> None:
        """Create the dynamic video sockets and optional presence sockets.

        Raises
        ------
        OSError
            If either required video socket cannot be created or bound.
        """
        self.video_socket = self.make_dynamic_socket()
        self.video2_socket = self.make_dynamic_socket()
        self.video_port = self.video_socket.getsockname()[1]
        self.video2_port = self.video2_socket.getsockname()[1]

        self.logger.info(f"UDP video receivers ready on ports {self.video_port} and {self.video2_port}")

        if self.keepalives_enabled:
            try:
                self.presence_video_socket = self.make_bound_broadcast_socket(PRESENCE_VIDEO_PORT)
                self.presence_control1_socket = self.make_bound_broadcast_socket(PRESENCE_CONTROL1_PORT)
                self.presence_control2_socket = self.make_bound_broadcast_socket(PRESENCE_CONTROL2_PORT)
                self.logger.info("Presence sockets:     44506, 52219, 52220")
            except OSError as exc:
                self.logger.warning(f"Presence sockets unavailable: {exc}")
                self.keepalives_enabled = False

    def _build_synthetic_udp_frame(
        self: SoulearCamera, src_ip: str, src_port: int, dst_port: int, payload: bytes
    ) -> bytes:
        """Wrap a UDP datagram in the Ethernet/IP format expected by the parser.

        Parameters
        ----------
        src_ip : str
            Source IPv4 address.
        src_port : int
            Source UDP port.
        dst_port : int
            Destination UDP port.
        payload : bytes
            UDP payload to wrap.

        Returns
        -------
        bytes
            Synthetic Ethernet frame using the configured local IP as destination.

        Raises
        ------
        OSError
            If either IPv4 address is invalid.
        """
        udp_len = 8 + len(payload)
        total_len = 20 + udp_len
        ethernet = (b"\x00" * 12) + b"\x08\x00"
        ip_header = struct.pack(
            "!BBHHHBBH4s4s",
            0x45,
            0,
            total_len,
            0,
            0,
            64,
            17,
            0,
            socket.inet_aton(src_ip),
            socket.inet_aton(self.local_ip),
        )
        udp_header = struct.pack("!HHHH", src_port, dst_port, udp_len, 0)
        return ethernet + ip_header + udp_header + payload

    def socket_receive_loop(self: SoulearCamera, sock: socket.socket, local_port: int, label: str) -> None:
        """Receive datagrams from one socket and feed valid camera packets onward.

        Parameters
        ----------
        sock : socket.socket
            UDP socket being serviced.
        local_port : int
            Local destination port represented in the synthetic frame.
        label : str
            Name used in diagnostics for this receiver.
        """
        received_count = 0
        while self.running:
            try:
                payload, address = sock.recvfrom(65535)
            except TimeoutError:
                continue
            except OSError:
                if self.running:
                    self.logger.exception("%s socket receive failed", label)
                return
            except Exception:
                if self.running:
                    self.logger.exception("%s receive error", label)
                return

            received_count += 1
            src_ip, src_port = address[0], address[1]
            self.log_video_socket_sample(label, received_count, local_port, src_ip, src_port, payload)
            if src_ip != self.camera_ip:
                continue
            frame = self._build_synthetic_udp_frame(src_ip, src_port, local_port, payload)
            try:
                with self.packet_process_lock:
                    self.process_ethernet_frame(frame)
            except Exception:
                if self.running:
                    self.logger.exception("%s packet processing failed", label)

    def log_video_socket_sample(
        self: SoulearCamera, label: str, count: int, local_port: int, src_ip: str, src_port: int, payload: bytes
    ) -> None:
        """Log a small sample of raw video-socket traffic in debug mode."""
        if not (self.debug and label.startswith("video-") and count <= 5):
            return
        self.logger.debug(
            f"UDP RX {label} #{count}: {src_ip}:{src_port} -> "
            f"{self.local_ip}:{local_port} len={len(payload)} "
            f"data={payload[:20].hex(' ')}"
        )

    def start_socket_receivers(self: SoulearCamera) -> None:
        """Start daemon receiver threads for all available UDP sockets."""
        self.receiver_threads = []
        sockets = [
            (self.video_socket, self.video_port, "video-primary"),
            (self.video2_socket, self.video2_port, "video-secondary"),
            (self.presence_video_socket, PRESENCE_VIDEO_PORT, "presence-44506"),
            (self.presence_control1_socket, PRESENCE_CONTROL1_PORT, "presence-52219"),
            (self.presence_control2_socket, PRESENCE_CONTROL2_PORT, "presence-52220"),
        ]
        for sock, port, label in sockets:
            if sock is None:
                continue
            thread = threading.Thread(
                target=self.socket_receive_loop,
                args=(sock, port, label),
                daemon=True,
            )
            thread.start()
            self.receiver_threads.append(thread)
        self.logger.info("UDP socket capture active")
