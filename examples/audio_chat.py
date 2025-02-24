import asyncio
import sys
import sounddevice as sd
import numpy as np
from xiaozhi_client import XiaozhiClient, ClientConfig, AudioConfig
import websockets
import threading

def print_message(message: str, end='\n'):
    """打印消息到控制台"""
    sys.stdout.write(message + end)
    sys.stdout.flush()

class AudioHandler:
    def __init__(self, client, main_loop):
        self.client = client
        self.main_loop = main_loop
        self._lock = threading.Lock()
        self._audio_loop = None
        self.recording_paused = False  # 用于TTS期间暂停录音
    
    def get_loop(self):
        """Get or create event loop for current thread"""
        if self._audio_loop is None:
            with self._lock:
                try:
                    self._audio_loop = asyncio.get_event_loop()
                except RuntimeError:
                    self._audio_loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(self._audio_loop)
        return self._audio_loop
    
    def audio_callback(self, indata, frames, time, status):
        if self.recording_paused:  # TTS播放期间不发送音频
            return
        if status:
            print_message(f"\n[错误] {status}")
            return
            
        try:
            # 将录音数据转换为float32格式
            audio_data = indata.reshape(-1).astype(np.float32)
            # 在主循环中执行发送
            asyncio.run_coroutine_threadsafe(
                self.client.send_audio(audio_data),
                self.main_loop
            )
        except Exception as e:
            print_message(f"\n[错误] 音频处理失败: {e}")

async def main():
    # 配置
    config = ClientConfig(
        ws_url="ws://localhost:8000",
    )
    audio_config = AudioConfig(
        sample_rate=16000,
        channels=1,
        frame_size=960
    )
    
    client = XiaozhiClient(config, audio_config)
    
    # 添加对话状态控制
    conversation_active = True
    audio_handler = AudioHandler(client, asyncio.get_running_loop())
    
    # 设置回调函数
    async def on_tts_start(msg):
        print_message("\n[系统] AI开始说话...")
        audio_handler.recording_paused = True  # 暂停录音
        
    async def on_tts_end(msg):
        print_message("\n[系统] AI说话结束")
        print_message("\n[系统] 继续聆听中... (q:退出 r:重置对话)")
        audio_handler.recording_paused = False  # 恢复录音
        
    async def on_connection_lost(error_msg):
        print_message(f"\n[系统] 连接断开: {error_msg}")
        nonlocal conversation_active
        conversation_active = False

    client.on_tts_start = on_tts_start
    client.on_tts_end = on_tts_end
    client.on_connection_lost = on_connection_lost

    # 简化的命令处理
    async def handle_command():
        nonlocal conversation_active
        while conversation_active:
            cmd = await asyncio.get_event_loop().run_in_executor(
                None, 
                lambda: input("")
            )
            cmd = cmd.strip().lower()
            
            if cmd == 'q':
                conversation_active = False
                print_message("\n[系统] 正在结束对话...")
                break
            elif cmd == 'r':
                print_message("\n[系统] 重置对话...")
                await client.abort()  # 中止当前对话
                await client.start_listen()  # 重新开始语音识别
                audio_handler.recording_paused = False  # 确保录音已启动

    try:
        # 连接服务器
        await client.connect()
        print_message("\n[系统] 已连接到服务器")
        print_message("[系统] 开始聆听... (q:退出 r:重置对话)\n")
        
        # 开始录音和对话循环
        with sd.InputStream(
            channels=1,
            samplerate=16000,
            callback=audio_handler.audio_callback
        ):
            # 开始语音识别
            await client.start_listen()
            
            # 启动命令处理
            command_task = asyncio.create_task(handle_command())
            
            # 主循环
            while conversation_active:
                await asyncio.sleep(0.1)
                
            # 等待命令处理完成
            await command_task
                
    except (websockets.exceptions.WebSocketException, ConnectionError) as e:
        print_message(f"\n[系统] 连接失败: {str(e)}")
    except KeyboardInterrupt:
        print_message("\n[系统] 正在停止录音...")
    finally:
        conversation_active = False
        # 停止语音识别
        try:
            await client.stop_listen()
        except:
            pass
        # 关闭连接
        await client.close()
        print_message("[系统] 程序已退出")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
