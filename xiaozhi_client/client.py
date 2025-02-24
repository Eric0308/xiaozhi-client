import asyncio
import json
import uuid
import numpy as np
import websockets
import opuslib
from loguru import logger
from typing import Optional, Callable, Any, List
from .types import AudioConfig, ClientConfig, ListenMode, MessageType, ListenState
import os
import datetime
import threading
from queue import Queue, Empty 
import sounddevice as sd
from xiaozhi_client.utils.wav import save_wav

class XiaozhiClient:
    def __init__(self, config: ClientConfig, audio_config: Optional[AudioConfig] = None):
        self.config = config
        self.audio_config = audio_config or AudioConfig()
        self.device_id = self._get_device_id()
        self.client_id = str(uuid.uuid4())
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self.encoder = opuslib.Encoder(
            self.audio_config.sample_rate,
            self.audio_config.channels,
            'voip'
        )
        self.decoder = None
        self._init_decoder()
        
        # 回调函数
        self.on_tts_start: Optional[Callable] = None
        self.on_tts_data: Optional[Callable] = None #暂不对外开放
        self.on_tts_end: Optional[Callable] = None
        self.on_message: Optional[Callable] = None
        self.on_connection_lost: Optional[Callable[[str], Any]] = None  # 添加连接断开回调
        self.on_connection_error: Optional[Callable[[Exception], Any]] = None  # 添加连接错误回调

        # 音频处理状态
        self.pcm_buffer = bytearray()  # 改为使用 bytearray
        self.current_sentence_text = ""

        # 音频播放相关
        self.audio_queue = Queue()
        self.is_playing = threading.Event()
        self.should_exit = threading.Event()
        self.audio_dir = "received_audio"
        os.makedirs(self.audio_dir, exist_ok=True)
        self.stream = None
        self._audio_task = None

        self.message_queue = asyncio.Queue()  # 添加消息队列
        self.audio_data_queue = asyncio.Queue()  # 添加音频数据队列

        self.audio_play_thread = None
        self.audio_buffer = Queue(maxsize=1024)  # 添加音频缓冲队列

    def _init_decoder(self):
        """初始化解码器"""
        self.decoder = opuslib.Decoder(
            self.audio_config.sample_rate,
            self.audio_config.channels
        )

    def _get_device_id(self) -> str:
        # 获取本机的MAC地址
        mac = uuid.getnode()
        # 将MAC地址转换为常见的格式（如：00:1A:2B:3C:4D:5E）
        mac_hex = ':'.join(['{:02x}'.format((mac >> elements) & 0xff) for elements in range(0,8*6,8)][::-1])
        return mac_hex

    def _get_headers(self) -> dict:
        """获取连接头信息"""
        headers = {
            'Device-Id': self.device_id,
            'Protocol-Version': str(self.config.protocol_version),
        }
        return headers

    async def connect(self):
        """建立WebSocket连接"""
        headers = {}
        
        # 合并设备标识等headers
        headers.update(self._get_headers())
        
        try:
            self.websocket = await websockets.connect(
                self.config.ws_url,
                extra_headers=headers,  # 使用 extra_headers
                ping_interval=20,  # 启用ping检测，20秒一次
                ping_timeout=10,   # ping超时时间
                close_timeout=5    # 关闭超时时间
            )
            asyncio.create_task(self._message_handler())
            # 启动音频播放任务
            self._audio_task = asyncio.create_task(self._run_audio_player())
            # 启动消息处理任务
            asyncio.create_task(self._process_messages())
            asyncio.create_task(self._process_audio_queue())
            # 发送hello消息
            await self._send_hello()
        except (websockets.exceptions.WebSocketException, ConnectionError) as e:
            error_msg = f"连接失败: {str(e)}"
            logger.error(error_msg)
            if self.on_connection_error:
                await self.on_connection_error(e)
            raise

    async def _send_hello(self):
        """发送hello消息"""
        hello_message = {
            "type": MessageType.HELLO.value,
            "version": self.config.protocol_version,
            "transport": "websocket",
            "audio_params": {
                "format": self.audio_config.format,
                "sample_rate": self.audio_config.sample_rate,
                "channels": self.audio_config.channels,
                "frame_duration": self.audio_config.frame_duration
            }
        }
        await self.websocket.send(json.dumps(hello_message, ensure_ascii=False))

    """处理接收到的网络消息"""
    async def _message_handler(self):
        try:
            async for message in self.websocket:
                if isinstance(message, str):
                    try:
                        msg_data = json.loads(message)
                        await self.message_queue.put(msg_data)
                    except json.JSONDecodeError:
                        if self.on_message:
                            await self.on_message(message)
                else:
                    # 音频数据直接处理，不经过队列
                    await self.audio_data_queue.put(message)
                    pass

        except websockets.exceptions.ConnectionClosed as e:
            error_msg = f"WebSocket连接已关闭: {e.code} - {e.reason}"
            logger.error(error_msg)
            if self.on_connection_lost:
                await self.on_connection_lost(error_msg)
        except websockets.exceptions.WebSocketException as e:
            error_msg = f"WebSocket错误: {str(e)}"
            logger.error(error_msg)
            if self.on_connection_error:
                await self.on_connection_error(e)
        except Exception as e:
            error_msg = f"未知错误: {str(e)}"
            logger.error(error_msg)
            if self.on_connection_error:
                await self.on_connection_error(e)
        finally:
            # 确保连接断开时清理资源
            await self._cleanup()

    async def _cleanup(self):
        """清理资源"""
        self.should_exit.set()
        if self.stream:
            self.stream.stop()
            self.stream.close()
        if self.websocket:
            await self.websocket.close()
            self.websocket = None
        
        # 清空队列
        while not self.audio_queue.empty():
            self.audio_queue.get_nowait()
            self.audio_queue.task_done()

    async def _process_messages(self):
        """处理消息队列"""
        while True:
            msg_data = await self.message_queue.get()
            self.message_queue.task_done()
            
            msg_type = msg_data.get('type')
            if msg_type == MessageType.TTS.value:
                await self._handle_tts_message(msg_data)
            elif msg_type == MessageType.IOT.value:
                await self._handle_iot_message(msg_data)
            elif msg_type == MessageType.LISTEN.value:
                await self._handle_listen_message(msg_data)
            
            if self.on_message:
                await self.on_message(msg_data)
                

    async def _process_audio_queue(self):
        """处理音频数据队列"""
        while True:
            audio_data = await self.audio_data_queue.get()
            self.audio_data_queue.task_done()
            try:
                pcm_data = self.decoder.decode(audio_data, self.audio_config.frame_size)
                if pcm_data:
                    self.audio_queue.put((pcm_data, True))
                    # Convert PCM data to bytes if it isn't already
                    if isinstance(pcm_data, (bytes, bytearray)):
                        self.pcm_buffer.extend(pcm_data)
                    else:
                        self.pcm_buffer.extend(bytes(pcm_data))
            except Exception as e:
                logger.error(f"音频处理错误: {e}")
                self._init_decoder()

    async def _handle_tts_message(self, msg_data: dict):
        """处理TTS状态消息"""
        state = msg_data.get('state')        
        if state == 'start':
            self.pcm_buffer = bytearray()  # 重置为空 bytearray
            self._init_decoder()
            logger.info(f"TTS开始 ")
            if self.on_tts_start:
                await self.on_tts_start(msg_data)
                
        elif state == 'sentence_start':
            self.current_sentence_text = msg_data.get('text', '')
            logger.info(f"tts语句: {self.current_sentence_text}")
                
        elif state == 'stop':
            logger.info(f"TTS结束")
            try:
                save_wav(self.audio_dir, self.pcm_buffer)
            except Exception as e:
                logger.error(f"保存音频文件失败: {e}")

            if self.on_tts_end:
                await self.on_tts_end(msg_data)

    async def _handle_iot_message(self, msg_data: dict):
        """处理IoT设备描述消息"""
        # 可以在这里添加IoT设备描述的处理逻辑
        pass

    async def _handle_listen_message(self, msg_data: dict):
        """处理语音识别状态消息"""
        # 可以在这里添加语音识别状态的处理逻辑
        pass

    async def send_audio(self, audio_data: np.ndarray):
        """发送音频数据
        
        Args:
            audio_data: float32类型的numpy数组，范围[-1.0, 1.0]
        """
        if self.websocket is None or self.websocket.closed:
            raise ConnectionError("WebSocket connection not established")

        try:
            # 确保数据是float32类型
            if audio_data.dtype != np.float32:
                audio_data = audio_data.astype(np.float32)

            # 将float32数据转换为PCM int16格式
            pcm_data = (audio_data * 32767).astype(np.int16)
            
            # 按帧长度分割数据
            frame_size = self.audio_config.frame_size
            for i in range(0, len(pcm_data), frame_size):
                frame = pcm_data[i:i + frame_size]
                
                # 如果是最后一帧且长度不足，则补零
                if len(frame) < frame_size:
                    frame = np.pad(frame, (0, frame_size - len(frame)))
                
                # 编码为Opus格式
                opus_data = self.encoder.encode(frame.tobytes(), frame_size)
                if opus_data:
                    await self.websocket.send(opus_data)
                
        except Exception as e:
            logger.error(f"音频编码发送错误: {e}")
            # 重新初始化编码器
            self.encoder = opuslib.Encoder(
                self.audio_config.sample_rate,
                self.audio_config.channels,
                'voip'
            )
            raise

    async def send_text(self, message: dict):
        """发送文本消息"""
        if self.websocket is None or self.websocket.closed:
            raise ConnectionError("WebSocket connection not established")
        
        # 使用ensure_ascii=False来保持中文字符
        json_str = json.dumps(message, ensure_ascii=False)
        await self.websocket.send(json_str)

    async def close(self):
        """关闭连接"""
        await self._cleanup()
        # 等待队列处理完成
        if hasattr(self, 'message_queue'):
            await self.message_queue.join()
        if hasattr(self, 'audio_data_queue'):
            await self.audio_data_queue.join()
        if self._audio_task:
            await self._audio_task
        if self.websocket:
            await self.websocket.close()
            self.websocket = None

    async def start_listen(self, mode: ListenMode = ListenMode.AUTO):
        """开始语音识别"""
        await self.send_text({
            "type": MessageType.LISTEN.value,
            "state": ListenState.START.value,
            "mode": mode.value
        })

    async def stop_listen(self):
        """停止语音识别"""
        await self.send_text({
            "type": MessageType.LISTEN.value,
            "state": ListenState.STOP.value
        })
    
    async def send_txt_message(self, text: str):
        await self.send_text({
            "type": MessageType.LISTEN.value,
            "state": ListenState.DETECT.value,
            "text": text
        })

    async def abort(self):
        """中止当前对话"""
        await self.send_text({
            "type": MessageType.ABORT.value
        })
    def _audio_play_thread_fn(self):
        """专门的音频播放线程"""
        try:
            while not self.should_exit.is_set():
                try:
                    audio_data = self.audio_buffer.get(timeout=0.1)
                    if audio_data is not None:
                        self.is_playing.set()
                        self.stream.write(audio_data)
                except Empty:
                    self.is_playing.clear()
                    continue
                except Exception as e:
                    logger.error(f"音频播放错误: {e}")
        finally:
            self.is_playing.clear()

    async def _run_audio_player(self):
        """运行音频播放器"""
        self.stream = sd.OutputStream(
            samplerate=self.audio_config.sample_rate,
            channels=self.audio_config.channels,
            dtype=np.int16
        )
        self.stream.start()

        # 启动专门的音频播放线程
        self.audio_play_thread = threading.Thread(target=self._audio_play_thread_fn)
        self.audio_play_thread.daemon = True
        self.audio_play_thread.start()

        try:
            while not self.should_exit.is_set():
                if not self.audio_queue.empty():
                    data, is_stream = self.audio_queue.get()
                    self.audio_queue.task_done()

                    try:
                        if is_stream:
                            # 将音频数据放入缓冲队列
                            audio_data = np.frombuffer(data, dtype=np.int16)
                            self.audio_buffer.put(audio_data)
                    except Exception as e:
                        logger.error(f"音频处理错误: {e}")
                else:
                    await asyncio.sleep(0.01)
        finally:
            if self.stream:
                self.stream.stop()
                self.stream.close()
            # 等待音频播放线程结束
            if self.audio_play_thread and self.audio_play_thread.is_alive():
                self.should_exit.set()
                self.audio_play_thread.join(timeout=1.0)

