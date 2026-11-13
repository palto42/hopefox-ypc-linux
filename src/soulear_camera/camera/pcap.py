"""Classic PCAP support and packet-capture errors."""

import struct
from collections.abc import Iterator
from typing import IO, Literal


class CaptureError(RuntimeError):
    """Report an optional packet-capture setup or decoding failure."""

    def __init__(
        self,
        code: Literal["header", "format", "link", "ip", "interface", "dumpcap", "stdout"],
        detail: str | int | None = None,
    ) -> None:
        """Create a capture error from a stable code and optional detail.

        Parameters
        ----------
        code : Literal["header", "format", "link", "ip", "interface", "dumpcap", "stdout"]
            Failure category used to select the user-facing message.
        detail : str | int | None, optional
            PCAP format or link-type detail for malformed input.
        """
        if code == "header":
            message = "dumpcap lieferte keinen PCAP-Header"
        elif code == "format":
            message = f"Unbekanntes PCAP-Format: {detail}"
        elif code == "link":
            message = f"Unerwarteter PCAP-Linktype {detail}; Ethernet (1) erwartet"
        elif code == "ip":
            message = "ip command not found"
        elif code == "interface":
            message = "dumpcap mode needs a network interface; use --interface"
        elif code == "dumpcap":
            message = "dumpcap not found; install Wireshark CLI or run without --dumpcap"
        else:
            message = "dumpcap stdout fehlt"
        super().__init__(message)


class PcapStreamReader:
    """Read classic PCAP records from a binary stream."""

    def __init__(self, stream: IO[bytes]) -> None:
        """Initialize a reader for a binary PCAP stream.

        Parameters
        ----------
        stream : IO[bytes]
            Open stream positioned at the PCAP global header.
        """
        self.stream = stream
        self.endian: str | None = None
        self.linktype: int | None = None

    def read_exact(self, size: int) -> bytes | None:
        """Read exactly ``size`` bytes, or return ``None`` at end of stream.

        Parameters
        ----------
        size : int
            Number of bytes to read.

        Returns
        -------
        bytes | None
            Read data, or ``None`` if the stream ends before any more data.

        Raises
        ------
        OSError
            If the underlying stream cannot be read.
        """
        result = bytearray()
        while len(result) < size:
            chunk = self.stream.read(size - len(result))
            if not chunk:
                return None
            result.extend(chunk)
        return bytes(result)

    def read_global_header(self) -> str:
        """Validate the PCAP global header and return its byte order.

        Returns
        -------
        str
            ``"<"`` for little-endian records or ``">"`` for big-endian records.

        Raises
        ------
        CaptureError
            If the header is missing, has an unsupported magic value, or does not use Ethernet.
        OSError
            If the underlying stream cannot be read.
        """
        header = self.read_exact(24)
        if header is None:
            raise CaptureError("header")

        magic = header[:4]
        if magic in (b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1"):
            endian = "<"
        elif magic in (b"\xa1\xb2\xc3\xd4", b"\xa1\xb2\x3c\x4d"):
            endian = ">"
        else:
            raise CaptureError("format", magic.hex(" "))

        self.endian = endian
        _, _, _, _, _, self.linktype = struct.unpack(endian + "HHIIII", header[4:24])
        if self.linktype != 1:
            raise CaptureError("link", self.linktype)
        return endian

    def packets(self) -> Iterator[bytes]:
        """Yield packet records after validating the global header.

        Returns
        -------
        Iterator[bytes]
            Captured Ethernet frames in file order.

        Raises
        ------
        CaptureError
            If the global header is invalid.
        OSError
            If the underlying stream cannot be read.
        """
        endian = self.read_global_header()
        while True:
            packet_header = self.read_exact(16)
            if packet_header is None:
                return
            _, _, captured_len, _ = struct.unpack(endian + "IIII", packet_header)
            packet = self.read_exact(captured_len)
            if packet is None:
                return
            yield packet
