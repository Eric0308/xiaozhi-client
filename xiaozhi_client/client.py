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
from queue import Queue, Empty, Full
import sounddevice as sd
from xiaozhi_client.utils.wav import save_wav
import time  # 确保引入time模块
import random  # 确保引入random模块

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
        self.on_tts_message: Optional[Callable] = None
        self.on_iot_message: Optional[Callable] = None
        self.on_listen_message: Optional[Callable] = None
        self.on_hello_message: Optional[Callable] = None
        self.on_llm_message: Optional[Callable] = None
        self.on_stt_message: Optional[Callable] = None
        self.on_other_message: Optional[Callable] = None
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

        # 录音相关状态
        self.is_recording = False
        self.recording_stream = None
        self.silent_frames_count = 0
        self.recording_buffer = []

        # 语音输入相关
        self._input_stream = None
        self._audio_input_queue = asyncio.Queue()
        self._input_task = None
        self._input_paused = threading.Event()
        self._input_running = threading.Event()
        self._input_queue = Queue(maxsize=1024)  # 改用线程安全的Queue
        self._input_initialized = False  # 添加新标记表示输入是否已经初始化过
        self._last_audio_sent_time = 0  # 添加最近一次发送音频的时间戳
        self._silence_detection_enabled = True  # 是否启用静音检测
        self._silence_threshold = 0.01  # 静音阈值
        self._consecutive_silence_frames = 0  # 连续静音帧计数
        self._max_silence_frames = 200  # 最大静音帧数 (约3-4秒)
        self._last_stats_time = 0  # 上次统计信息时间

    def _init_decoder(self):
        """初始化解码器"""
        self.decoder = opuslib.Decoder(
            self.audio_config.sample_rate,
            self.audio_config.channels
        )

    def set_device_id(self, device_id: str):
        """设置设备ID"""
        self.device_id = device_id
        
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
        await self.stop_voice_input()
        # 确保停止录音
        if self.is_recording:
            await self.stop_recording()
            
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
            elif msg_type == MessageType.LLM.value:
                await self._handle_llm_message(msg_data)
            elif msg_type == MessageType.STT.value:
                await self._handle_stt_message(msg_data)
            elif msg_type == MessageType.HELLO.value:
                await self._handle_hello_message(msg_data)
            else:
                await self._handle_other_message(msg_data)
            
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

    async def _handle_hello_message(self, msg_data: dict):
        #logger.info(f"服务器消息: {msg_data}")
        """处理Hello消息"""
        if self.on_hello_message:
            await self.on_hello_message(msg_data)
    
    async def _handle_llm_message(self, msg_data: dict):
        logger.info(f"LLM消息: {msg_data.get('text')}")
        """处理LLM消息"""
        if self.on_llm_message:
            await self.on_llm_message(msg_data)
    
    async def _handle_stt_message(self, msg_data: dict):
        logger.info(f"STT消息: {msg_data.get('text')}")
        """处理STT消息"""
        if self.on_stt_message:
            await self.on_stt_message(msg_data)

    async def _handle_other_message(self, msg_data: dict):
        """处理其他消息"""
        logger.info(f"未知消息类型{msg_data.get('type')}: {msg_data}")
        if self.on_other_message:
            await self.on_other_message

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
            if self.on_tts_message:
                await self.on_tts_message(msg_data)
                
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

            # 计算并记录音频强度
            rms = np.sqrt(np.mean(np.square(audio_data)))
            if random.random() < 0.01:  # 只对1%的帧记录强度
                logger.debug(f"发送音频数据，强度: {rms:.5f}")

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

    async def start_recording(self, silence_threshold: float = 0.01, 
                            silence_frames: int = 5,
                            sound_threshold: float = 0.1):
        """开始录音并实时发送音频数据

        Args:
            silence_threshold: 静音判断阈值
            silence_frames: 连续静音帧数阈值 
            sound_threshold: 声音判断阈值
        """
        if self.is_recording:
            return

        self.is_recording = True
        self.silent_frames_count = 0
        self.recording_buffer = []

        def audio_callback(indata, frames, time, status):
            if status:
                logger.warning(f"音频输入状态: {status}")
            
            if not self.is_recording:
                return

            try:
                # 计算音频能量
                audio_data = np.frombuffer(indata, dtype=np.float32)
                rms = np.sqrt(np.mean(audio_data ** 2))

                if rms > sound_threshold:
                    self.silent_frames_count = 0
                    # 将float32数据转换为PCM int16
                    pcm_data = (audio_data * 32767).astype(np.int16)
                    # 编码为Opus格式
                    opus_data = self.encoder.encode(pcm_data.tobytes(), 
                                                  self.audio_config.frame_size)
                    if opus_data:
                        # 使用事件循环发送数据
                        asyncio.run_coroutine_threadsafe(
                            self.websocket.send(opus_data), 
                            asyncio.get_event_loop()
                        )
                        self.recording_buffer.append(pcm_data.tobytes())
                else:
                    self.silent_frames_count += 1
                    if self.silent_frames_count >= silence_frames:
                        # 停止录音
                        asyncio.run_coroutine_threadsafe(
                            self.stop_recording(),
                            asyncio.get_event_loop()
                        )

            except Exception as e:
                logger.error(f"录音处理错误: {e}")

        try:
            # 启动录音流
            self.recording_stream = sd.InputStream(
                channels=self.audio_config.channels,
                samplerate=self.audio_config.sample_rate,
                callback=audio_callback,
                dtype=np.float32,
                blocksize=self.audio_config.frame_size
            )
            self.recording_stream.start()
            logger.info("开始录音")

            # 发送开始录音的消息
            await self.start_listen()

        except Exception as e:
            logger.error(f"启动录音失败: {e}")
            self.is_recording = False
            raise

    async def stop_recording(self):
        """停止录音"""
        if not self.is_recording:
            return

        self.is_recording = False
        if self.recording_stream:
            self.recording_stream.stop()
            self.recording_stream.close()
            self.recording_stream = None

        # 保存录音文件
        try:
            if self.recording_buffer:
                timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
                filepath = os.path.join(self.audio_dir, f'recorded_{timestamp}.wav')
                save_wav(filepath, b''.join(self.recording_buffer))
                logger.info(f"录音已保存: {filepath}")
        except Exception as e:
            logger.error(f"保存录音失败: {e}")

        # 发送停止录音的消息
        await self.stop_listen()
        logger.info("停止录音")

    def check_audio_input(self) -> bool:
        """检查是否有可用的音频输入设备"""
        try:
            with sd.InputStream(
                channels=self.audio_config.channels,
                samplerate=self.audio_config.sample_rate,
                dtype=np.float32,
                blocksize=self.audio_config.frame_size
            ) as stream:
                return True
        except Exception as e:
            logger.warning(f"检查音频输入设备失败: {e}")
            return False

    async def start_voice_input(self):
        """启动语音输入"""
        logger.debug("开始启动语音输入")
        
        # 先确保之前的资源被清理
        if self._input_stream is not None:
            logger.debug("检测到已存在语音输入流，先停止它")
            await self.stop_voice_input()
        
        if not self.check_audio_input():
            raise RuntimeError("未检测到可用的音频输入设备")

        self._input_running.set()
        self._input_paused.clear()
        self._last_audio_sent_time = time.time()
        self._consecutive_silence_frames = 0

        def input_callback(indata, frames, time, status):
            if status or self._input_paused.is_set():
                return
                
            try:
                audio_data = indata.reshape(-1).astype(np.float32)
                rms = np.sqrt(np.mean(np.square(audio_data)))
                
                # 如果是有效声音，直接发送
                if rms > self._silence_threshold:
                    try:
                        self._input_queue.put_nowait((audio_data, rms))
                        self._consecutive_silence_frames = 0
                    except Full:
                        pass
                else:
                    # 如果是静音，记录并适时发送静音帧
                    self._consecutive_silence_frames += 1
                    if self._consecutive_silence_frames <= self._max_silence_frames:
                        try:
                            self._input_queue.put_nowait((audio_data * 0.01, rms))
                        except Full:
                            pass
            except Exception as e:
                logger.debug(f"音频处理错误: {e}")
            
        try:
            self._input_stream = sd.InputStream(
                channels=self.audio_config.channels,
                samplerate=self.audio_config.sample_rate,
                callback=input_callback,
                dtype=np.float32,
                blocksize=self.audio_config.frame_size
            )
            self._input_stream.start()
            
            if self._input_task and not self._input_task.done():
                self._input_task.cancel()
                try:
                    await self._input_task
                except asyncio.CancelledError:
                    pass
                
            self._input_task = asyncio.create_task(self._process_input())
            await self.start_listen()
            self._input_initialized = True
            logger.info("语音输入已启动")
            
        except Exception as e:
            self._input_running.clear()
            logger.error(f"启动语音输入失败: {str(e)}")
            raise RuntimeError(f"启动语音输入失败: {e}")

    async def _process_input(self):
        """处理输入音频队列"""
        try:
            logger.info("开始处理音频输入")
            recording = False  # 添加录音状态标记
            frames_sent = 0
            
            while self._input_running.is_set():
                try:
                    while not self._input_queue.empty() and self._input_running.is_set():
                        audio_data, rms = self._input_queue.get_nowait()
                        
                        if not self._input_paused.is_set():
                            # 如果音频强度超过阈值，进入录音状态
                            if rms > self._silence_threshold:
                                if not recording:
                                    logger.debug(f"检测到声音开始，能量: {rms:.5f}")
                                    recording = True
                                    # 发送开始录音消息
                                    await self.start_listen()
                                
                                # 直接发送音频数据
                                await self.send_audio(audio_data)
                                frames_sent += 1
                                self._last_audio_sent_time = time.time()
                                self._consecutive_silence_frames = 0
                            else:
                                if recording:
                                    self._consecutive_silence_frames += 1
                                    # 发送低音量帧以触发服务端静音检测
                                    await self.send_audio(audio_data * 0.01)
                                    
                                    # 如果连续静音帧达到阈值，结束录音
                                    if self._consecutive_silence_frames >= self._max_silence_frames:
                                        logger.debug(f"检测到语音结束，已发送 {frames_sent} 帧")
                                        recording = False
                                        frames_sent = 0
                                        # 发送停止录音消息
                                        await self.stop_listen()
                        
                        self._input_queue.task_done()
                        
                    await asyncio.sleep(0.001)
                    
                except Empty:
                    continue
                except Exception as e:
                    logger.error(f"处理音频帧错误: {e}")
                    
        except asyncio.CancelledError:
            if recording:
                # 如果被取消时正在录音，确保发送停止消息
                await self.stop_listen()
            logger.debug("音频输入处理任务已取消")
        except Exception as e:
            logger.error(f"音频输入处理任务异常: {e}")

    def pause_voice_input(self):
        """暂停语音输入"""
        logger.debug("暂停语音输入")
        self._input_paused.set()

    def resume_voice_input(self):
        """恢复语音输入"""
        logger.debug("恢复语音输入")
        self._input_paused.clear()
        self._consecutive_silence_frames = 0
        self._last_audio_sent_time = time.time()
        
        # 清空积累的队列数据
        items_cleared = 0
        while True:
            try:
                self._input_queue.get_nowait()
                self._input_queue.task_done()
                items_cleared += 1
            except Empty:
                break
        
        # 确保开始新的录音会话
        if items_cleared > 0:
            logger.debug(f"恢复语音输入: 清空了 {items_cleared} 个队列项")

    async def stop_voice_input(self):
        """停止语音输入"""
        logger.debug("停止语音输入")
        self._input_running.clear()
        
        # 确保发送停止消息
        try:
            await self.stop_listen()
        except Exception as e:
            logger.error(f"发送停止消息失败: {e}")
        
        if self._input_task:
            self._input_task.cancel()
            try:
                await self._input_task
            except asyncio.CancelledError:
                pass
            self._input_task = None
        
        if self._input_stream:
            self._input_stream.stop()
            self._input_stream.close()
            self._input_stream = None
            
        # 重置所有状态
        self._consecutive_silence_frames = 0
        self._last_audio_sent_time = 0
        
        # 清空所有队列
        while True:
            try:
                self._input_queue.get_nowait()
                self._input_queue.task_done()
            except Empty:
                break
            
        while not self._audio_input_queue.empty():
            try:
                self._audio_input_queue.get_nowait()
                self._audio_input_queue.task_done()
            except asyncio.QueueEmpty:
                break

    def enable_silence_detection(self, enabled=True, threshold=0.01, max_frames=200):
        """启用或禁用静音检测，并设置相关参数
        
        Args:
            enabled: 是否启用静音检测
            threshold: 静音阈值，默认0.01
            max_frames: 最大静音帧数，超过此值将认为语音结束，默认200
        """
        self._silence_detection_enabled = enabled
        self._silence_threshold = threshold
        self._max_silence_frames = max_frames
        # 重置相关计数器
        self._consecutive_silence_frames = 0
        self._last_audio_sent_time = time.time()
        logger.debug(f"静音检测设置: 启用={enabled}, 阈值={threshold}, 最大帧数={max_frames}")