"""
Tutk Wyze protocol message definitions and handshake logic.

This module is a backward-compatible facade re-exporting the protocol
implementation that has been split into themed sub-modules:

- :mod:`protocol_core` — base classes (``TutkWyzeProtocolError``,
  ``TutkWyzeProtocolHeader``, ``TutkWyzeProtocolMessage``), ``encode``/
  ``decode`` helpers, ``supports``, ``generate_challenge_response`` and
  shared state (``device_config``, ``logger``, ``STATUS_MESSAGES``).
- :mod:`protocol_messages` — connection / authentication / camera-setting
  message classes (K10000-K10092).
- :mod:`protocol_messages_ptz` — motion / night-vision / PTZ / accessory
  message classes (K10290-K12060).

The auth-handshake entry point ``respond_to_ioctrl_10001`` remains here so
that test patches on ``wyzecam.tutk.tutk_protocol.supports`` and
``wyzecam.tutk.tutk_protocol.generate_challenge_response`` continue to work.
"""

from struct import unpack
from typing import Optional

from wyzecam.api_models import DOORBELL

from .protocol_core import (
    STATUS_MESSAGES,
    TutkWyzeProtocolError,
    TutkWyzeProtocolHeader,
    TutkWyzeProtocolMessage,
    decode,
    device_config,
    encode,
    generate_challenge_response,
    logger,
    supports,
)
from .protocol_messages import (
    K10000ConnectRequest,
    K10002ConnectAuth,
    K10006ConnectUserAuth,
    K10008ConnectUserAuth,
    K10010ControlChannel,
    K10020CheckCameraInfo,
    K10020CheckCameraParams,
    K10030GetNetworkLightStatus,
    K10032SetNetworkLightStatus,
    K10040GetNightVisionStatus,
    K10042SetNightVisionStatus,
    K10044GetIRLEDStatus,
    K10046SetIRLEDStatus,
    K10050GetVideoParam,
    K10052DBSetResolvingBit,
    K10052HorizontalFlip,
    K10052SetBitrate,
    K10052SetFPS,
    K10052VerticalFlip,
    K10056SetResolvingBit,
    K10070GetOSDStatus,
    K10072SetOSDStatus,
    K10074GetOSDLogoStatus,
    K10076SetOSDLogoStatus,
    K10090GetCameraTime,
    K10092SetCameraTime,
)
from .protocol_messages_ptz import (
    K10058TakePhoto,
    K10148StartBoa,
    K10200GetMotionAlarm,
    K10202SetMotionAlarm,
    K10206SetMotionAlarm,
    K10242FormatSDCard,
    K10290GetMotionTagging,
    K10292SetMotionTagging,
    K10302SetTimeZone,
    K10444SetDeviceState,
    K10446CheckConnStatus,
    K10448GetBatteryUsage,
    K10600SetRtspSwitch,
    K10604GetRtspParam,
    K10620CheckNight,
    K10624GetAutoSwitchNightType,
    K10626SetAutoSwitchNightType,
    K10630SetAlarmFlashing,
    K10632GetAlarmFlashing,
    K10640GetSpotlightStatus,
    K10646SetSpotlightStatus,
    K10720GetAccessoriesInfo,
    K10788GetIntegratedFloodlightInfo,
    K10820GetWhiteLightInfo,
    K11000SetRotaryByDegree,
    K11002SetRotaryByAction,
    K11004ResetRotatePosition,
    K11006GetCurCruisePoint,
    K11010GetCruisePoints,
    K11012SetCruisePoints,
    K11014GetCruise,
    K11016SetCruise,
    K11018SetPTZPosition,
    K11020GetMotionTracking,
    K11022SetMotionTracking,
    K11635ResponseQuickMessage,
    K12060SetFloodLightSwitch,
)

__all__ = [
    "STATUS_MESSAGES",
    "TutkWyzeProtocolError",
    "TutkWyzeProtocolHeader",
    "TutkWyzeProtocolMessage",
    "decode",
    "device_config",
    "encode",
    "generate_challenge_response",
    "logger",
    "supports",
    "respond_to_ioctrl_10001",
    # protocol_messages
    "K10000ConnectRequest",
    "K10002ConnectAuth",
    "K10006ConnectUserAuth",
    "K10008ConnectUserAuth",
    "K10010ControlChannel",
    "K10020CheckCameraInfo",
    "K10020CheckCameraParams",
    "K10030GetNetworkLightStatus",
    "K10032SetNetworkLightStatus",
    "K10040GetNightVisionStatus",
    "K10042SetNightVisionStatus",
    "K10044GetIRLEDStatus",
    "K10046SetIRLEDStatus",
    "K10050GetVideoParam",
    "K10052DBSetResolvingBit",
    "K10052HorizontalFlip",
    "K10052SetBitrate",
    "K10052SetFPS",
    "K10052VerticalFlip",
    "K10056SetResolvingBit",
    "K10070GetOSDStatus",
    "K10072SetOSDStatus",
    "K10074GetOSDLogoStatus",
    "K10076SetOSDLogoStatus",
    "K10090GetCameraTime",
    "K10092SetCameraTime",
    # protocol_messages_ptz
    "K10058TakePhoto",
    "K10148StartBoa",
    "K10200GetMotionAlarm",
    "K10202SetMotionAlarm",
    "K10206SetMotionAlarm",
    "K10242FormatSDCard",
    "K10290GetMotionTagging",
    "K10292SetMotionTagging",
    "K10302SetTimeZone",
    "K10444SetDeviceState",
    "K10446CheckConnStatus",
    "K10448GetBatteryUsage",
    "K10600SetRtspSwitch",
    "K10604GetRtspParam",
    "K10620CheckNight",
    "K10624GetAutoSwitchNightType",
    "K10626SetAutoSwitchNightType",
    "K10630SetAlarmFlashing",
    "K10632GetAlarmFlashing",
    "K10640GetSpotlightStatus",
    "K10646SetSpotlightStatus",
    "K10720GetAccessoriesInfo",
    "K10788GetIntegratedFloodlightInfo",
    "K10820GetWhiteLightInfo",
    "K11000SetRotaryByDegree",
    "K11002SetRotaryByAction",
    "K11004ResetRotatePosition",
    "K11006GetCurCruisePoint",
    "K11010GetCruisePoints",
    "K11012SetCruisePoints",
    "K11014GetCruise",
    "K11016SetCruise",
    "K11018SetPTZPosition",
    "K11020GetMotionTracking",
    "K11022SetMotionTracking",
    "K11635ResponseQuickMessage",
    "K12060SetFloodLightSwitch",
]


def respond_to_ioctrl_10001(
    data: bytes,
    protocol: int,
    enr: str,
    product_model: str,
    mac: str,
    phone_id: str,
    open_userid: str,
    audio: bool = False,
) -> Optional[TutkWyzeProtocolMessage]:
    camera_status, camera_enr_b = unpack("<B16s", data[:17])

    logger.info(
        f"[TUTKP] 10001 challenge: model={product_model} protocol={protocol} camera_status={camera_status}"
    )

    if camera_status in STATUS_MESSAGES:
        logger.warning(
            f"[TUTKP] Camera is {STATUS_MESSAGES[camera_status]}, can't auth."
        )
        return

    if camera_status not in {1, 3, 6}:
        logger.warning(
            f"[TUTKP] Unexpected mode for connect challenge response (10001): {camera_status=}"
        )
        return

    resp = generate_challenge_response(camera_enr_b, enr, camera_status)

    supports_10006 = supports(product_model, protocol, 10006)
    supports_10008 = supports(product_model, protocol, 10008)
    logger.info(
        f"[TUTKP] 10001 capabilities: model={product_model} supports_10006={supports_10006} supports_10008={supports_10008}"
    )

    if product_model in DOORBELL and supports_10006:
        response = K10006ConnectUserAuth(resp, phone_id, open_userid, audio=audio)
    elif (
        product_model != "WYZEDB3" and supports_10008
    ):  # https://github.com/kroo/wyzecam/compare/v1.1.0...v1.2.0#diff-683cfded8e7a6b1c96f1110685f6e004c086b006efd0f5e43bc9416dafe2325eR494
        response = K10008ConnectUserAuth(resp, phone_id, open_userid, audio=audio)
    else:
        response = K10002ConnectAuth(resp, mac, audio=audio)

    logger.debug(f"[TUTKP] Sending response: {response}")
    return response
