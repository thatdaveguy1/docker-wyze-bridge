import json
import logging
from ctypes import LittleEndianStructure, c_char, c_uint16, c_uint32
from os import getenv
from pathlib import Path
from struct import pack
from typing import Any

import xxtea

from . import tutk

PROJECT_ROOT = Path(getenv("TUTK_PROJECT_ROOT", Path(__file__).parent))

logger = logging.getLogger(__name__)

with open(PROJECT_ROOT / "device_config.json") as f:
    device_config = json.load(f)


class TutkWyzeProtocolError(tutk.TutkError):
    pass


class TutkWyzeProtocolHeader(LittleEndianStructure):
    """
    Struct representing the first 16 bytes of messages sent back and forth between the camera
    and a client over a [TutkIOCtrlMux][wyzecam.tutk.tutk_ioctl_mux.TutkIOCtrlMux].

    :var prefix: the first two bytes of the header, always `HL`.
    :vartype prefix: str
    :var protocol: the protocol version being spoken by the client or camera. This varies quite a bit
                   depending on the firmware version of the camera.
    :vartype protocol: int
    :var code: The 2-byte "command" being issued, either by the camera, or the client.  By convention,
               it appears commands sent from a client to the camera are even numbered 'codes', whereas
               responses from the camera back to the client are always odd.
    :vartype code: int
    :var txt_len: the length of the payload of the message, i.e. the contents just after this header
    :vartype txt_len: int
    """

    _pack_ = 1
    _fields_ = [
        ("prefix", c_char * 2),  # 0:2
        ("protocol", c_uint16),  # 2:4
        ("code", c_uint16),  # 4:6
        ("txt_len", c_uint32),  # 6:10
        ("reserved2", c_uint16),  # 10:12
        ("reserved3", c_uint32),  # 12:16
    ]

    def __repr__(self):
        classname = self.__class__.__name__
        return f"<{classname} prefix={self.prefix} protocol={self.protocol} code={self.code} txt_len={self.txt_len}>"


class TutkWyzeProtocolMessage:
    """
    An abstract class representing a command sent from the client to
    the camera.  Subclasses implement particular codes.

    :var code: the 2 digit code representing this message
    :vartype code: int
    :var expected_response_code: the code of the message expected to
                                 be the 'response' to this one, from
                                 the camera and is always code + 1
    :vartype expected_response_code: int
    """

    def __init__(self, code: int) -> None:
        """Construct a new TutkWyzeProtocolMessage

        :param code: The 2-byte "command" being issued, either by the camera, or the client.  By convention,
                   it appears commands sent from a client to the camera are even numbered 'codes', whereas
                   responses from the camera back to the client are always odd.
        """
        self.code = code
        self.expected_response_code = code + 1

    def encode(self) -> bytes:
        """
        Translates this protocol message into a series of bytes,
        including the appropriate
        [16 byte header][wyzecam.tutk.tutk_protocol.TutkWyzeProtocolHeader].
        """
        return encode(self.code, None)

    def parse_response(self, resp_data: bytes) -> Any:
        """
        Called by [TutkIOCtrlMux][wyzecam.tutk.tutk_ioctl_mux.TutkIOCtrlMux] upon receipt
        of the corresponding
        [expected_response_code][wyzecam.tutk.tutk_protocol.TutkWyzeProtocolMessage]
        of this message.
        """
        return resp_data

    def __repr__(self):
        return f"<{self.__class__.__name__} code={self.code} resp_code={self.expected_response_code}>"


def encode(code: int, data: bytes | None) -> bytes:
    """
    Encode message

    Note: this uses the standard header of `72, 76, 5`
    See CamProtocolUtils for additional headers.
    """
    data = data or b""

    return pack(f"<BBHHH8x{len(data)}s", 72, 76, 5, code, len(data), data)


def decode(buf):
    if len(buf) < 16:
        raise TutkWyzeProtocolError("IOCtrl message too short")

    header = TutkWyzeProtocolHeader.from_buffer_copy(buf)

    if header.prefix != b"HL":
        raise TutkWyzeProtocolError("IOCtrl message should begin with the prefix 'HL'")

    expected_size = header.txt_len + 16
    if len(buf) != expected_size:
        raise TutkWyzeProtocolError(
            f"Encoded length doesn't match message size (header says {expected_size}, got message of len {len(buf)}"
        )

    return header, buf[16:expected_size] if header.txt_len > 0 else None


STATUS_MESSAGES = {2: "updating", 4: "checking enr", 5: "off"}


def generate_challenge_response(camera_enr_b, enr, camera_status):
    if camera_status == 3:
        assert len(enr.encode("ascii")) >= 16, "Enr expected to be 16 bytes"
        camera_secret_key = enr.encode("ascii")[:16]
    elif camera_status == 6:
        assert len(enr.encode("ascii")) >= 32, "Enr expected to be 32 bytes"
        secret_key = enr.encode("ascii")[:16]
        camera_enr_b = xxtea.decrypt(camera_enr_b, secret_key, padding=False)
        camera_secret_key = enr.encode("ascii")[16:32]
    else:
        camera_secret_key = b"FFFFFFFFFFFFFFFF"

    return xxtea.decrypt(camera_enr_b, camera_secret_key, padding=False)


def supports(product_model, protocol, command):
    commands_db = device_config["supportedCommands"]
    supported_commands = []

    for k in commands_db["default"]:
        if int(k) <= int(protocol):
            supported_commands.extend(commands_db["default"][k])

    if product_model in commands_db:
        for k in commands_db[product_model]:
            if int(k) <= int(protocol):
                supported_commands.extend(commands_db[product_model][k])

    return str(command) in supported_commands
