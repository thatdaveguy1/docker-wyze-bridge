"""TUTK FFI function bindings -- ctypes calls into ``libIOTCAPIs_ALL.so``."""

import logging
from ctypes import (
    CDLL,
    POINTER,
    byref,
    c_char_p,
    c_int,
    c_int32,
    c_ubyte,
    c_uint,
    c_uint16,
    c_uint32,
    cast,
    create_string_buffer,
    sizeof,
)

from .tutk_structures import (
    AVClientStartInConfig,
    AVClientStartOutConfig,
    FrameInfo3Struct,
    FrameInfoStruct,
    LogAttr,
    SInfoStructEx,
    St_IOTCCheckDeviceInput,
    St_IOTCCheckDeviceOutput,
    St_IOTCConnectInput,
    get_frame_info,
)

logger = logging.getLogger(__name__)


def av_recv_frame_data(
    tutk_platform_lib: CDLL, av_chan_id: c_int
) -> tuple[
    int,
    bytes | None,
    FrameInfoStruct | FrameInfo3Struct | None,
    int | None,
]:
    """A new version AV client receives frame data from an AV server.

    An AV client uses this function to receive frame data from AV server

    :param tutk_platform_lib: the c library loaded from the 'load_library' call.
    :param av_chan_id: The channel ID of the AV channel to recv data on.
    :return: a 4-tuple of errno, frame_data, frame_info, and frame_index
    """
    frame_data_max_len = 800_000
    frame_info_max_len = 4096

    frame_data_buffer = create_string_buffer(frame_data_max_len)
    frame_info_buffer = create_string_buffer(frame_info_max_len)

    frame_data_actual_len = c_int32(0)
    frame_data_expected_len = c_int32(0)
    frame_info_actual_len = c_int32(0)
    frame_index = c_uint(0)

    logger.debug(f"[TUTK] Calling avRecvFrameData2 av_chan_id: {av_chan_id}")
    errno = tutk_platform_lib.avRecvFrameData2(
        av_chan_id,
        frame_data_buffer,
        c_int(frame_data_max_len),
        byref(frame_data_actual_len),
        byref(frame_data_expected_len),
        frame_info_buffer,
        c_int(frame_info_max_len),
        byref(frame_info_actual_len),
        byref(frame_index),
    )
    logger.debug(
        f"[TUTK] avRecvFrameData2 returned {errno=}, {frame_data_actual_len=}, {frame_data_expected_len=} {frame_info_actual_len=}, {frame_index=}"
    )

    if errno < 0:
        return errno, None, None, None

    video_data = memoryview(frame_data_buffer)[: frame_data_actual_len.value].tobytes()
    frame_info = get_frame_info(frame_info_buffer, frame_info_actual_len)

    logger.debug(f"[TUTK] Received video frame {frame_info=}")
    return 0, video_data, frame_info, frame_index.value


def av_recv_audio_data(tutk_platform_lib: CDLL, av_chan_id: c_int):
    """An AV client receives audio data from an AV server.

    An AV client uses this function to receive audio data from AV server

    :param tutk_platform_lib: the c library loaded from the 'load_library' call.
    :param av_chan_id: The channel ID of the AV channel to recv data on.
    :return: a 4-tuple of errno, audio_data, frame_info, and frame_index
    """
    audio_data_max_size = 51_200
    frame_info_max_size = 4096

    audio_data_buffer = create_string_buffer(audio_data_max_size)
    frame_info_buffer = create_string_buffer(frame_info_max_size)
    frame_index = c_uint32(0)

    logger.debug(f"[TUTK] Calling avRecvAudioData {av_chan_id=}")
    frame_len = tutk_platform_lib.avRecvAudioData(
        av_chan_id,
        audio_data_buffer,
        audio_data_max_size,
        frame_info_buffer,
        frame_info_max_size,
        byref(frame_index),
    )
    logger.debug(f"[TUTK] avRecvAudioData returned {frame_len=}, {frame_index=}")

    if frame_len < 0:
        return frame_len, None, None

    audio_data = memoryview(audio_data_buffer)[:frame_len].tobytes()
    frame_info = cast(frame_info_buffer, POINTER(FrameInfo3Struct)).contents
    logger.debug(f"[TUTK] Received audio frame {frame_info=}")
    return 0, audio_data, frame_info


def av_check_audio_buf(tutk_platform_lib: CDLL, av_chan_id: c_int) -> int:
    """Get the frame count of audio buffer remaining in the queue."""
    logger.debug(f"[TUTK] Calling avCheckAudioBuf {av_chan_id=}")
    result = tutk_platform_lib.avCheckAudioBuf(av_chan_id)
    logger.debug(f"[TUTK] avCheckAudioBuf returned {result=}")
    return result


def av_recv_io_ctrl(tutk_platform_lib: CDLL, av_chan_id: c_int, timeout_ms: int) -> tuple[int, int, bytes | None]:
    """Receive AV IO control.

    This function is used by AV servers or AV clients to receive a AV IO control.
    :param tutk_platform_lib: the c library loaded from the 'load_library' call.
    :param av_chan_id: The channel ID of the AV channel to be stopped
    :param timeout_ms: the number of milliseconds to wait before timing out
    :returns: a tuple of (the length of the io_ctrl received (or error number),
              the io_ctrl_type, and the data in bytes)
    """
    pn_io_ctrl_type = c_uint()
    ctl_data_len = 50_000
    ctl_buffer = create_string_buffer(ctl_data_len)

    logger.debug(f"[TUTK] Calling avRecvIOCTRL {av_chan_id=}")
    frame_len = tutk_platform_lib.avRecvIOCTRL(
        av_chan_id, byref(pn_io_ctrl_type), ctl_buffer, c_int(ctl_data_len), c_uint(timeout_ms)
    )
    logger.debug(f"[TUTK] avRecvIOCTRL returned {frame_len=}, {pn_io_ctrl_type=}")

    if frame_len < 0:
        return frame_len, 0, None

    data = memoryview(ctl_buffer)[:frame_len].tobytes()

    return frame_len, pn_io_ctrl_type.value, data


def av_client_set_max_buf_size(tutk_platform_lib: CDLL, size: c_int) -> None:
    """Set the maximum video frame buffer used in AV client.

    AV client sets the maximum video frame buffer by this function. The size of
    video frame buffer will affect the streaming fluency. The default size of
    video frame buffer is 1MB.
    :param tutk_platform_lib: the c library loaded from the 'load_library' call.
    :param size: The maximum video frame buffer, in unit of kilo-byte
    """
    logger.debug(f"[TUTK] Calling avClientSetMaxBufSize size: {size}")
    tutk_platform_lib.avClientSetMaxBufSize(size)


def av_client_set_recv_buf_size(tutk_platform_lib: CDLL, channel_id: c_int, size: c_uint) -> None:
    """Set the maximum frame buffer size used in AV client with specific AV channel ID.

    AV client sets the maximum video frame buffer by this function. The size of
    video frame buffer will affect the streaming fluency. The default size of
    video frame buffer is 1MB.

    :param tutk_platform_lib: the c library loaded from the 'load_library' call.
    :param channel_id: The channel ID of the AV channel to setup max buffer size
    :param size: The maximum video frame buffer, in unit of kilo-byte
    """
    logger.debug(f"[TUTK] Calling avClientSetRecvBufMaxSize {channel_id=}, {size=}")
    tutk_platform_lib.avClientSetRecvBufMaxSize(channel_id, size)


def av_client_clean_buf(tutk_platform_lib: CDLL, channel_id: c_int) -> None:
    """Clean the video buffer both in client and device, and clean the audio buffer of the client.

    A client with multiple device connection application should call
    this function to clean AV buffer while switch to another devices.

    :param tutk_platform_lib: the c library loaded from the 'load_library' call.
    :param channel_id: The channel ID of the AV channel to clean buffer
    """
    logger.debug(f"[TUTK] Calling avClientCleanBuf {channel_id=}")
    tutk_platform_lib.avClientCleanBuf(channel_id)


def av_client_clean_local_buf(tutk_platform_lib: CDLL, channel_id: c_int) -> None:
    """Clean the local video and audio buffer of the client.

    This function is used to clean the video and audio buffer that the client
    has already received

    :param channel_id: The channel ID of the AV channel to clean buffer
    """
    logger.debug(f"[TUTK] Calling avClientCleanLocalBuf {channel_id=}")
    tutk_platform_lib.avClientCleanLocalBuf(channel_id)


def av_client_clean_local_video_buf(tutk_platform_lib: CDLL, channel_id: c_int) -> None:
    """Clean the local video buffer of the client.

    This function is used to clean the video buffer that the client
    has already received

    :param channel_id: The channel ID of the AV channel to clean buffer
    """
    logger.debug(f"[TUTK] Calling avClientCleanLocalVideoBuf {channel_id=}")
    tutk_platform_lib.avClientCleanLocalVideoBuf(channel_id)


def av_client_clean_local_audio_buf(tutk_platform_lib: CDLL, channel_id: c_int) -> None:
    """Clean the local audio buffer of the client.

    This function is used to clean the audio buffer that the client
    has already received

    :param channel_id: The channel ID of the AV channel to clean buffer
    """
    logger.debug(f"[TUTK] Calling avClientCleanAudioBuf {channel_id=}")
    tutk_platform_lib.avClientCleanAudioBuf(channel_id)


def av_client_stop(tutk_platform_lib: CDLL, av_chan_id: c_int) -> None:
    """Stop an AV client.

    An AV client stop AV channel by this function if this channel is no longer
    required.

    :param tutk_platform_lib: the c library loaded from the 'load_library' call.
    :param av_chan_id: The channel ID of the AV channel to be stopped
    """
    logger.debug(f"[TUTK] Calling avClientStop {av_chan_id=}")
    tutk_platform_lib.avClientStop(av_chan_id)


def av_send_io_ctrl_exit(tutk_platform_lib: CDLL, av_chan_id: c_int) -> None:
    logger.debug(f"[TUTK] Calling avSendIOCtrlExit {av_chan_id=}")
    tutk_platform_lib.avSendIOCtrlExit(av_chan_id)


def av_send_io_ctrl(tutk_platform_lib: CDLL, av_chan_id: c_int, ctrl_type: c_uint, data: bytes | None) -> int:
    length = len(data) if data else 0
    cdata = c_char_p(data) if data else None

    logger.debug(f"[TUTK] Calling avSendIOCtrl {av_chan_id=}, {ctrl_type=}")
    result = tutk_platform_lib.avSendIOCtrl(av_chan_id, ctrl_type, cdata, length)
    logger.debug(f"[TUTK] avSendIOCtrl returnd {result=}")
    return result


def iotc_session_close(tutk_platform_lib: CDLL, session_id: c_int) -> None:
    """Used by a device or a client to close a IOTC session.

    A device or a client uses this function to close a IOTC session specified
    by its session ID if this IOTC session is no longer required.

    :param tutk_platform_lib: the c library loaded from the 'load_library' call.
    :param session_id: The session ID of the IOTC session to start AV client
    """
    logger.debug(f"[TUTK] Calling IOTC_Session_Close {session_id=}")
    result = tutk_platform_lib.IOTC_Session_Close(session_id)
    logger.debug(f"[TUTK] IOTC_Session_Close returned: {result=}")


def av_client_start(
    tutk_platform_lib: CDLL,
    session_id: c_int,
    username: bytes,
    password: bytes,
    timeout_secs: c_uint32,
    channel_id: c_ubyte,
    resend: c_int,
) -> c_int:  # tuple[c_int, c_uint]:
    """Start an AV client.

    Start an AV client by providing view account and password. It shall pass
    the authentication of the AV server before receiving AV data.

    :param tutk_platform_lib: the c library loaded from the 'load_library' call.
    :param session_id: The session ID of the IOTC session to start AV client
    :param username: The view account for authentication
    :param password: The view password for authentication
    :param timeout_secs: The timeout for this function in unit of second
                         Specify it as 0 will make this AV client try connection once
                         and this process will exit immediately if not connection
                         is unsuccessful.
    :param channel_id: The channel ID of the channel to start AV client
    :return: returns a tuple of two values:
             - av_chan_id: AV channel ID if return value >= 0; error code if return value < 0
             NO IT DOESN'T - pn_serv_type: The user-defined service type set when an AV server starts. Can be NULL.
    """
    avc_in = AVClientStartInConfig()
    avc_in.cb = sizeof(avc_in)
    avc_in.iotc_session_id = session_id
    avc_in.iotc_channel_id = channel_id
    avc_in.timeout_sec = timeout_secs
    avc_in.account_or_identity = username
    avc_in.password_or_token = password
    avc_in.resend = resend
    avc_in.security_mode = 2
    avc_in.auth_type = 0
    avc_in.sync_recv_data = 0

    avc_out = AVClientStartOutConfig()
    avc_out.cb = sizeof(avc_out)

    logger.debug(f"[TUTK] Calling avClientStartEx {avc_in=}")
    result = tutk_platform_lib.avClientStartEx(byref(avc_in), byref(avc_out))
    logger.debug(f"[TUTK] avClientStartEx returned {result=} {avc_out=}")
    return result


def av_initialize(tutk_platform_lib: CDLL, max_num_channels: c_int = c_int(1)) -> c_int:
    """Initialize AV module.

    This function is used by AV servers or AV clients to initialize AV module
    and shall be called before any AV module related function is invoked.

    :param tutk_platform_lib: the c library loaded from the 'load_library' call.
    :param max_num_channels: The max number of AV channels. If it is specified
                             less than 1, AV will set max number of AV channels as 1.

    :return:The actual maximum number of AV channels to be set. Error code if return value < 0.
    """
    logger.debug(f"[TUTK] Calling avInitialize {max_num_channels=}")
    max_chans: c_int = tutk_platform_lib.avInitialize(max_num_channels)
    logger.debug(f"[TUTK] avInitialize returned {max_chans=}")
    return max_chans


def av_deinitialize(tutk_platform_lib: CDLL) -> int:
    """Deinitialize AV module.

    This function will deinitialize AV module.

    :param tutk_platform_lib: The underlying c library (from tutk.load_library())
    :return: Error code if return value < 0
    """
    logger.debug("[TUTK] Calling avDeInitialize")
    errno: int = tutk_platform_lib.avDeInitialize()
    logger.debug(f"[TUTK] avDeInitialize returned {errno=}")
    return errno


def iotc_session_check(tutk_platform_lib: CDLL, session_id: c_int) -> tuple[int, SInfoStructEx]:
    """Used by a device or a client to check the IOTC session info.

    A device or a client may use this function to check if the IOTC session is
    still alive as well as getting the IOTC session info.

    :param tutk_platform_lib: The underlying c library (from tutk.load_library())
    :param session_id: The session ID of the IOTC session to be checked
    :return: The session info of specified IOTC session
    """
    sess_info = SInfoStructEx()
    sess_info.size = sizeof(sess_info)
    logger.debug(f"[TUTK] Calling IOTC_Session_Check_Ex {session_id=}")
    err_code = tutk_platform_lib.IOTC_Session_Check_Ex(session_id, byref(sess_info))
    logger.debug(f"[TUTK] IOTC_Session_Check_Ex returned {err_code=}, {sess_info=}")
    return err_code, sess_info


def iotc_connect_by_uid(tutk_platform_lib: CDLL, p2p_id: str) -> c_int:
    """Used by a client to connect a device.

    This function is for a client to connect a device by specifying the UID of
    that device. If connection is established with the help of IOTC servers,
    the IOTC session ID will be returned in this function and then device and
    client can communicate for the other later by using this IOTC session ID.

    :param tutk_platform_lib: The underlying c library (from tutk.load_library())
    :param p2p_id: The UID of a device that client wants to connect
    :return: IOTC session ID if return value >= 0, error code if return value < 0
    """
    logger.debug(f"[TUTK] Calling IOTC_Connect_ByUID {p2p_id=}")
    session_id: c_int = tutk_platform_lib.IOTC_Connect_ByUID(
        c_char_p(
            p2p_id.encode("ascii")
        )  # , p2p-id https://github.com/kroo/wyzecam/compare/main...mrlt8:wyzecam:dev#diff-4c514493ec78af5de7272e675354bb93bb97ebc59874a9ab6c3da641351dce38R786
    )
    logger.debug(f"[TUTK] IOTC_Connect_ByUID returned {session_id=}")
    return session_id


def iotc_get_session_id(tutk_platform_lib: CDLL) -> c_int:
    """Used by a client to get a tutk_platform_free session ID.

    This function is for a client to get a tutk_platform_free
    session ID used for a parameter of iotc_connect_by_uid_parallel()
    """
    logger.debug("[TUTK] Calling IOTC_Get_SessionID")
    session_id: c_int = tutk_platform_lib.IOTC_Get_SessionID()
    logger.debug(f"[TUTK] IOTC_Get_SessionID returned {session_id=}")
    return session_id


def iotc_check_device_online(
    tutk_platform_lib: CDLL,
    p2p_id: str,
    auth_key: bytes,
    timeout_ms: c_uint = c_uint(5000),
) -> tuple[c_int, St_IOTCCheckDeviceOutput]:
    """Checking device online or not."""
    device_in = St_IOTCCheckDeviceInput()
    device_in.cb = sizeof(device_in)
    device_in.auth_key = auth_key

    device_out = St_IOTCCheckDeviceOutput()

    logger.debug(f"[TUTK] Calling IOTC_Check_Device_OnlineEx {p2p_id=}, {device_in=}")
    status: c_int = tutk_platform_lib.IOTC_Check_Device_OnlineEx(
        c_char_p(p2p_id.encode("ascii")),
        byref(device_in),
        byref(device_out),
        timeout_ms,
        c_int32(),
    )
    logger.debug(f"[TUTK] IOTC_Check_Device_OnlineEx returned {status=}, {device_out=}")
    return status, device_out


def iotc_connect_by_uid_parallel(tutk_platform_lib: CDLL, p2p_id: str, session_id: c_int) -> c_int:
    """Used by a client to connect a device and bind to a specified session ID.

    This function is for a client to connect a device by specifying the UID of that device,
    and bind to a tutk_platform_free session ID from IOTC_Get_SessionID(). If connection is
    established with the help of IOTC servers, the IOTC_ER_NoERROR will be returned in this
    function and then device and client can communicate for the other later by using this
    IOTC session ID. If this function is called by multiple threads, the connections will
    be processed concurrently.

    :param tutk_platform_lib: The underlying c library (from tutk.load_library())
    :param p2p_id: The UID of a device that client wants to connect
    :param session_id: The Session ID got from IOTC_Get_SessionID() the connection should bind to.
    :return: IOTC session ID if return value >= 0, error code if return value < 0
    """
    logger.debug(f"[TUTK] Calling IOTC_Connect_ByUID_Parallel {p2p_id=}, {session_id=}")
    resultant_session_id: c_int = tutk_platform_lib.IOTC_Connect_ByUID_Parallel(
        c_char_p(p2p_id.encode("ascii")), session_id
    )
    logger.debug(f"[TUTK] IOTC_Check_Device_OnlineEx returned {resultant_session_id=}")
    return resultant_session_id


def iotc_connect_by_uid_ex(
    tutk_platform_lib: CDLL,
    p2p_id: str,
    session_id: c_int,
    auth_key: str,
    timeout: int = 60,
) -> c_int:
    """Used by a client to connect a device.

    This function is for a client to connect a device by specifying
    the UID and password of that device. If connection is established with the
    help of IOTC servers, the IOTC session ID will be returned in this
    function and then device and client can communicate for the other
    later by using this IOTC session ID.This function will wake up device if it's sleeping.

    :param tutk_platform_lib: The underlying c library (from tutk.load_library())
    :param p2p_id: The UID of a device that client wants to connect
    :param session_id: The Session ID got from IOTC_Get_SessionID() the connection should bind to.
    :return: IOTC session ID if return value >= 0, error code if return value < 0
    """
    connect_input = St_IOTCConnectInput()
    connect_input.cb = sizeof(connect_input)
    connect_input.authenticationType = 0
    connect_input.auth_key = auth_key.encode()
    connect_input.timeout = timeout

    logger.debug(f"[TUTK] Calling IOTC_Connect_ByUIDEx {p2p_id=}, {session_id=}, {connect_input=}")
    result = tutk_platform_lib.IOTC_Connect_ByUIDEx(c_char_p(p2p_id.encode("ascii")), session_id, byref(connect_input))
    logger.debug(f"[TUTK] IOTC_Connect_ByUIDEx returned {result=}, {connect_input=}")
    return result


def iotc_connect_stop_by_session_id(tutk_platform_lib: CDLL, session_id: c_int) -> c_int:
    """
    Used by a client to stop a specific session connecting a device.

    This function is for a client to stop connecting a device. Since IOTC_Connect_ByUID_Parallel()
     is a block processes, that means the client will have to wait for the return of these functions
     before executing sequential instructions. In some cases, users may want the client to stop
     connecting immediately by this function in another thread before the return of connection process.

    :param tutk_platform_lib: The underlying c library (from tutk.load_library())
    :param session_id: The Session ID got from IOTC_Get_SessionID() the connection should bind to.
    :return: Error code if return value < 0, otherwise 0 if successful
    """
    logger.debug(f"[TUTK] Calling IOTC_Connect_Stop_BySID {session_id=}")
    errno: c_int = tutk_platform_lib.IOTC_Connect_Stop_BySID(session_id)
    logger.debug(f"[TUTK] IOTC_Connect_Stop_BySID returned {errno=}")
    return errno


def iotc_set_log_attr(
    tutk_platform_lib: CDLL,
    path: str,
    log_level: c_int = c_int(0),
    max_size: c_int = c_int(0),
    max_count: c_int = c_int(0),
) -> int:
    """
    Set Attribute of log file

    :param path: The path of log file, NULL = disable Log
    :param log_level: The log message with level log_level or higher would be logged, LEVEL_SILENCE = nothing would be logged
    :param file_max_size: The maximum size of log file in bytes, 0 = unlimited
    :param file_max_count: The maximum number of log file if file_max_size is set, 0 = unlimited
    """
    log_attr = LogAttr()
    log_attr.path = path.encode("ascii")
    log_attr.log_level = log_level
    log_attr.file_max_size = max_size
    log_attr.file_max_count = max_count

    logger.debug(f"[TUTK] Calling IOTC_Set_Log_Attr {log_attr=}")
    errno: int = tutk_platform_lib.IOTC_Set_Log_Attr(byref(log_attr))
    logger.debug(f"[TUTK] IOTC_Set_Log_Attr returned {errno=}")
    return errno


def iotc_get_version(tutk_platform_lib: CDLL) -> int:
    """Get the version of IOTC module.

    This function returns the version of IOTC module.
    """
    logger.debug("[TUTK] Calling IOTC_Get_Version_String")
    result = tutk_platform_lib.IOTC_Get_Version_String()
    logger.debug(f"[TUTK] IOTC_Get_Version_String returned {result=}")
    return result


def iotc_initialize(tutk_platform_lib: CDLL, udp_port: c_uint16 = c_uint16(0)) -> int:
    """Initialize IOTC module.

    This function is used by devices or clients to initialize IOTC module and
    shall be called before any IOTC module related function is invoked except
    for IOTC_Set_Max_Session_Number().

    The different between this function and IOTC_Initialize() is this function
    uses following steps to connect masters (1) IP addresses of master (2) if
    fails to connect in step 1, resolve predefined domain name of masters (3)
    try to connect again with the resolved IP address of step 2 if IP is
    resolved successfully.

    :param tutk_platform_lib: The underlying c library (from tutk.load_library())
    :param udp_port: Specify a UDP port. Random UDP port is used if it is specified as 0.
    :return: 0 if successful, Error code if return value < 0
    """
    logger.debug(f"[TUTK] Calling IOTC_Initialize2 {udp_port=}")
    errno: int = tutk_platform_lib.IOTC_Initialize2(udp_port)
    logger.debug(f"[TUTK] IOTC_Initialize2 returned {errno=}")
    return errno


def TUTK_SDK_Set_License_Key(tutk_platform_lib: CDLL, key: str) -> int:
    logger.debug(f"[TUTK] Calling TUTK_SDK_Set_License_Key {key=}")
    errno: int = tutk_platform_lib.TUTK_SDK_Set_License_Key(c_char_p(key.encode("ascii")))
    logger.debug(f"[TUTK] TUTK_SDK_Set_License_Key returned {errno=}")
    return errno


def iotc_deinitialize(tutk_platform_lib: CDLL) -> c_int:
    """Deinitialize IOTC module.

    This function will deinitialize IOTC module.

    :param tutk_platform_lib: The underlying c library (from tutk.load_library())
    :return: Error code if return value < 0
    """
    logger.debug("[TUTK] Calling IOTC_DeInitialize")
    errno: c_int = tutk_platform_lib.IOTC_DeInitialize()
    logger.debug(f"[TUTK] IOTC_DeInitialize returned {errno=}")
    return errno


__all__ = [
    "av_recv_frame_data",
    "av_recv_audio_data",
    "av_check_audio_buf",
    "av_recv_io_ctrl",
    "av_client_set_max_buf_size",
    "av_client_set_recv_buf_size",
    "av_client_clean_buf",
    "av_client_clean_local_buf",
    "av_client_clean_local_video_buf",
    "av_client_clean_local_audio_buf",
    "av_client_stop",
    "av_send_io_ctrl_exit",
    "av_send_io_ctrl",
    "iotc_session_close",
    "av_client_start",
    "av_initialize",
    "av_deinitialize",
    "iotc_session_check",
    "iotc_connect_by_uid",
    "iotc_get_session_id",
    "iotc_check_device_online",
    "iotc_connect_by_uid_parallel",
    "iotc_connect_by_uid_ex",
    "iotc_connect_stop_by_session_id",
    "iotc_set_log_attr",
    "iotc_get_version",
    "iotc_initialize",
    "TUTK_SDK_Set_License_Key",
    "iotc_deinitialize",
]
