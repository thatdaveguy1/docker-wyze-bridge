import json
import time
from struct import iter_unpack, pack, unpack

from .protocol_core import TutkWyzeProtocolMessage, encode


class K10290GetMotionTagging(TutkWyzeProtocolMessage):
    """
    A message used to check if motion tagging (green box around motion) is enabled.

    :return: returns the current motion tagging status:
        - 1: Enabled
        - 2: Disabled
    """

    def __init__(self):
        super().__init__(10290)


class K10200GetMotionAlarm(TutkWyzeProtocolMessage):
    def __init__(self):
        super().__init__(10200)

    def parse_response(self, resp_data):
        enabled, sensitivity = unpack("<BB", resp_data)
        return enabled


class K10202SetMotionAlarm(TutkWyzeProtocolMessage):
    def __init__(self, value: int):
        super().__init__(10202)
        assert value in {1, 2}, "value must be 1 or 2"
        self.value: int = value

    def encode(self) -> bytes:
        return encode(self.code, bytes([self.value, 0]))


class K10206SetMotionAlarm(TutkWyzeProtocolMessage):
    def __init__(self, value: int):
        super().__init__(10206)
        assert value in {1, 2}, "value must be 1 or 2"
        self.value: int = value

    def encode(self) -> bytes:
        return encode(self.code, bytes([self.value, 0]))


class K10292SetMotionTagging(TutkWyzeProtocolMessage):
    """
    A message used to enable/disable motion tagging (green box around motion).

    Parameters:
    -  value (int): 1 for on; 2 for off.
    """

    def __init__(self, value: int):
        super().__init__(10292)

        assert 0 <= value <= 2, "value must be 1 or 2"
        self.value: int = value

    def encode(self) -> bytes:
        return encode(self.code, bytes([self.value]))


class K10302SetTimeZone(TutkWyzeProtocolMessage):
    """
    A message used to set the time zone on the camera.

    Parameters:
    -  value (int): the time zone to set (-11 to 13).
    """

    def __init__(self, value: int):
        super().__init__(10302)
        assert -11 <= value <= 13, "value must be -11 to 13"
        self.value: int = value

    def encode(self) -> bytes:
        return encode(self.code, pack("<b", self.value))


class K10620CheckNight(TutkWyzeProtocolMessage):
    """
    A message used to check the night mode settings of the camera.

    Not terribly well understood.
    """

    def __init__(self):
        super().__init__(10620)


class K10624GetAutoSwitchNightType(TutkWyzeProtocolMessage):
    """
    A message used to geet the night vision conditions settings on the camera.

    :return: returns conditions required for night vision:
        - 1: Dusk. Switch on night vision when the environment has low light.
        - 2: Dark. Switch on night vision when the environment has extremely low light.
    """

    def __init__(self):
        super().__init__(10624)


class K10626SetAutoSwitchNightType(TutkWyzeProtocolMessage):
    """
    A message used to set the night vision conditions settings on the camera.

    :param type: the type of condition to use:
        - 1: Dusk. Switch on night vision when the environment has low light.
        - 2: Dark. Switch on night vision when the environment has extremely low light.
    """

    def __init__(self, type: int):
        super().__init__(10626)
        self.type: int = type

    def encode(self) -> bytes:
        return encode(self.code, bytes([self.type]))


class K10630SetAlarmFlashing(TutkWyzeProtocolMessage):
    """
    A message used to control the alarm AND siren on the camera.

    Parameters:
    -  value (int):  1 to turn on alarm and siren; 2 to turn off alarm and siren.
    """

    def __init__(self, value: int):
        super().__init__(10630)
        assert 0 <= value <= 2, "value must be 1 or 2"
        self.value: int = value

    def encode(self) -> bytes:
        return encode(self.code, bytes([self.value, self.value]))


class K10632GetAlarmFlashing(TutkWyzeProtocolMessage):
    """
    A message used to get the alarm/siren status on the camera.

    :return enabled: returns a tuple of the current alarm status on the camera:
        - (1,1): On.
        - (2,2): Off.
    """

    def __init__(self):
        super().__init__(10632)


class K10640GetSpotlightStatus(TutkWyzeProtocolMessage):
    """
    A message used to check the spotlight settings of the camera.

    Not terribly well understood.
    """

    def __init__(self):
        super().__init__(10640)


class K10058TakePhoto(TutkWyzeProtocolMessage):
    """
    Take photo on camera sensor and save to /media/mmc/photo/YYYYMMDD/YYYYMMDD_HH_MM_SS.jpg
    """

    def __init__(self):
        super().__init__(10058)

    def encode(self) -> bytes:
        return encode(self.code, bytes([1]))


class K10148StartBoa(TutkWyzeProtocolMessage):
    """
    Temporarily start boa server
    """

    def __init__(self):
        super().__init__(10148)

    def encode(self) -> bytes:
        return encode(self.code, bytes([0, 1, 0, 0, 0]))


class K10242FormatSDCard(TutkWyzeProtocolMessage):
    """
    Format SD Card.

    Parameters:
    -  value (int): 1 to confirm format.
    """

    def __init__(self, value: int = 0):
        super().__init__(10242)
        assert value == 1, "value must be 1 to confirm format!"


class K10444SetDeviceState(TutkWyzeProtocolMessage):
    """
    Set outdoor cam wake status?

    Parameters:
    -  value (int): 1 = on; 2 = off. Defaults to on.
    """

    def __init__(self, value: int = 1):
        super().__init__(10444)
        assert 0 <= value <= 2, "value must be 1 or 2"
        self.value: int = value

    def encode(self) -> bytes:
        return encode(self.code, bytes([self.value]))


class K10446CheckConnStatus(TutkWyzeProtocolMessage):
    """
    Get connection status on outdoor cam?

    Returns:
    - json: connection status.
    """

    def __init__(self):
        super().__init__(10446)

    def parse_response(self, resp_data):
        return json.loads(resp_data)


class K10448GetBatteryUsage(TutkWyzeProtocolMessage):
    """
    Get battery usage on outdoor cam?

    Returns:
    - json: battery usage.
    """

    def __init__(self):
        super().__init__(10448)

    def parse_response(self, resp_data):
        data = json.loads(resp_data)
        return {
            "last_charge": data["0"],
            "live_streaming": data["1"],
            "events_uploaded": data["2"],
            "events_filtered": data["3"],
            "sd_recordings": data["4"],
            "5": data["5"],
        }


class K10600SetRtspSwitch(TutkWyzeProtocolMessage):
    """
    Set switch value for RTSP server on camera.

    Parameters:
    -  value (int): 1 for on; 2 for off. Defaults to True.
    """

    def __init__(self, value: int = 1):
        super().__init__(10600)
        assert 1 <= value <= 2, "value must be 1 or 2"
        self.value: int = value

    def encode(self) -> bytes:
        return encode(self.code, bytes([self.value]))


class K10604GetRtspParam(TutkWyzeProtocolMessage):
    """
    Get RTSP parameters from supported firmware.
    """

    def __init__(self):
        super().__init__(10604)


class K11000SetRotaryByDegree(TutkWyzeProtocolMessage):
    """
    Rotate by horizontal and vertical degree?

    Speed seems to be a constant 5.

    Parameters:
    - horizontal (int): horizontal position in degrees?
    - vertical (int): vertical position in degrees?
    - speed (int, optional): rotation speed. seems to default to 5.

    """

    def __init__(self, horizontal: int, vertical: int = 0, speed: int = 5):
        super().__init__(11000)
        self.horizontal = horizontal
        self.vertical = vertical
        self.speed = speed if 1 < speed < 9 else 5

    def encode(self) -> bytes:
        msg = pack("<hhB", self.horizontal, self.vertical, self.speed)
        return encode(self.code, msg)


class K11002SetRotaryByAction(TutkWyzeProtocolMessage):
    """
    Rotate by action.

    Speed seems to be a constant 5.

    Parameters:
    - horizontal (int): 1 for left; 2 for right
    - vertical (int): 1 for up; 2 for down
    - speed (int, optional): rotation speed. seems to default to 5.

    Example:
    - Rotate left: K11002SetRotaryByAction(1,0)
    - Rotate right: K11002SetRotaryByAction(2,0)
    - Rotate up: K11002SetRotaryByAction(0,1)
    - Rotate down: K11002SetRotaryByAction(0,2)

    """

    def __init__(self, horizontal: int, vertical: int, speed: int = 5):
        super().__init__(11002)
        self.horizontal = horizontal if 0 <= horizontal <= 2 else 0
        self.vertical = vertical if 0 <= vertical <= 2 else 0
        self.speed = speed if 1 <= speed <= 9 else 5

    def encode(self) -> bytes:
        return encode(self.code, bytes([self.horizontal, self.vertical, self.speed]))


class K11004ResetRotatePosition(TutkWyzeProtocolMessage):
    """
    Reset Rotation.

    Parameters:
    - position (int,optional): Reset position? Defaults to 3
    """

    def __init__(self, position: int = 3):
        super().__init__(11004)
        self.position = position

    def encode(self) -> bytes:
        return encode(self.code, bytes([self.position]))


class K11006GetCurCruisePoint(TutkWyzeProtocolMessage):
    """
    Get current PTZ.

    Returns:
    - dict: current PTZ:
        - vertical (int): vertical angle.
        - horizontal (int): horizontal angle.
        - time (int): wait time in seconds.
        - blank (int): isBlankPst?.
    """

    def __init__(self):
        super().__init__(11006)

    def encode(self) -> bytes:
        return encode(self.code, pack("<I", int(time.time())))

    def parse_response(self, resp_data: bytes):
        data = unpack("<IBH", resp_data)
        return {
            "vertical": data[1],
            "horizontal": data[2],
            "time": data[3],
            "blank": data[4],
        }


class K11010GetCruisePoints(TutkWyzeProtocolMessage):
    """
    Get cruise points.

    Returns:
    - list[dict]: list of cruise points as a dictionary:
        - vertical (int): vertical angle.
        - horizontal (int): horizontal angle.
        - time (int): wait time in seconds.
        - blank (int): isBlankPst?.
    """

    def __init__(self):
        super().__init__(11010)

    def parse_response(self, resp_data: bytes):
        return [
            {
                "vertical": data[0],
                "horizontal": data[1],
                "time": data[2],
                "blank": [3],
            }
            for data in iter_unpack("<BHB", resp_data[1:])
        ]


class K11012SetCruisePoints(TutkWyzeProtocolMessage):
    """
    Set cruise points.

    Parameters:
    -  points (list[dict]): list of cruise points as a dictionary:
            - vertical (int[0-40], optional): vertical angle.
            - horizontal (int[0-350], optional): horizontal angle.
            - time (int, optional[10-255]): wait time in seconds. Defaults to 10.
    - wait_time(int, optional): Default wait time. Defaults to 10.
    """

    def __init__(self, points: list[dict], wait_time=10):
        super().__init__(11012)

        self.points = bytearray(pack("<B", len(points)))
        for point in points:
            vertical = int(point.get("vertical", 0))
            horizontal = int(point.get("horizontal", 0))
            time = int(point.get("time", wait_time))
            blank = (int(point.get("blank", 0)),)
            self.points.extend(pack("<BHB", vertical, horizontal, time, blank))

    def encode(self) -> bytes:
        return encode(self.code, self.points)


class K11014GetCruise(TutkWyzeProtocolMessage):
    """
    Get switch value for Pan Scan, aka Cruise.

    :return: returns the current cruise status:
        - 1: On
        - 2: Off
    """

    def __init__(self):
        super().__init__(11014)


class K11016SetCruise(TutkWyzeProtocolMessage):
    """
    Set switch value for Pan Scan, aka Cruise.

    Parameters:
    -  value (int): 1 for on; 2 for off. Defaults to On.
    """

    def __init__(self, value: int):
        super().__init__(11016)

        assert 0 <= value <= 2, "value must be 1 or 2"
        self.value: int = value

    def encode(self) -> bytes:
        return encode(self.code, bytes([self.value]))


class K11018SetPTZPosition(TutkWyzeProtocolMessage):
    """
    Set PTZ Position.

    Parameters:
    - vertical (int[0-40], optional): vertical angle.
    - horizontal (int[0-350], optional): horizontal angle.
    """

    def __init__(self, vertical: int = 0, horizontal: int = 0):
        super().__init__(11018)
        self.vertical = vertical
        self.horizontal = horizontal

    def encode(self) -> bytes:
        time_val = int(time.time() * 1000) % 1_000_000_000
        return encode(self.code, pack("<IBH", time_val, self.vertical, self.horizontal))


class K11020GetMotionTracking(TutkWyzeProtocolMessage):
    """
    A message used to check if motion tracking is enabled (camera pans
    to follow detected motion).

    :return: returns the current motion tracking status:
        - 1: Enabled
        - 2: Disabled
    """

    def __init__(self):
        super().__init__(11020)


class K11022SetMotionTracking(TutkWyzeProtocolMessage):
    """
    A message used to enable/disable motion tracking (camera pans
    to follow detected motion).

    Parameters:
    -  value (int): 1 for on; 2 for off.
    """

    def __init__(self, value: int):
        super().__init__(11022)

        assert 0 <= value <= 2, "value must be 1 or 2"
        self.value: int = value

    def encode(self) -> bytes:
        return encode(self.code, bytes([self.value]))


class K11635ResponseQuickMessage(TutkWyzeProtocolMessage):
    """
    A message used to send a quick response to the camera.

    Parameters:
    -  value (int):
        - 1: db_response_1 (Can I help you?)
        - 2: db_response_2 (Be there shortly)
        - 3: db_response_3 (Leave package at door)
    """

    def __init__(self, value: int):
        super().__init__(11635)

        assert 1 <= value <= 3, "value must be 1, 2 or 3"
        self.value: int = value

    def encode(self) -> bytes:
        return encode(self.code, bytes([self.value]))


class K10646SetSpotlightStatus(TutkWyzeProtocolMessage):
    """
    A message used to set the spotlight (WYZEC3L) status.

    Args:
    - value (int): 1 for on; 2 for off.
    """

    def __init__(self, value):
        super().__init__(10646)

        assert 1 <= value <= 2, "value must be 1 or 2"
        self.value: int = value

    def encode(self) -> bytes:
        return encode(self.code, bytes([self.value]))


class K10720GetAccessoriesInfo(TutkWyzeProtocolMessage):
    """
    A message used to get the accessories info.
    """

    def __init__(self):
        super().__init__(10720)

    def parse_response(self, resp_data):
        return json.loads(resp_data)


class K10788GetIntegratedFloodlightInfo(TutkWyzeProtocolMessage):
    """
    A message used to get the integrated floodlight info.
    """

    def __init__(self):
        super().__init__(10788)


class K10820GetWhiteLightInfo(TutkWyzeProtocolMessage):
    """
    A message used to get the white light info.
    """

    def __init__(self):
        super().__init__(10820)


class K12060SetFloodLightSwitch(TutkWyzeProtocolMessage):
    """
    A message used to set the flood light switch.
    """

    def __init__(self, value):
        super().__init__(12060)

        assert 1 <= value <= 2, "value must be 1 or 2"
        self.value: int = value

    def encode(self) -> bytes:
        return encode(self.code, bytes([self.value]))
