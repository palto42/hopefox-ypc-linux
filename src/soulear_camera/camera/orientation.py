"""Orientation behavior for the Soulear camera client."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .facade import SoulearCamera

import math
import struct


class _OrientationMixin:
    @staticmethod
    def circular_delta_deg(new_angle: float, old_angle: float) -> float:
        """Compute the shortest signed angular difference in ``[-180, 180)``.

        Parameters
        ----------
        new_angle : float
            Current angle in degrees.
        old_angle : float
            Reference angle in degrees.

        Returns
        -------
        float
            Signed shortest rotation from the reference to the current angle.
        """
        return ((new_angle - old_angle + 180.0) % 360.0) - 180.0

    def filter_camera_angle(self: SoulearCamera, raw_angle: float) -> float:
        """Apply wrap-aware smoothing and reject implausible angle jumps.

        Parameters
        ----------
        raw_angle : float
            Absolute sensor angle in degrees.

        Returns
        -------
        float
            Filtered absolute camera angle in the range ``[0, 360)``.
        """
        raw_angle %= 360.0
        self.raw_camera_angle = raw_angle

        if self.last_camera_angle is None:
            self.last_camera_angle = raw_angle
            return raw_angle

        delta = self.circular_delta_deg(raw_angle, self.last_camera_angle)

        # The raw metadata occasionally flips by roughly 100-180 degrees.
        if abs(delta) > self.angle_reject_jump:
            self.angle_reject_count += 1
            return self.last_camera_angle

        step = delta * self.angle_filter_alpha
        step = max(-self.angle_max_step, min(self.angle_max_step, step))
        self.last_camera_angle = (self.last_camera_angle + step) % 360.0
        return self.last_camera_angle

    def relative_auto_rotate_angle(self: SoulearCamera, filtered_angle: float | None) -> float | None:
        """Convert an absolute angle to a rotation relative to auto-rotate start.

        Parameters
        ----------
        filtered_angle : float | None
            Filtered absolute angle in degrees.

        Returns
        -------
        float | None
            Relative angle in degrees, or ``None`` before an angle is available.
        """
        if filtered_angle is None:
            return None
        if self.auto_rotate_zero is None:
            self.auto_rotate_zero = filtered_angle
        return self.circular_delta_deg(filtered_angle, self.auto_rotate_zero)

    def _update_motion_metadata(self: SoulearCamera, header: bytes) -> float | None:
        """Update gyro state and return the frame's relative rotation angle.

        Parameters
        ----------
        header : bytes
            Video transport header containing the packed motion word.

        Returns
        -------
        float | None
            Frame-relative rotation angle, or ``None`` when no angle is available.

        Raises
        ------
        struct.error
            If the header is too short to contain the motion word.
        """
        # The tested Soulear/YPC firmware packs orientation metadata into
        # one 32-bit little-endian word at bytes 6..9:
        # little-endian word at bytes 6..9:
        #
        #   bits  0.. 8 : magnitude A
        #   bit       9 : sign A
        #   bits 10..18 : magnitude B
        #   bit      19 : sign B
        #   bits 20..28 : magnitude C
        #   bit      29 : sign C
        #
        # A one-revolution calibration log showed that atan2(A, B) unwraps to
        # ~350 degrees, matching one physical turn almost exactly.
        motion_word = struct.unpack_from("<I", header, 6)[0]

        a_mag = motion_word & 0x1FF
        a_neg = (motion_word >> 9) & 0x01

        b_mag = (motion_word >> 10) & 0x1FF
        b_neg = (motion_word >> 19) & 0x01

        c_mag = (motion_word >> 20) & 0x1FF
        c_neg = (motion_word >> 29) & 0x01

        gyro_a = -a_mag if a_neg else a_mag
        gyro_b = -b_mag if b_neg else b_mag
        gyro_c = -c_mag if c_neg else c_mag

        self.gyro_xyz = (gyro_a, gyro_b, gyro_c)
        self.gyro_packet_count += 1

        if gyro_a != 0 or gyro_b != 0:
            raw_angle = math.degrees(math.atan2(gyro_a, gyro_b)) % 360.0

            # The packed sensor angle is already continuous. Only apply a
            # light wrap-aware low-pass filter; don't reject normal motion.
            self.raw_camera_angle = raw_angle
            if self.last_camera_angle is None:
                self.last_camera_angle = raw_angle
            else:
                delta = self.circular_delta_deg(
                    raw_angle,
                    self.last_camera_angle,
                )
                self.last_camera_angle = (self.last_camera_angle + (delta * 0.45)) % 360.0

            filtered_angle = self.last_camera_angle
            camera_angle = self.relative_auto_rotate_angle(filtered_angle)
        else:
            filtered_angle = self.last_camera_angle
            camera_angle = self.relative_auto_rotate_angle(filtered_angle)

        if self.debug and (self.video_packet_count % 200 == 0):
            self.logger.debug(
                f"MOTION_PACKED: A={gyro_a} B={gyro_b} C={gyro_c} "
                f"raw={self.raw_camera_angle:.1f}° "
                f"filtered={self.last_camera_angle:.1f}° "
                f"relative={camera_angle:.1f}°"
                if camera_angle is not None
                else f"MOTION_PACKED: A={gyro_a} B={gyro_b} C={gyro_c} angle=?"
            )

        return camera_angle
