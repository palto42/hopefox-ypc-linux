"""Public camera facade and shared camera state."""

import logging
import threading
from collections import deque

from ..signals import Signals
from .capture import _CaptureMixin
from .controls import _ControlsMixin
from .diagnostics import _DiagnosticsMixin
from .ethernet import _EthernetMixin
from .image import _ImageMixin
from .legacy import _LegacyVideoMixin
from .lifecycle import _LifecycleMixin
from .network import _NetworkMixin
from .orientation import _OrientationMixin
from .presence import _PresenceMixin
from .publishing import _PublishingMixin
from .reassembly import _ReassemblyMixin
from .recording import _RecordingMixin
from .stream_control import _StreamControlMixin
from .transactions import _TransactionMixin
from .video import _VideoMixin
from .video_transport import _VideoTransportMixin
from .watchdog import _WatchdogMixin


class SoulearCamera(
    _NetworkMixin,
    _PresenceMixin,
    _TransactionMixin,
    _ControlsMixin,
    _StreamControlMixin,
    _RecordingMixin,
    _ImageMixin,
    _CaptureMixin,
    _DiagnosticsMixin,
    _LegacyVideoMixin,
    _EthernetMixin,
    _VideoTransportMixin,
    _OrientationMixin,
    _VideoMixin,
    _ReassemblyMixin,
    _PublishingMixin,
    _WatchdogMixin,
    _LifecycleMixin,
):
    """Manage camera UDP communication, JPEG reassembly, and local recording."""

    def __init__(
        self,
        interface: str | None,
        use_dumpcap: bool,
        local_ip: str,
        camera_ip: str,
        broadcast_ip: str,
        signals: Signals,
        debug: bool = False,
        keepalives: bool = True,
        online_monitor: bool = True,
        fallback_force: bool = True,
        auto_reopen: bool = True,
    ) -> None:
        """Initialize camera configuration and runtime state.

        Parameters
        ----------
        interface : str | None
            Optional network interface used by dumpcap capture.
        use_dumpcap : bool
            Select dumpcap instead of native UDP receive sockets.
        local_ip : str
            Local IPv4 address to bind for camera communication.
        camera_ip : str
            Camera IPv4 address.
        broadcast_ip : str
            IPv4 broadcast destination for presence packets.
        signals : Signals
            Qt signals used to publish frames and status updates.
        debug : bool, optional
            Enable verbose protocol diagnostics.
        keepalives : bool, optional
            Enable camera-presence packets.
        online_monitor : bool, optional
            Enable periodic online checks.
        fallback_force : bool, optional
            Try the force-open command when the normal stream does not start.
        auto_reopen : bool, optional
            Reopen the stream after an extended packet stall.
        """
        self.interface = interface
        self.use_dumpcap = bool(use_dumpcap)
        self.local_ip = local_ip
        self.camera_ip = camera_ip
        self.broadcast_ip = broadcast_ip
        self.signals = signals
        self.debug = debug
        self.logger = logging.getLogger(__name__)
        self.keepalives_enabled = keepalives
        self.online_monitor_enabled = online_monitor
        self.fallback_force = fallback_force
        self.auto_reopen = auto_reopen

        self.running = False
        self.camera_connected = False
        self.reconfiguring = False
        self.seq_lock = threading.Lock()
        self.protocol_sequence = 1
        self.command_lock = threading.Lock()
        self.packet_process_lock = threading.Lock()

        # The camera sends video to dynamically allocated UDP ports advertised
        # by OPEN_VIDEO. Normal operation uses ordinary UDP recvfrom() sockets.
        # dumpcap is available only as an optional diagnostic capture backend.
        self.video_socket = None
        self.video2_socket = None
        self.video_port = 0
        self.video2_port = 0

        self.presence_video_socket = None
        self.presence_control1_socket = None
        self.presence_control2_socket = None

        self.dumpcap_process = None
        self.capture_thread = None
        self.receiver_threads = []
        self.keepalive_thread = None
        self.online_thread = None
        self.watchdog_thread = None

        self.first_video_event = threading.Event()
        self.last_video_packet_time = None
        self.last_open_time = 0.0
        self.last_reopen_time = 0.0
        self.normal_open_acked = False
        self.force_open_used = False

        self.status_payload = None
        self.capabilities_byte = 0
        self.dual_stream = False

        # JPEG reassembly
        self.jpeg_lock = threading.Lock()
        self.jpeg_buffer = bytearray()
        self.collecting_jpeg = False
        self.current_frame_id = None
        self.expected_packet_sequence = None
        self.current_width = 0
        self.current_height = 0
        self.current_camera_angle = None

        # Stats
        self.raw_packet_count = 0
        self.video_packet_count = 0
        self.video_log_every = 100
        self.jpeg_log_every = 25
        self._last_logged_jpeg_resolution = None
        self.control_packet_count = 0

        # Diagnostics
        self.notify_packet_count = 0
        self.stale_video_count = 0
        self._last_stale_log_time = 0.0
        self._last_stale_port = None
        self._packet_loss_events = 0
        self._last_packet_loss_log_time = 0.0

        #  NE7-compatible legacy 44506 JPEG stream
        self.legacy_frame_number = None
        self.legacy_chunks = {}
        self.legacy_last_chunk = None
        self.legacy_packet_count = 0
        self.legacy_frame_count = 0

        #  real camera RecordStatusSync + local video capture
        self.camera_recording = False
        self.record_remote_active = False
        self.record_remote_error = None
        self.record_start_resolution = None
        self.record_seen_resolutions = set()
        self.record_frame_count = 0
        self.record_output_path = None
        self.record_ffmpeg = None
        self.record_lock = threading.Lock()
        self.record_fps = 20
        self.record_auto_rotate = False

        self.frame_count = 0
        self.dropped_frames = 0
        self.nack_count = 0
        self.last_jpeg = None
        self.last_jpeg_angle = None

        # Track the actual dimensions encoded in the JPEG SOF marker.
        # The proprietary transport header advertises 640x480 on the tested
        # YPC device while the JPEG payload itself is 480x480.
        self.last_jpeg_resolution = None
        self.last_transport_resolution = None

        #  recording-style preview buffer.
        # The camera delivers frames with noticeable timing jitter (~16-17 fps).
        # Instead of showing them immediately, the GUI plays a small FIFO at a
        # fixed 20 Hz, exactly like the successful MJPEG recording timeline.
        self.gui_frame_lock = threading.Lock()
        self.gui_frame_queue = deque(maxlen=12)
        self.gui_last_frame = None
        self.gui_preview_started = False
        self.gui_preview_target_buffer = 4
        self.gui_preview_max_latency_frames = 8

        #  camera-side light control and per-frame gyro angle.
        # APK closeOrOpenDeviceLed(boolean):
        #   opType=1
        #   ON : ledStatus=1, lightValue=100
        #   OFF: ledStatus=0, lightValue=0
        self.led_status = None
        self.led_value = None
        self.led_op_type = None

        self.auto_rotate = False
        self.last_camera_angle = None  # filtered absolute angle
        self.raw_camera_angle = None
        self.auto_rotate_zero = None  # angle at enable time
        self.angle_reject_count = 0
        self.angle_filter_alpha = 0.35
        self.angle_max_step = 20.0
        self.angle_reject_jump = 75.0
        self.gyro_packet_count = 0
        self.gyro_xyz = None

        # RX FPS is measured over a real time window instead of smoothing
        # per-frame intervals (UDP delivery can arrive in short bursts).
        self.fps_window_start = None
        self.fps_window_frames = 0
        self.fps_average = 0.0
