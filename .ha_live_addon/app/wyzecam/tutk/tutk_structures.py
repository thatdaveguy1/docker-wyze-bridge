"""TUTK FFI data structures, type definitions, and ctypes Structure subclasses.

This module contains the :class:`ctypes.Structure` subclasses used to marshal
data to and from the TUTK shared library (``libIOTCAPIs_ALL.so``).  It is a
pure-data module -- it defines no FFI call bindings and no error classes.
"""
import logging
from ctypes import (
    POINTER,
    Structure,
    c_char,
    c_char_p,
    c_int8,
    c_int32,
    c_ubyte,
    c_uint8,
    c_uint16,
    c_uint32,
    cast,
    sizeof,
)
from typing import Union

logger = logging.getLogger(__name__)

__all__ = [
    "FormattedStructure",
    "SInfoStructEx",
    "FrameInfoStruct",
    "FrameInfo3Struct",
    "St_IOTCCheckDeviceInput",
    "St_IOTCCheckDeviceOutput",
    "St_IOTCConnectInput",
    "LogAttr",
    "AVClientStartInConfig",
    "AVClientStartOutConfig",
    "get_frame_info",
]


class FormattedStructure(Structure):
    def __str__(self):
        fields = "\n\t".join(
            [
                f"{field[0]}: {getattr(self, field[0])}"
                for field in self._fields_
                # if getattr(self, field[0])
            ]
        )
        return f"{self.__class__.__name__}:\n\t{fields}"

class SInfoStructEx(FormattedStructure):
    """
    Result of iotc_session_check(), this struct holds a bunch of diagnostic
    data about the state of the connection to the camera.

    :var mode: 0: P2P mode, 1: Relay mode, 2: LAN mode
    :vartype mode: int
    :var c_or_d: 0: As a Client, 1: As a Device
    :vartype c_or_d: int
    :var uid: The UID of the device.
    :vartype uid: str
    :var remote_ip: The IP address of remote site used during this IOTC session.
    :vartype remote_ip: str
    :var remote_port: The port number of remote site used during this IOTC session.
    :vartype remote_port: int
    :var tx_packet_count: The total packets sent from the device and the client during this IOTC session.
    :vartype tx_packet_count: int
    :var rx_packet_count: The total packets received in the device and the client during this IOTC session
    :vartype rx_packet_count: int
    :var iotc_version: version number of the IOTC device.
    :vartype iotc_version: int
    :var vendor_id: id of the vendor of the device
    :vartype vendor_id: int
    :var product_id: id of the product of the device
    :vartype product_id: int
    :var group_id: id of the group of the device
    :vartype group_id: int
    :var nat_type: The remote NAT type.
    :vartype nat_type: int
    :var is_secure: 0: The IOTC session is in non-secure mode, 1: The IOTC session is in secure mode
    :vartype is_secure: int

    """

    _fields_ = [
        ("size", c_uint32),  # size of the structure
        ("mode", c_uint8),  # 0: P2P mode, 1: Relay mode, 2: LAN mode
        ("c_or_d", c_int8),  # 0: As a Client, 1: As a Device
        ("uid", c_char * 21),  # The UID of the device.
        (
            "remote_ip",
            c_char * 47,
        ),  # The IP address of remote site used during this IOTC session.
        (
            "remote_port",
            c_uint16,
        ),  # The port number of remote site used during this IOTC session.
        (
            "tx_packet_count",
            c_uint32,
        ),  # The total packets sent from the device and the client during this IOTC session.
        (
            "rx_packet_count",
            c_uint32,
        ),  # The total packets received in the device and the client during this IOTC session
        ("iotc_version", c_uint32),
        ("vendor_id", c_uint16),
        ("product_id", c_uint16),
        ("group_id", c_uint16),
        (
            "is_secure",
            c_uint8,
        ),  # 0: The IOTC session is in non-secure mode, 1: The IOTC session is in secure mode
        (
            "local_nat_type",
            c_uint8,
        ),  # The local NAT type, 0: Unknown type, 1: Type 1, 2: Type 2, 3: Type 3, 10: TCP only
        (
            "remote_nat_type",
            c_uint8,
        ),  # The remote NAT type, 0: Unknown type, 1: Type 1, 2: Type 2, 3: Type 3, 10: TCP only
        ("relay_type", c_uint8),  # 0: Not Relay, 1: UDP Relay, 2: TCP Relay
        (
            "net_state",
            c_uint32,
        ),  # If no UDP packet is ever received, the first bit of value is 1, otherwise 0
        (
            "remote_wan_ip",
            c_char * 47,
        ),  # The WAN IP address of remote site used during this IOTC session and it is only valid in P2P or Relay mode
        (
            "remote_wan_port",
            c_uint16,
        ),  # The WAN port number of remote site used during this IOTC session and it is only valid in P2P or Relay mode
        (
            "is_nebula",
            c_uint8,
        ),  # 0: Session not created by nebula, 1: Session created by nebula
    ]
    def __repr__(self):
        return (f"SInfoStructEx("
                f"{self.size=}, "
                f"{self.mode=}, "
                f"{self.c_or_d=}, "
                f"uid={self.uid.decode('ascii')!r}, "
                f"remote_ip={self.remote_ip.decode('ascii')!r}, "
                f"{self.remote_port=}, "
                f"{self.tx_packet_count=}, "
                f"{self.rx_packet_count=}, "
                f"{self.iotc_version=}, "
                f"{self.vendor_id=}, "
                f"{self.product_id=}, "
                f"{self.group_id=}, "
                f"{self.is_secure=}, "
                f"{self.local_nat_type=}, "
                f"{self.remote_nat_type=}, "
                f"{self.relay_type=}, "
                f"{self.net_state=}, "
                f"remote_wan_ip={self.remote_wan_ip.decode('ascii')!r}, "
                f"{self.remote_wan_port=}, "
                f"{self.is_nebula=})")

class FrameInfoStruct(FormattedStructure):
    """
    A struct recieved on every video frame, with lots of useful information
    about the frame sent by the camera.

    :var codec_id: 78: h264 80: h265
    :vartype codec_id: int
    :var is_keyframe: True if the frame being described is a keyframe
    :vartype is_keyframe: int
    :var cam_index: The index of the camera
    :vartype cam_index: int
    :var online_num: Not clear
    :vartype online_num: int
    :var framerate: framerate of the video frame, in frames / second
    :vartype framerate: int
    :var frame_size: frame size of the video frame, either `FRAME_SIZE_1080P` or `FRAME_SIZE_360P`
    :vartype frame_size: int
    :var bitrate: bitrate of the video frame, as configured.
    :vartype bitrate: int
    :var timestamp_ms: the millisecond component of the timestamp.
    :vartype timestamp_ms: int
    :var timestamp: the seconds component of the timestamp.
    :vartype timestamp: int
    :var frame_len: the size of the data sent by the camera, in bytes.
    :vartype frame_len: int
    :var frame_no: the current frame number as recorded by the camera
    :vartype frame_no: int
    :var ac_mac_addr: unknown
    :vartype ac_mac_addr: str
    :var n_play_token: unknown
    :vartype n_play_token: int
    """

    _fields_ = [
        ("codec_id", c_uint16),
        ("is_keyframe", c_uint8),
        ("cam_index", c_uint8),
        ("online_num", c_uint8),
        ("framerate", c_uint8),
        ("frame_size", c_uint8),
        ("bitrate", c_uint8),
        ("timestamp_ms", c_uint32),
        ("timestamp", c_uint32),
        ("frame_len", c_uint32),
        ("frame_no", c_uint32),
        ("ac_mac_addr", c_char * 12),
        ("n_play_token", c_int32),
    ]

    def __repr__(self):
        is_keyframe = True if self.is_keyframe else False
        ac_mac_addr = f"{self.ac_mac_addr.decode('ascii')!r}"
        return (f"FrameInfoStruct("
                f"{self.codec_id=}, "
                f"{is_keyframe=}, "
                f"{self.cam_index=}, "
                f"{self.online_num=}, "
                f"{self.framerate=}, "
                f"{self.frame_size=}, "
                f"{self.bitrate=}, "
                f"{self.timestamp_ms=}, "
                f"{self.timestamp=}, "
                f"{self.frame_len=}, "
                f"{self.frame_no=}, "
                f"{ac_mac_addr=}, "
                f"{self.n_play_token=})")

class FrameInfo3Struct(FormattedStructure):
    _fields_ = [
        ("codec_id", c_uint16),
        ("is_keyframe", c_uint8),
        ("cam_index", c_uint8),
        ("online_num", c_uint8),
        ("framerate", c_uint8),
        ("frame_size", c_uint8),
        ("bitrate", c_uint8),
        ("timestamp_ms", c_uint32),
        ("timestamp", c_uint32),
        ("frame_len", c_uint32),
        ("frame_no", c_uint32),
        ("ac_mac_addr", c_char * 12),
        ("n_play_token", c_int32),
        ("face_pos_x", c_uint16),
        ("face_pos_y", c_uint16),
        ("face_width", c_uint16),
        ("face_height", c_uint16),
    ]

    def __repr__(self):
        is_keyframe = True if self.is_keyframe else False
        ac_mac_addr = f"{self.ac_mac_addr.decode('ascii')!r}"
        return (f"FrameInfo3Struct("
                f"{self.codec_id=}, "
                f"{is_keyframe=}, "
                f"{self.cam_index=}, "
                f"{self.online_num=}, "
                f"{self.framerate=}, "
                f"{self.frame_size=}, "
                f"{self.bitrate=}, "
                f"{self.timestamp_ms=}, "
                f"{self.timestamp=}, "
                f"{self.frame_len=}, "
                f"{self.frame_no=}, "
                f"{ac_mac_addr=}, "
                f"{self.n_play_token=}, "
                f"{self.face_pos_x=}, "
                f"{self.face_pos_y=}, "
                f"{self.face_width=}, "
                f"{self.face_height=})")

class St_IOTCCheckDeviceInput(FormattedStructure):
    _fields_ = [
        ("cb", c_uint32),
        ("auth_type", c_uint32),
        ("auth_key", c_char * 8),
    ]

    def __repr__(self):
        return (f"St_IOTCCheckDeviceInput("
                f"{self.cb=}, "
                f"{self.auth_type=}, "
                f"auth_key={self.auth_key.decode('ascii')!r})")

class St_IOTCCheckDeviceOutput(FormattedStructure):
    _fields_ = [
        ("status", c_uint32),
        ("last_login", c_uint32),
    ]

    def __repr__(self):
        return (f"St_IOTCCheckDeviceOutput("
                f"{self.status=}, "
                f"{self.last_login=})")

class St_IOTCConnectInput(FormattedStructure):
    _fields_ = [
        ("cb", c_uint32),
        ("authentication_type", c_uint32),
        ("auth_key", c_char * 8),
        ("timeout", c_uint32),
    ]

    def __repr__(self):
        auth_key = f"{self.auth_key.decode('ascii')!r}"
        return (f"St_IOTCConnectInput("
                f"{self.cb=}, "
                f"{self.authentication_type=}, "
                f"{auth_key=}, "
                f"{self.timeout=})")

class LogAttr(FormattedStructure):
    _fields_ = [
        ("path", c_char_p),
        ("log_level", c_uint32),
        ("file_max_size", c_int32),
        ("file_max_count", c_int32),
    ]

    def __repr__(self):
        path = f"{self.path.decode('ascii')!r}" if self.path else None
        return (f"LogAttr("
                f"{path=}, "
                f"{self.log_level=}, "
                f"{self.file_max_size=}, "
                f"{self.file_max_count=})")

class AVClientStartInConfig(FormattedStructure):
    _fields_ = [
        ("cb", c_uint32),
        ("iotc_session_id", c_uint32),
        ("iotc_channel_id", c_uint8),
        ("timeout_sec", c_uint32),
        ("account_or_identity", c_char_p),
        ("password_or_token", c_char_p),
        ("resend", c_int32),
        ("security_mode", c_uint32),
        ("auth_type", c_uint32),
        ("sync_recv_data", c_int32),
    ]

    def __repr__(self):
        account_or_identity = f"{self.account_or_identity.decode('ascii')!r}" if self.account_or_identity else None
        password_or_token = f"{self.password_or_token.decode('ascii')!r}" if self.password_or_token else None
        return (f"AVClientStartInConfig("
                f"{self.cb=}, "
                f"{self.iotc_session_id=}, "
                f"{self.iotc_channel_id=}, "
                f"{self.timeout_sec=}, "
                f"{account_or_identity=}, "
                f"{password_or_token=}, "
                f"{self.resend=}, "
                f"{self.security_mode=}, "
                f"{self.auth_type=}, "
                f"{self.sync_recv_data=})")

class AVClientStartOutConfig(FormattedStructure):
    _fields_ = [
        ("cb", c_uint32),
        ("server_type", c_uint32),
        ("resend", c_int32),
        ("two_way_streaming", c_int32),
        ("sync_recv_data", c_int32),
        ("security_mode", c_uint32),
    ]

    def __repr__(self):
        return (f"AVClientStartOutConfig("
                f"{self.cb=}, "
                f"{self.server_type=}, "
                f"{self.resend=}, "
                f"{self.two_way_streaming=}, "
                f"{self.sync_recv_data=}, "
                f"{self.security_mode=})")


def get_frame_info(frame_info_buffer, frame_info_actual_len):
    frame_info: Union[FrameInfoStruct, FrameInfo3Struct | None]

    if frame_info_actual_len.value == sizeof(FrameInfo3Struct):
        frame_info = cast(frame_info_buffer, POINTER(FrameInfo3Struct)).contents
    elif frame_info_actual_len.value == sizeof(FrameInfoStruct):
        frame_info = cast(frame_info_buffer, POINTER(FrameInfoStruct)).contents
    else:
        logger.warning(f"[TUTK] Unexpected frame length {frame_info_actual_len=}")
        frame_info = None

    return frame_info
