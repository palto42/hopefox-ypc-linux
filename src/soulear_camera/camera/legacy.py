"""LegacyVideo behavior for the Soulear camera client."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .facade import SoulearCamera

from .constants import JPEG_EOI, JPEG_SOI


class _LegacyVideoMixin:
    @staticmethod
    def jpeg_dimensions(jpeg: bytes) -> tuple[int, int]:
        """Read image dimensions from the first supported JPEG SOF marker.

        Parameters
        ----------
        jpeg : bytes
            Encoded JPEG data.

        Returns
        -------
        tuple[int, int]
            Width and height in pixels, or ``(0, 0)`` when no valid SOF marker exists.
        """
        try:
            i = 2
            n = len(jpeg)
            while i + 9 < n:
                if jpeg[i] != 0xFF:
                    i += 1
                    continue
                while i < n and jpeg[i] == 0xFF:
                    i += 1
                if i >= n:
                    break
                marker = jpeg[i]
                i += 1
                if marker in (0xD8, 0xD9):
                    continue
                if i + 2 > n:
                    break
                seglen = int.from_bytes(jpeg[i : i + 2], "big")
                if seglen < 2 or i + seglen > n:
                    break
                if (
                    marker
                    in (
                        0xC0,
                        0xC1,
                        0xC2,
                        0xC3,
                        0xC5,
                        0xC6,
                        0xC7,
                        0xC9,
                        0xCA,
                        0xCB,
                        0xCD,
                        0xCE,
                        0xCF,
                    )
                    and seglen >= 7
                ):
                    h = int.from_bytes(jpeg[i + 3 : i + 5], "big")
                    w = int.from_bytes(jpeg[i + 5 : i + 7], "big")
                    return w, h
                i += seglen
        except (IndexError, ValueError):
            pass
        return 0, 0

    def reset_legacy_frame(self: SoulearCamera, frame_number: int | None = None) -> None:
        """Clear the legacy chunk buffer and select an optional active frame.

        Parameters
        ----------
        frame_number : int | None, optional
            Frame identifier to begin tracking, or ``None`` to clear it.
        """
        self.legacy_frame_number = frame_number
        self.legacy_chunks = {}
        self.legacy_last_chunk = None

    def process_legacy_44506_packet(self: SoulearCamera, payload: bytes) -> None:
        """NE7-Scope compatible legacy transport.

        Header:
          byte0 frame_number
          byte1 flags (bit0 = final packet)
          byte2 chunk_number (1-based)
          byte3 flags2
        The final packet carries a 5-byte trailer after JPEG data.

        Parameters
        ----------
        payload : bytes
            One legacy UDP payload containing a frame/chunk header and image data.
        """
        if len(payload) < 5:
            return

        frame_number, flags, chunk_number, flags2 = payload[:4]
        if chunk_number == 0:
            return

        data = payload[4:]
        is_last = bool(flags & 0x01)
        if is_last:
            if len(data) < 5:
                return
            data = data[:-5]

        self.legacy_packet_count += 1

        if self.legacy_frame_number is None:
            self.reset_legacy_frame(frame_number)
        elif frame_number != self.legacy_frame_number:
            # New frame started before the old one completed.
            if self.legacy_chunks:
                self.logger.debug(
                    f"LEGACY44506: frame jump {self.legacy_frame_number}->{frame_number}; old frame dropped"
                )
            self.reset_legacy_frame(frame_number)

        self.legacy_chunks[chunk_number - 1] = data

        if self.debug and (chunk_number == 1 or is_last):
            self.logger.debug(
                f"LEGACY44506 pkt#{self.legacy_packet_count}: "
                f"frame={frame_number} chunk={chunk_number} "
                f"flags=0x{flags:02x} flags2=0x{flags2:02x} "
                f"last={'yes' if is_last else 'no'} len={len(payload)}"
            )

        if not is_last:
            return

        self.legacy_last_chunk = chunk_number
        self._finish_legacy_frame(frame_number, chunk_number)

    def _finish_legacy_frame(self: SoulearCamera, frame_number: int, chunk_number: int) -> None:
        """Validate, join, and publish the completed legacy JPEG frame.

        Parameters
        ----------
        frame_number : int
            Legacy frame identifier used in diagnostics and the emitted frame ID.
        chunk_number : int
            One-based index of the final expected chunk.
        """
        expected = set(range(chunk_number))
        present = set(self.legacy_chunks.keys())
        missing = sorted(expected - present)
        if missing:
            self.logger.warning(
                f"LEGACY44506: frame {frame_number} incomplete; missing chunks={[x + 1 for x in missing]}"
            )
            self.reset_legacy_frame()
            return

        jpeg = b"".join(self.legacy_chunks[i] for i in range(chunk_number))
        self.reset_legacy_frame()

        soi = jpeg.find(JPEG_SOI)
        if soi > 0:
            jpeg = jpeg[soi:]
        eoi = jpeg.rfind(JPEG_EOI)
        if eoi >= 0:
            jpeg = jpeg[: eoi + 2]

        if not jpeg.startswith(JPEG_SOI) or not jpeg.endswith(JPEG_EOI):
            self.logger.warning(f"LEGACY44506: frame {frame_number} is not a complete JPEG ({len(jpeg)} bytes)")
            return

        width, height = self.jpeg_dimensions(jpeg)
        self.legacy_frame_count += 1
        self.logger.debug(
            f"LEGACY44506 JPEG #{self.legacy_frame_count}: "
            f"frame={frame_number} {len(jpeg):,} Bytes "
            f"{width if width else '?'}x{height if height else '?'}"
        )

        # Feed the same GUI/output path as the modern stream.
        self.emit_jpeg(
            jpeg,
            f"L{frame_number}",
            width,
            height,
        )
