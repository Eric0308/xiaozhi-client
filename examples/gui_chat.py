import asyncio
import tkinter as tk
from tkinter import ttk, scrolledtext
import threading
from xiaozhi_client import XiaozhiClient, ClientConfig, AudioConfig
from loguru import logger
import queue

class VoiceChatGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("小智语音助手")
        self.root.geometry("800x600")  # 增大窗口以显示更多内容
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)  # 添加窗口关闭处理
        
        # 创建消息队列用于线程间通信
        self.msg_queue = queue.Queue()
        
        # 创建GUI组件
        self.setup_gui()
        
        # 初始化客户端相关变量
        self.client = None
        self.is_running = False
        self.loop = None
        self.client_task = None
        
        # 启动消息处理
        self.process_messages()

    def setup_gui(self):
        # 状态框架
        status_frame = ttk.LabelFrame(self.root, text="状态", padding="5")
        status_frame.pack(fill="x", padx=5, pady=5)
        
        self.connection_status = ttk.Label(status_frame, text="未连接")
        self.connection_status.pack(side="left", padx=5)
        
        self.voice_status = ttk.Label(status_frame, text="语音输入：关闭")
        self.voice_status.pack(side="left", padx=5)
        
        # 控制按钮框架
        control_frame = ttk.Frame(self.root)
        control_frame.pack(fill="x", padx=5, pady=5)
        
        self.start_button = ttk.Button(control_frame, text="开始对话", command=self.toggle_chat)
        self.start_button.pack(side="left", padx=5)
        
        self.reset_button = ttk.Button(control_frame, text="重置对话", command=self.reset_chat, state="disabled")
        self.reset_button.pack(side="left", padx=5)
        
        # 对话和日志区域的容器
        content_frame = ttk.Frame(self.root)
        content_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        # 对话显示区域（左侧）
        chat_frame = ttk.LabelFrame(content_frame, text="对话", padding="5")
        chat_frame.pack(side="left", fill="both", expand=True, padx=(0, 2.5))
        
        self.chat_area = scrolledtext.ScrolledText(chat_frame, wrap=tk.WORD)
        self.chat_area.pack(fill="both", expand=True)
        
        # 日志显示区域（右侧）
        log_frame = ttk.LabelFrame(content_frame, text="日志", padding="5")
        log_frame.pack(side="left", fill="both", expand=True, padx=(2.5, 0))
        
        self.log_area = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD)
        self.log_area.pack(fill="both", expand=True)

    def log_message(self, message, area="log"):
        self.msg_queue.put((area, message))

    def process_messages(self):
        try:
            while True:
                area, message = self.msg_queue.get_nowait()
                if area == "log":
                    self.log_area.insert(tk.END, message + "\n")
                    self.log_area.see(tk.END)
                elif area == "chat":
                    self.chat_area.insert(tk.END, message + "\n")
                    self.chat_area.see(tk.END)
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self.process_messages)

    def update_status(self, connection_status=None, voice_status=None):
        if connection_status:
            self.connection_status.config(text=connection_status)
        if voice_status:
            self.voice_status.config(text=f"语音输入：{voice_status}")

    async def setup_client(self):
        config = ClientConfig(ws_url = "ws://localhost:8000") #不要改此行，现在是开发环境
        audio_config = AudioConfig(sample_rate=16000, channels=1)
        self.client = XiaozhiClient(config, audio_config)
        
        # 配置静音检测 - 降低阈值以提高灵敏度，适当减少帧数以加快响应
        self.client.enable_silence_detection(
            enabled=True, 
            threshold=0.008,  # 降低阈值使其更容易检测到声音
            max_frames=200  # 减少帧数以更快响应
        )
        
        # 设置回调
        async def on_tts_start(msg):
            self.log_message("\n[系统] AI开始说话...", "log")
            self.client.pause_voice_input()
            self.update_status(voice_status="暂停")

        async def on_tts_message(msg):
            state = msg.get('state')
            if state == 'sentence_start':
                text = msg.get('text', '').strip()
                if text:
                    self.log_message(f"[AI] {text}", "chat")
                    # 每次开始说新句子时确保语音输入暂停
                    self.client.pause_voice_input()
                    self.update_status(voice_status="暂停")

        async def on_tts_end(msg):
            self.log_message("\n[系统] AI说话结束", "log")
            self.log_message("\n[系统] 继续聆听中...", "log")
            
            try:
                # 先确保语音输入停止
                try:
                    await self.client.stop_voice_input()
                except Exception as e:
                    logger.warning(f"停止语音输入时出错: {e}")
                
                await asyncio.sleep(0.3)  # 等待状态切换
                
                # 如果程序还在运行，重新启动语音输入
                if self.is_running:
                    try:
                        await self.client.start_voice_input()
                        self.client._consecutive_silence_frames = 0
                        self.client._last_audio_sent_time = asyncio.get_event_loop().time()
                        self.update_status(voice_status="开启")
                    except Exception as e:
                        logger.error(f"启动语音输入失败: {e}")
                        self.log_message(f"\n[错误] 启动语音输入失败: {e}", "log")
            except Exception as e:
                logger.error(f"重启语音输入过程中出错: {e}")
                self.log_message(f"\n[错误] 重启语音输入过程中出错: {e}", "log")

        async def on_stt_message(msg_data):
            text = msg_data.get('text', '').strip()
            if text:
                self.log_message(f"[你] {text}", "chat")
                
        async def on_llm_message(msg_data):
            text = msg_data.get('text', '').strip()
            emoji = msg_data.get('emoji', '').strip()
            if emoji:
                self.log_message(f"[表情] {emoji}", "chat")
            if text:
                self.log_message(f"[AI] {text}", "chat")
            
        async def on_connection_lost(reason):
            self.log_message(f"\n[系统] 连接断开: {reason}")
            self.update_status(connection_status="已断开")
            self.log_message("[系统] 尝试重新连接...")
            try:
                await self.client.connect()
                self.log_message("[系统] 重新连接成功")
                self.update_status(connection_status="已连接")
                await self.client.start_voice_input()
            except Exception as e:
                self.log_message(f"[系统] 重新连接失败: {e}")
        
        self.client.on_tts_start = on_tts_start
        self.client.on_tts_message = on_tts_message
        self.client.on_tts_end = on_tts_end
        self.client.on_connection_lost = on_connection_lost
        self.client.on_stt_message = on_stt_message
        self.client.on_llm_message = on_llm_message

    async def start_chat(self):
        try:
            # 配置日志
            logger.add("gui_chat.log", rotation="10 MB", level="DEBUG")
            
            # 连接服务器
            await self.client.connect()
            self.update_status(connection_status="已连接")
            self.log_message("\n[系统] 已连接到服务器")
            
            # 启动语音输入
            await self.client.start_voice_input()
            self.update_status(voice_status="开启")
            self.log_message("[系统] 开始对话...\n")
            
            # 启用重置按钮
            self.root.after(0, lambda: self.reset_button.config(state="normal"))
            
            # 保持运行直到停止
            while self.is_running:
                await asyncio.sleep(0.1)
                
        except Exception as e:
            logger.error(f"错误: {e}", exc_info=True)
            self.log_message(f"\n[错误] {str(e)}")
        finally:
            if self.client:
                await self.client.close()
            self.update_status(connection_status="未连接", voice_status="关闭")
            self.is_running = False
            self.root.after(0, lambda: self.start_button.config(text="开始对话"))
            self.root.after(0, lambda: self.reset_button.config(state="disabled"))

    def toggle_chat(self):
        if not self.is_running:
            # 启动对话
            self.is_running = True
            self.start_button.config(text="停止对话")
            
            # 创建新的事件循环
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            
            async def start_chat_async():
                await self.setup_client()
                await self.start_chat()
            
            # 在新线程中运行异步任务
            def run_async():
                self.loop.run_until_complete(start_chat_async())
                self.loop.close()
            
            self.client_task = threading.Thread(target=run_async)
            self.client_task.start()
        else:
            # 停止对话
            self.is_running = False
            self.start_button.config(text="开始对话")
            if self.client:
                asyncio.run_coroutine_threadsafe(self.client.close(), self.loop)

    async def reset_chat_async(self):
        if self.client:
            self.log_message("\n[系统] 重置对话...")
            await self.client.abort()
            # 重置语音输入
            await self.client.stop_voice_input()
            await asyncio.sleep(0.3)  # 减少重置时的等待时间
            await self.client.start_voice_input()
            # 重置静音检测
            self.client._consecutive_silence_frames = 0
            self.client._last_audio_sent_time = asyncio.get_event_loop().time()
            self.log_message("[系统] 对话已重置，开始新对话...")

    def reset_chat(self):
        if self.is_running and self.loop:
            asyncio.run_coroutine_threadsafe(self.reset_chat_async(), self.loop)

    def on_closing(self):
        """处理窗口关闭事件"""
        try:
            if self.is_running:
                # 停止对话
                self.is_running = False
                if self.client:
                    if self.loop and self.loop.is_running():
                        asyncio.run_coroutine_threadsafe(self.client.close(), self.loop)
                        # 清理和等待所有任务完成
                        pending = asyncio.all_tasks(self.loop)
                        for task in pending:
                            task.cancel()
                        # 等待关闭完成
                        if self.client_task and self.client_task.is_alive():
                            self.client_task.join(timeout=2.0)
        except Exception as e:
            logger.error(f"关闭时发生错误: {e}", exc_info=True)
        finally:
            # 关闭窗口
            try:
                self.root.quit()
                self.root.destroy()
            except Exception as e:
                logger.error(f"关闭窗口时发生错误: {e}", exc_info=True)

def main():
    try:
        root = tk.Tk()
        app = VoiceChatGUI(root)
        root.mainloop()
    except Exception as e:
        logger.error(f"程序异常退出: {e}", exc_info=True)

if __name__ == "__main__":
    main()
