"""TUTK core constants, the :class:`TutkError` exception, and library loading.

This module holds the non-FFI parts of the TUTK interface: numeric constants
(bitrates, frame sizes, error codes), the :class:`TutkError` runtime error
with its full name-mapping table, and :func:`load_library` which loads the
shared ``libIOTCAPIs_ALL`` library.
"""

import logging
import pathlib
from ctypes import CDLL, cdll

logger = logging.getLogger(__name__)

BITRATE_360P = 0x1E
"""
The bitrate used by the "360P" setting in the app.  Approx 30 KB/s.
"""

BITRATE_SD = 0x3C
"""
The bitrate used by the "SD" setting in the app.  Approx 60 KB/s.
"""
BITRATE_HD = 0x78
"""
The bitrate used by the "HD" setting in the app.  Approx 120 KB/s.
"""

BITRATE_SUPER_HD = 0x96
"""
A bitrate higher than the "HD" setting in the app.  Approx 150 KB/s.
"""

BITRATE_SUPER_SUPER_HD = 0xF0
"""
A bitrate higher than the "HD" setting in the app.  Approx 240 KB/s.
"""

FRAME_SIZE_2K = 3
"""
Represents the size of the video stream sent back from the server; 2K
or 2560x1440 pixels.
"""

FRAME_SIZE_1080P = 0
"""
Represents the size of the video stream sent back from the server; 1080P
or 1920x1080 pixels.
"""

FRAME_SIZE_360P = 1
"""
Represents the size of the video stream sent back from the server; 360P
or 640x360 pixels.
"""

FRAME_SIZE_DOORBELL_HD = 3
"""
Represents the size of the video stream sent back from the server;
portrait 1296 x 1728.
"""

FRAME_SIZE_DOORBELL_SD = 4
"""
Represents the size of the video stream sent back from the server;
portrait 480 x 640.
"""

IOTYPE_USER_DEFINED_START = 256

AV_ER_TIMEOUT = -20011
"""
An error raised when the AV library times out.
"""

AV_ER_SESSION_CLOSE_BY_REMOTE = -20015
"""
An error raised when the camera closes the connection.
"""

AV_ER_REMOTE_TIMEOUT_DISCONNECT = -20016
"""
An error raised when the IOTC session is disconnected because of no response from the camera.
"""

AV_ER_DATA_NOREADY = -20012
"""
An error raised when the client asks for data not yet available on the camera.
"""

AV_ER_INCOMPLETE_FRAME = -20013
"""
An error sent during video streaming if the camera wasn't able to send a complete frame.
"""

AV_ER_LOSED_THIS_FRAME = -20014
"""
An error sent during video streaming if the frame was lost in transmission.
"""

AV_ER_SENDIOCTRL_ALREADY_CALLED = -20021
"""
An error raised if the IOCTRL message was already sent
"""

project_root = pathlib.Path(__file__).parent


class TutkError(RuntimeError):
    name_mapping = {
        -1: "IOTC_ER_SERVER_NOT_RESPONSE",
        -2: "IOTC_ER_FAIL_RESOLVE_HOSTNAME",
        -3: "IOTC_ER_ALREADY_INITIALIZED",
        -4: "IOTC_ER_FAIL_CREATE_MUTEX",
        -5: "IOTC_ER_FAIL_CREATE_THREAD",
        -6: "IOTC_ER_FAIL_CREATE_SOCKET",
        -7: "IOTC_ER_FAIL_SOCKET_OPT",
        -8: "IOTC_ER_FAIL_SOCKET_BIND",
        -10: "IOTC_ER_UNLICENSE",
        -11: "IOTC_ER_LOGIN_ALREADY_CALLED",
        -12: "IOTC_ER_NOT_INITIALIZED",
        -13: "IOTC_ER_TIMEOUT",
        -14: "IOTC_ER_INVALID_SID",
        -15: "IOTC_ER_UNKNOWN_DEVICE",
        -16: "IOTC_ER_FAIL_GET_LOCAL_IP",
        -17: "IOTC_ER_LISTEN_ALREADY_CALLED",
        -18: "IOTC_ER_EXCEED_MAX_SESSION",
        -19: "IOTC_ER_CAN_NOT_FIND_DEVICE",
        -20: "IOTC_ER_CONNECT_IS_CALLING",
        -22: "IOTC_ER_SESSION_CLOSE_BY_REMOTE",
        -23: "IOTC_ER_REMOTE_TIMEOUT_DISCONNECT",
        -24: "IOTC_ER_DEVICE_NOT_LISTENING",
        -26: "IOTC_ER_CH_NOT_ON",
        -27: "IOTC_ER_FAIL_CONNECT_SEARCH",
        -28: "IOTC_ER_MASTER_TOO_FEW",
        -29: "IOTC_ER_AES_CERTIFY_FAIL",
        -31: "IOTC_ER_SESSION_NO_FREE_CHANNEL",
        -32: "IOTC_ER_TCP_TRAVEL_FAILED",
        -33: "IOTC_ER_TCP_CONNECT_TO_SERVER_FAILED",
        -34: "IOTC_ER_CLIENT_NOT_SECURE_MODE",
        -35: "IOTC_ER_CLIENT_SECURE_MODE",
        -36: "IOTC_ER_DEVICE_NOT_SECURE_MODE",
        -37: "IOTC_ER_DEVICE_SECURE_MODE",
        -38: "IOTC_ER_INVALID_MODE",
        -39: "IOTC_ER_EXIT_LISTEN",
        -40: "IOTC_ER_NO_PERMISSION",
        -41: "IOTC_ER_NETWORK_UNREACHABLE",
        -42: "IOTC_ER_FAIL_SETUP_RELAY",
        -43: "IOTC_ER_NOT_SUPPORT_RELAY",
        -44: "IOTC_ER_NO_SERVER_LIST",
        -45: "IOTC_ER_DEVICE_MULTI_LOGIN",
        -46: "IOTC_ER_INVALID_ARG",
        -47: "IOTC_ER_NOT_SUPPORT_PE",
        -48: "IOTC_ER_DEVICE_EXCEED_MAX_SESSION",
        -49: "IOTC_ER_BLOCKED_CALL",
        -50: "IOTC_ER_SESSION_CLOSED",
        -51: "IOTC_ER_REMOTE_NOT_SUPPORTED",
        -52: "IOTC_ER_ABORTED",
        -53: "IOTC_ER_EXCEED_MAX_PACKET_SIZE",
        -54: "IOTC_ER_SERVER_NOT_SUPPORT",
        -55: "IOTC_ER_NO_PATH_TO_WRITE_DATA",
        -56: "IOTC_ER_SERVICE_IS_NOT_STARTED",
        -57: "IOTC_ER_STILL_IN_PROCESSING",
        -58: "IOTC_ER_NOT_ENOUGH_MEMORY",
        -59: "IOTC_ER_DEVICE_IS_BANNED",
        -60: "IOTC_ER_MASTER_NOT_RESPONSE",
        -61: "IOTC_ER_RESOURCE_ERROR",
        -62: "IOTC_ER_QUEUE_FULL",
        -63: "IOTC_ER_NOT_SUPPORT",
        -64: "IOTC_ER_DEVICE_IS_SLEEP",
        -65: "IOTC_ER_TCP_NOT_SUPPORT",
        -66: "IOTC_ER_WAKEUP_NOT_INITIALIZED",
        -67: "IOTC_ER_DEVICE_REJECT_BYPORT",
        -68: "IOTC_ER_DEVICE_REJECT_BY_WRONG_AUTH_KEY",
        -69: "IOTC_ER_DEVICE_NOT_USE_KEY_AUTHENTICATION",
        -70: "IOTC_ER_DID_NOT_LOGIN",
        -71: "IOTC_ER_DID_NOT_LOGIN_WITH_AUTHKEY",
        -72: "IOTC_ER_SESSION_IN_USE",
        -90: "IOTC_ER_DEVICE_OFFLINE",
        -91: "IOTC_ER_MASTER_INVALID",
        -1001: "TUTK_ER_ALREADY_INITIALIZED",
        -1002: "TUTK_ER_INVALID_ARG",
        -1003: "TUTK_ER_MEM_INSUFFICIENT",
        -1004: "TUTK_ER_INVALID_LICENSE_KEY",
        -1005: "TUTK_ER_NO_LICENSE_KEY",
        -10000: "RDT_ER_NOT_INITIALIZED",
        -10001: "RDT_ER_ALREADY_INITIALIZED",
        -10002: "RDT_ER_EXCEED_MAX_CHANNEL",
        -10003: "RDT_ER_MEM_INSUFF",
        -10004: "RDT_ER_FAIL_CREATE_THREAD",
        -10005: "RDT_ER_FAIL_CREATE_MUTEX",
        -10006: "RDT_ER_RDT_DESTROYED",
        -10007: "RDT_ER_TIMEOUT",
        -10008: "RDT_ER_INVALID_RDT_ID",
        -10009: "RDT_ER_RCV_DATA_END",
        -10010: "RDT_ER_REMOTE_ABORT",
        -10011: "RDT_ER_LOCAL_ABORT",
        -10012: "RDT_ER_CHANNEL_OCCUPIED",
        -10013: "RDT_ER_NO_PERMISSION",
        -10014: "RDT_ER_INVALID_ARG",
        -10015: "RDT_ER_LOCAL_EXIT",
        -10016: "RDT_ER_REMOTE_EXIT",
        -10017: "RDT_ER_SEND_BUFFER_FULL",
        -10018: "RDT_ER_UNCLOSED_CONNECTION_DETECTED",
        -10019: "RDT_ER_DEINITIALIZING",
        -10020: "RDT_ER_FAIL_INITIALIZE_DTLS",
        -10021: "RDT_ER_CREATE_DTLS_FAIL",
        -10022: "RDT_ER_OPERATION_IS_INVALID",
        -10023: "RDT_ER_REMOTE_NOT_SUPPORT_DTLS",
        -10024: "RDT_ER_LOCAL_NOT_SUPPORT_DTLS",
        -20000: "AV_ER_INVALID_ARG",
        -20001: "AV_ER_BUFPARA_MAXSIZE_INSUFF",
        -20002: "AV_ER_EXCEED_MAX_CHANNEL",
        -20003: "AV_ER_MEM_INSUFF",
        -20004: "AV_ER_FAIL_CREATE_THREAD",
        -20005: "AV_ER_EXCEED_MAX_ALARM",
        -20006: "AV_ER_EXCEED_MAX_SIZE",
        -20007: "AV_ER_SERV_NO_RESPONSE",
        -20008: "AV_ER_CLIENT_NO_AVLOGIN",
        -20009: "AV_ER_WRONG_VIEWACCorPWD",
        -20010: "AV_ER_INVALID_SID",
        -20011: "AV_ER_TIMEOUT",
        -20012: "AV_ER_DATA_NOREADY",
        -20013: "AV_ER_INCOMPLETE_FRAME",
        -20014: "AV_ER_LOSED_THIS_FRAME",
        -20015: "AV_ER_SESSION_CLOSE_BY_REMOTE",
        -20016: "AV_ER_REMOTE_TIMEOUT_DISCONNECT",
        -20017: "AV_ER_SERVER_EXIT",
        -20018: "AV_ER_CLIENT_EXIT",
        -20019: "AV_ER_NOT_INITIALIZED",
        -20020: "AV_ER_CLIENT_NOT_SUPPORT",
        -20021: "AV_ER_SENDIOCTRL_ALREADY_CALLED",
        -20022: "AV_ER_SENDIOCTRL_EXIT",
        -20023: "AV_ER_NO_PERMISSION",
        -20024: "AV_ER_WRONG_ACCPWD_LENGTH",
        -20025: "AV_ER_IOTC_SESSION_CLOSED",
        -20026: "AV_ER_IOTC_DEINITIALIZED",
        -20027: "AV_ER_IOTC_CHANNEL_IN_USED",
        -20028: "AV_ER_WAIT_KEY_FRAME",
        -20029: "AV_ER_CLEANBUF_ALREADY_CALLED",
        -20030: "AV_ER_SOCKET_QUEUE_FULL",
        -20031: "AV_ER_ALREADY_INITIALIZED",
        -20032: "AV_ER_DASA_CLEAN_BUFFER",
        -20033: "AV_ER_NOT_SUPPORT",
        -20034: "AV_ER_FAIL_INITIALIZE_DTLS",
        -20035: "AV_ER_FAIL_CREATE_DTLS",
        -20036: "AV_ER_REQUEST_ALREADY_CALLED",
        -20037: "AV_ER_REMOTE_NOT_SUPPORT",
        -20038: "AV_ER_TOKEN_EXCEED_MAX_SIZE",
        -20039: "AV_ER_REMOTE_NOT_SUPPORT_DTLS",
        -20040: "AV_ER_DTLS_WRONG_PWD",
        -20041: "AV_ER_DTLS_AUTH_FAIL",
        -20042: "AV_ER_VSAAS_PULLING_NOT_ENABLE",
        -20043: "AV_ER_FAIL_CONNECT_TO_VSAAS",
        -20044: "AV_ER_PARSE_JSON_FAIL",
        -20045: "AV_ER_PUSH_NOTIFICATION_NOT_ENABLE",
        -20046: "AV_ER_PUSH_NOTIFICATION_ALREADY_ENABLED",
        -20047: "AV_ER_NO_NOTIFICATION_LIST",
        -20048: "AV_ER_HTTP_ERROR",
        -20049: "AV_ER_LOCAL_NOT_SUPPORT_DTLS",
        -21334: "AV_ER_SDK_NOT_SUPPORT_DTLS",
        -30000: "TUNNEL_ER_NOT_INITIALIZED",
        -30001: "TUNNEL_ER_EXCEED_MAX_SERVICE",
        -30002: "TUNNEL_ER_BIND_LOCAL_SERVICE",
        -30003: "TUNNEL_ER_LISTEN_LOCAL_SERVICE",
        -30004: "TUNNEL_ER_FAIL_CREATE_THREAD",
        -30005: "TUNNEL_ER_ALREADY_CONNECTED",
        -30006: "TUNNEL_ER_DISCONNECTED",
        -30007: "TUNNEL_ER_ALREADY_INITIALIZED",
        -30008: "TUNNEL_ER_AUTH_FAILED",
        -30009: "TUNNEL_ER_EXCEED_MAX_LEN",
        -30010: "TUNNEL_ER_INVALID_SID",
        -30011: "TUNNEL_ER_UID_UNLICENSE",
        -30012: "TUNNEL_ER_UID_NO_PERMISSION",
        -30013: "TUNNEL_ER_UID_NOT_SUPPORT_RELAY",
        -30014: "TUNNEL_ER_DEVICE_NOT_ONLINE",
        -30015: "TUNNEL_ER_DEVICE_NOT_LISTENING",
        -30016: "TUNNEL_ER_NETWORK_UNREACHABLE",
        -30017: "TUNNEL_ER_FAILED_SETUP_CONNECTION",
        -30018: "TUNNEL_ER_LOGIN_FAILED",
        -30019: "TUNNEL_ER_EXCEED_MAX_SESSION",
        -30020: "TUNNEL_ER_AGENT_NOT_SUPPORT",
        -30021: "TUNNEL_ER_INVALID_ARG",
        -30022: "TUNNEL_ER_OS_RESOURCE_LACK",
        -30023: "TUNNEL_ER_AGENT_NOT_CONNECTING",
        -30024: "TUNNEL_ER_NO_FREE_SESSION",
        -30025: "TUNNEL_ER_CONNECTION_CANCELLED",
        -30026: "TUNNEL_ER_OPERATION_IS_INVALID",
        -30027: "TUNNEL_ER_HANDSHAKE_FAILED",
        -30028: "TUNNEL_ER_REMOTE_NOT_SUPPORT_DTLS",
        -30029: "TUNNEL_ER_LOCAL_NOT_SUPPORT_DTLS",
        -30030: "TUNNEL_ER_TIMEOUT",
        -31000: "TUNNEL_ER_UNDEFINED",
    }

    def __init__(self, code, data=None):
        super().__init__(code)
        self.code = code
        self.data = data

    @property
    def name(self):
        return TutkError.name_mapping.get(self.code, self.code)

    def __str__(self):
        return self.name or ""


def load_library(shared_lib_path: str | None = None) -> CDLL:
    """Load the underlying iotc library

    :param shared_lib_path: the path to the shared library libIOTCAPIs_ALL
    :return: the tutk_platform_lib, suitable for passing to other functions in this module
    """
    if not shared_lib_path:
        shared_lib_path = "/usr/local/lib/libIOTCAPIs_ALL.so"
    return cdll.LoadLibrary(shared_lib_path)


__all__ = [
    "BITRATE_360P",
    "BITRATE_SD",
    "BITRATE_HD",
    "BITRATE_SUPER_HD",
    "BITRATE_SUPER_SUPER_HD",
    "FRAME_SIZE_2K",
    "FRAME_SIZE_1080P",
    "FRAME_SIZE_360P",
    "FRAME_SIZE_DOORBELL_HD",
    "FRAME_SIZE_DOORBELL_SD",
    "IOTYPE_USER_DEFINED_START",
    "AV_ER_TIMEOUT",
    "AV_ER_SESSION_CLOSE_BY_REMOTE",
    "AV_ER_REMOTE_TIMEOUT_DISCONNECT",
    "AV_ER_DATA_NOREADY",
    "AV_ER_INCOMPLETE_FRAME",
    "AV_ER_LOSED_THIS_FRAME",
    "AV_ER_SENDIOCTRL_ALREADY_CALLED",
    "TutkError",
    "load_library",
    "project_root",
]
