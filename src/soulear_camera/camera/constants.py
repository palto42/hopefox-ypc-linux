"""Wire-level constants used by the Soulear camera client."""

from importlib.metadata import version

__version__ = version("hopefox-ypc-linux")

MAGIC = 0xFFEEFFEE

CMD_CHECK_ONLINE = 0x00010001

CMD_OPEN_VIDEO = 0x00010004  # internal proOpenVideo (normal stream)

CMD_OPEN_VIDEO_FORCE = 0x00010016  # exported openVideoForceApi

CMD_SET_TIME = 0x0001001A

CMD_RECORD_STATUS_SYNC = 0x0001001B  # native proRecordStatusSync

CMD_NACK = 0x0000000B

CMD_LED_STATUS_GET = 0x0001000A

CMD_LED_STATUS_SET = 0x0001000A  # native LedStatusSet reuses LedStatusGet wire helper

CAMERA_CONTROL_PORT = 10005

CAMERA_VIDEO_COMMAND_PORT = 10006

PRESENCE_VIDEO_PORT = 44506

PRESENCE_CONTROL1_PORT = 52219

PRESENCE_CONTROL2_PORT = 52220

PRESENCE_CONTROL2_DEST = 46526

KEEPALIVE_VIDEO = bytes.fromhex("20 36")

KEEPALIVE_CONTROL1 = bytes.fromhex("86 06 01")

KEEPALIVE_CONTROL2 = bytes.fromhex("66 30 01 01")

VIDEO_HEADER_SIZE = 16

JPEG_SOI = b"\xff\xd8"

JPEG_EOI = b"\xff\xd9"

MAX_JPEG_SIZE = 5 * 1024 * 1024
