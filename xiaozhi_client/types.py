from dataclasses import dataclass
from typing import Optional, Dict, Any, List
from enum import Enum

class ListenMode(Enum):
    AUTO = "auto"
    MANUAL = "manual" 
    REALTIME = "realtime"

class MessageType(Enum):
    HELLO = "hello"
    ABORT = "abort"  # 添加中止消息类型
    LISTEN = "listen"
    CHAT = "chat"
    TTS = "tts"
    IOT = "iot"
    LLM = "llm"

class ListenState(Enum):
    START = "start"
    STOP = "stop"
    DETECT = "detect"

@dataclass
class AudioConfig:
    sample_rate: int = 16000
    channels: int = 1
    frame_size: int = 960
    frame_duration: int = 60
    format: str = "opus"

@dataclass
class ClientConfig:
    ws_url: str
    device_token: str = "test-token"
    enable_token: bool = True
    protocol_version: int = 1

@dataclass
class IoTProperty:
    description: str
    type: str

@dataclass
class IoTMethod:
    description: str
    parameters: Dict[str, Any]

@dataclass
class IoTDescriptor:
    name: str
    description: str
    properties: Dict[str, IoTProperty]
    methods: Dict[str, IoTMethod]

@dataclass
class IoTMessage:
    session_id: str
    type: str
    descriptors: List[IoTDescriptor]
