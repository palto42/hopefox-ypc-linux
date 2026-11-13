"""Controls behavior for the Soulear camera client."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .facade import SoulearCamera

import struct

from .constants import CAMERA_CONTROL_PORT, CMD_LED_STATUS_GET, CMD_LED_STATUS_SET, MAGIC


class _ControlsMixin:
    def get_led_status(self: SoulearCamera, op_type: int = 1) -> tuple[int, int] | None:
        """Read LED status using the exact APK LedStatusGet transport.

        Request:
          cmd 0x0001000A
          payload length 3
          payload: [opType, 0, 0]

        The native wrapper returns payload byte1 as ledStatus and byte2 as
        lightValue.

        Parameters
        ----------
        op_type : int, optional
            Four-bit operation selector sent in the request payload.

        Returns
        -------
        tuple[int, int] | None
            LED state and brightness, or ``None`` if the response is invalid.

        """
        if not self.ensure_camera_connected():
            return None

        seq = self.next_sequence()
        payload = bytes([op_type & 0x0F, 0x00, 0x00])
        packet = (
            struct.pack(
                "<IHIH",
                MAGIC,
                seq,
                CMD_LED_STATUS_GET,
                len(payload),
            )
            + payload
        )

        self.logger.info(f"LED_GET: seq={seq} opType={op_type}")
        self.logger.debug(f"  TX: {packet.hex(' ')}")

        response = self.sync_transaction(
            packet,
            CAMERA_CONTROL_PORT,
            seq,
            timeout=0.25,
            retries=5,
            label="LED_GET",
        )
        if response is None or len(response) < 15:
            self.logger.warning("LED_GET: no or short response")
            return None

        magic, rseq = struct.unpack_from("<IH", response, 0)
        cmd_low = struct.unpack_from("<H", response, 6)[0]
        if magic != MAGIC or rseq != seq or cmd_low != (CMD_LED_STATUS_GET & 0xFFFF):
            self.logger.warning(f"LED_GET: unexpected response header: {response.hex(' ')}")
            return None

        # Native parser checks byte 9 as firmware result for these generic
        # camera commands.
        fw_result = response[9]
        if fw_result != 0:
            self.logger.warning(f"LED_GET: firmware error {fw_result}")
            return None

        payload_len = struct.unpack_from("<H", response, 10)[0]
        if payload_len < 3 or len(response) < 12 + payload_len:
            self.logger.warning(f"LED_GET: invalid payload length {payload_len}: {response.hex(' ')}")
            return None

        rp = response[12 : 12 + payload_len]
        self.led_status = rp[1]
        self.led_value = rp[2]
        self.logger.info(f"LED_GET: status={self.led_status}, value={self.led_value}")
        return self.led_status, self.led_value

    def discover_led_op_type(self: SoulearCamera) -> int | None:
        """Probe the 4-bit LED operation type with read-only GET requests.

        The native transport masks opType to four bits. Firmware result code 3
        means "unsupported/invalid operation type" on this device.  We try
        0..15 and keep the first GET that succeeds.

        Returns
        -------
        int | None
            First readable operation type, or ``None`` if none responds.

        """
        if self.led_op_type is not None:
            return self.led_op_type

        self.logger.debug("LED_PROBE: test opType 0..15 via GET")
        for op_type in range(16):
            result = self.get_led_status(op_type=op_type)
            if result is not None:
                self.logger.debug(
                    f"LED_PROBE: GET opType={op_type} lesbar "
                    f"(status={result[0]}, value={result[1]}); "
                    "das beweist noch keinen schreibbaren SET-Typ"
                )
                # Keep historical candidate 0 for diagnostic SET attempts,
                # but don't call it successfully discovered.
                self.led_op_type = op_type
                return op_type

        self.logger.debug("LED_PROBE: no opType 0..15 accepted")
        return None

    def set_led(self: SoulearCamera, enabled: bool) -> bool:
        """Switch the scope illumination using the confirmed YPC payload.

        Parameters
        ----------
        enabled : bool
            Whether to turn the camera LED on.

        Returns
        -------
        bool
            Whether the camera acknowledged the command.

        """
        if not self.ensure_camera_connected():
            return False

        # Confirmed on the tested camera: opType=1, state 0/1 and
        # brightness/value 0/100 over command 0x0001000A.
        op_type = 1

        led_status = 1 if enabled else 0
        light_value = 100 if enabled else 0

        seq = self.next_sequence()
        # Native ARM64 LedStatusSet builds exactly THREE bytes:
        #   byte0 = 0x10 | opType
        #   byte1 = ledStatus
        #   byte2 = lightValue
        # and then reuses the LedStatusGet wire helper.
        payload = bytes([
            0x10 | (op_type & 0x0F),
            led_status,
            light_value,
        ])
        packet = (
            struct.pack(
                "<IHIH",
                MAGIC,
                seq,
                CMD_LED_STATUS_SET,
                len(payload),
            )
            + payload
        )

        label = "LED_ON" if enabled else "LED_OFF"
        self.logger.info(
            f"{label}: seq={seq} opType={op_type} "
            f"ledStatus={led_status} value={light_value} "
            f"(3-Byte native payload via cmd 0x0001000A)"
        )
        self.logger.debug(f"  TX: {packet.hex(' ')}")

        response = self.sync_transaction(
            packet,
            CAMERA_CONTROL_PORT,
            seq,
            timeout=0.25,
            retries=5,
            label=label,
        )
        if response is None or len(response) < 10:
            self.logger.warning(f"{label}: no valid response")
            return False

        magic, rseq = struct.unpack_from("<IH", response, 0)
        cmd_low = struct.unpack_from("<H", response, 6)[0]
        if magic != MAGIC or rseq != seq or cmd_low != (CMD_LED_STATUS_SET & 0xFFFF):
            self.logger.warning(f"{label}: unexpected response header: {response.hex(' ')}")
            return False

        fw_result = response[9]
        if fw_result != 0:
            self.logger.warning(f"{label}: firmware error {fw_result}; response={response.hex(' ')}")
            return False

        self.led_status = led_status
        self.led_value = light_value
        self.logger.info(f"{label}: bestätigt")
        return True
