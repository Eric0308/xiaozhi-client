import asyncio
import tkinter as tk
from tkinter import ttk
import threading
from xiaozhi_client import XiaozhiClient, ClientConfig, AudioConfig
from loguru import logger
import queue
import os
from datetime import datetime
import math
from PIL import Image, ImageTk, ImageDraw

class AvatarAnimator:
    def __init__(self, canvas, x, y, size=160):
        self.canvas = canvas
        self.x = x
        self.y = y
        self.size = size
        self.angle = 0
        self.animation_id = None
        self.is_speaking = False
        
        # Try to load avatar image
        try:
            # Create circular mask
            mask = Image.new('L', (int(size), int(size)), 0)
            draw = ImageDraw.Draw(mask)
            draw.ellipse((0, 0, size, size), fill=255)
            
            # Try to load and mask the avatar image
            avatar_path = os.path.join(os.path.dirname(__file__), "head.png")
            if os.path.exists(avatar_path):
                # Load and resize image
                image = Image.open(avatar_path)
                image = image.resize((int(size), int(size)), Image.Resampling.LANCZOS)
                
                # Convert to RGBA if needed
                if image.mode != 'RGBA':
                    image = image.convert('RGBA')
                
                # Apply circular mask
                image.putalpha(mask)
                
                # Create photo image
                self.photo = ImageTk.PhotoImage(image)
                self.avatar = canvas.create_image(x, y, image=self.photo)
            else:
                # Fallback to default circle
                self.avatar = canvas.create_oval(
                    x - size/2, y - size/2,
                    x + size/2, y + size/2,
                    fill='#075E54',
                    width=2
                )
        except Exception as e:
            # Fallback to default circle if image loading fails
            logger.error(f"加载头像失败: {e}")
            self.avatar = canvas.create_oval(
                x - size/2, y - size/2,
                x + size/2, y + size/2,
                fill='#075E54',
                width=2
            )
        
        # Create outer ring
        ring_size = size + 20
        self.ring = canvas.create_oval(
            x - ring_size/2, y - ring_size/2,
            x + ring_size/2, y + ring_size/2,
            outline='#128C7E',
            width=2
        )
        
        # Create inner dot pattern
        self.dots = []
        self.create_dot_pattern()

    def create_dot_pattern(self):
        radius = self.size/3
        for i in range(8):
            angle = i * (360/8)
            rad = math.radians(angle)
            cx = self.x + radius * math.cos(rad)
            cy = self.y + radius * math.sin(rad)
            dot = self.canvas.create_oval(
                cx-3, cy-3, cx+3, cy+3,
                fill='white',
                outline='white'
            )
            self.dots.append(dot)

    def start_speaking_animation(self):
        self.is_speaking = True
        self.animate()

    def stop_speaking_animation(self):
        self.is_speaking = False
        if self.animation_id:
            self.canvas.after_cancel(self.animation_id)
            self.animation_id = None

    def animate(self):
        if not self.is_speaking:
            return
            
        self.angle = (self.angle + 2) % 360
        rad = math.radians(self.angle)
        
        # Rotate ring
        ring_size = self.size + 20 + math.sin(rad*2) * 5
        self.canvas.coords(
            self.ring,
            self.x - ring_size/2, self.y - ring_size/2,
            self.x + ring_size/2, self.y + ring_size/2
        )
        
        # Animate dots
        radius = self.size/3
        for i, dot in enumerate(self.dots):
            dot_angle = math.radians(i * (360/8) + self.angle)
            cx = self.x + radius * math.cos(dot_angle)
            cy = self.y + radius * math.sin(dot_angle)
            size = 3 + math.sin(dot_angle*2) * 2
            self.canvas.coords(
                dot,
                cx-size, cy-size,
                cx+size, cy+size
            )
        
        self.animation_id = self.canvas.after(20, self.animate)

class VoiceAssistantGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("小智助手")
        self.root.geometry("400x700")
        self.root.configure(bg='#F0F2F5')
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        self.msg_queue = queue.Queue()
        self.setup_gui()
        
        self.client = None
        self.is_running = False
        self.loop = None
        self.client_task = None
        
        self.process_messages()

    def setup_gui(self):
        # Header
        header_frame = tk.Frame(self.root, bg='#075E54', height=50)
        header_frame.pack(fill='x', side='top')
        header_frame.pack_propagate(False)
        
        title_label = tk.Label(header_frame, text="小智助手", font=('Arial', 16, 'bold'),
                              bg='#075E54', fg='white')
        title_label.pack(side='left', padx=15, pady=10)
        
        self.status_label = tk.Label(header_frame, text="", font=('Arial', 10),
                                   bg='#075E54', fg='#B5D7D1')
        self.status_label.pack(side='right', padx=15, pady=15)

        # Main content frame
        main_frame = tk.Frame(self.root, bg='#F0F2F5')
        main_frame.pack(fill='both', expand=True)

        # Avatar section (top)
        avatar_frame = tk.Frame(main_frame, bg='#F0F2F5', height=250)
        avatar_frame.pack(fill='x', padx=20, pady=(20,0))
        avatar_frame.pack_propagate(False)
        
        self.canvas = tk.Canvas(avatar_frame, bg='#F0F2F5', highlightthickness=0)
        self.canvas.pack(fill='both', expand=True)
        
        # Create avatar animator at center
        canvas_width = 400
        canvas_height = 250
        self.avatar = AvatarAnimator(self.canvas, canvas_width//2, canvas_height//2)
        
        # Emoji section (middle)
        emoji_frame = tk.Frame(main_frame, bg='#F0F2F5', height=80)
        emoji_frame.pack(fill='x', padx=20)
        emoji_frame.pack_propagate(False)
        
        self.emoji_label = tk.Label(emoji_frame, text="",
                                  font=('Arial', 48),
                                  bg='#F0F2F5')
        self.emoji_label.pack(expand=True)
        
        # Text section (bottom)
        text_frame = tk.Frame(main_frame, bg='#F0F2F5')
        text_frame.pack(fill='both', expand=True, padx=20, pady=(0,20))
        
        self.message_label = tk.Label(text_frame, text="", 
                                    font=('Arial', 14),
                                    bg='#F0F2F5', 
                                    wraplength=320,
                                    justify='center')
        self.message_label.pack(expand=True)

        # Bottom control area
        control_frame = tk.Frame(self.root, bg='white', height=100)
        control_frame.pack(fill='x', side='bottom')
        control_frame.pack_propagate(False)

        btn_frame = tk.Frame(control_frame, bg='white')
        btn_frame.pack(expand=True)

        self.start_button = tk.Button(btn_frame, text="开始对话", font=('Arial', 12),
                                    command=self.toggle_chat,
                                    bg='#075E54', fg='white',
                                    width=15, height=2)
        self.start_button.pack(side='left', padx=5)

        self.reset_button = tk.Button(btn_frame, text="重置对话", font=('Arial', 12),
                                    command=self.reset_chat,
                                    state="disabled",
                                    bg='#128C7E', fg='white',
                                    width=15, height=2)
        self.reset_button.pack(side='left', padx=5)

    def update_message(self, text="", emoji=""):
        if emoji:
            self.emoji_label.config(text=emoji)
        if text:
            self.message_label.config(text=text)

    def log_message(self, message, area="chat"):
        self.msg_queue.put((area, message))

    def process_messages(self):
        try:
            while True:
                area, message = self.msg_queue.get_nowait()
                if area == "chat":
                    if message.startswith("[你]"):
                        self.update_message(text=message[4:])
                    elif message.startswith("[AI]"):
                        self.update_message(text=message[4:])
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self.process_messages)

    def update_status(self, connection_status=None, voice_status=None):
        if not connection_status and not voice_status:
            return
        status_text = []
        if connection_status:
            status_text.append(connection_status)
        if voice_status:
            status_text.append(voice_status)
        self.status_label.config(text=" | ".join(status_text))

    async def setup_client(self):
        self.update_status(connection_status="正在连接...")
        config = ClientConfig(ws_url="ws://localhost:8000")
        audio_config = AudioConfig(sample_rate=16000, channels=1)
        self.client = XiaozhiClient(config, audio_config)
        
        self.client.enable_silence_detection(
            enabled=True,
            threshold=0.008,
            max_frames=200
        )
        
        async def on_tts_start(msg):
            self.client.pause_voice_input()
            self.update_status(voice_status="AI说话中")
            self.root.after(0, self.avatar.start_speaking_animation)

        async def on_tts_message(msg):
            state = msg.get('state')
            if state == 'sentence_start':
                text = msg.get('text', '').strip()
                if text:
                    self.log_message(f"[AI] {text}", "chat")
                    self.client.pause_voice_input()
                    self.update_status(voice_status="AI说话中")

        async def on_tts_end(msg):
            self.root.after(0, self.avatar.stop_speaking_animation)
            try:
                try:
                    await self.client.stop_voice_input()
                except Exception as e:
                    logger.warning(f"停止语音输入时出错: {e}")
                
                await asyncio.sleep(0.3)
                
                if self.is_running:
                    try:
                        await self.client.start_voice_input()
                        self.client._consecutive_silence_frames = 0
                        self.client._last_audio_sent_time = asyncio.get_event_loop().time()
                        self.update_status(voice_status="聆听中")
                    except Exception as e:
                        logger.error(f"启动语音输入失败: {e}")
            except Exception as e:
                logger.error(f"重启语音输入过程中出错: {e}")

        async def on_stt_message(msg_data):
            text = msg_data.get('text', '').strip()
            if text:
                self.log_message(f"[你] {text}", "chat")
                
        async def on_llm_message(msg_data):
            text = msg_data.get('text', '').strip()
            emoji = msg_data.get('emoji', '').strip()
            if text:
                self.log_message(f"[AI] {text}", "chat")
            if emoji:
                self.root.after(0, lambda: self.emoji_label.config(text=emoji))
            
        async def on_connection_lost(reason):
            self.update_status(connection_status="已断开")
            try:
                await self.client.connect()
                self.update_status(connection_status="已连接")
                await self.client.start_voice_input()
            except Exception as e:
                self.update_status(connection_status=f"连接失败: {str(e)[:20]}")
        
        self.client.on_tts_start = on_tts_start
        self.client.on_tts_message = on_tts_message
        self.client.on_tts_end = on_tts_end
        self.client.on_connection_lost = on_connection_lost
        self.client.on_stt_message = on_stt_message
        self.client.on_llm_message = on_llm_message

    async def start_chat(self):
        try:
            logger.add("voice_assistant.log", rotation="10 MB", level="DEBUG")
            
            await self.client.connect()
            self.update_status(connection_status="已连接")
            
            await self.client.start_voice_input()
            self.update_status(voice_status="聆听中")
            
            self.root.after(0, lambda: self.reset_button.config(state="normal"))
            
            while self.is_running:
                await asyncio.sleep(0.1)
                
        except Exception as e:
            logger.error(f"错误: {e}", exc_info=True)
            self.update_status(connection_status=f"错误: {str(e)[:20]}")
        finally:
            if self.client:
                await self.client.close()
            self.update_status(connection_status="未连接")
            self.is_running = False
            self.root.after(0, lambda: self.start_button.config(text="开始对话"))
            self.root.after(0, lambda: self.reset_button.config(state="disabled"))
            self.root.after(0, self.avatar.stop_speaking_animation)

    def toggle_chat(self):
        if not self.is_running:
            self.is_running = True
            self.start_button.config(text="停止对话")
            
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            
            async def start_chat_async():
                await self.setup_client()
                await self.start_chat()
            
            def run_async():
                self.loop.run_until_complete(start_chat_async())
                self.loop.close()
            
            self.client_task = threading.Thread(target=run_async)
            self.client_task.start()
        else:
            self.is_running = False
            self.start_button.config(text="开始对话")
            if self.client:
                asyncio.run_coroutine_threadsafe(self.client.close(), self.loop)

    async def reset_chat_async(self):
        if self.client:
            await self.client.abort()
            await self.client.stop_voice_input()
            await asyncio.sleep(0.3)
            await self.client.start_voice_input()
            self.client._consecutive_silence_frames = 0
            self.client._last_audio_sent_time = asyncio.get_event_loop().time()
            self.update_status(voice_status="聆听中")
            self.update_message(text="", emoji="")  # Clear message display

    def reset_chat(self):
        if self.is_running and self.loop:
            asyncio.run_coroutine_threadsafe(self.reset_chat_async(), self.loop)

    def on_closing(self):
        try:
            if self.is_running:
                self.is_running = False
                if self.client:
                    if self.loop and self.loop.is_running():
                        asyncio.run_coroutine_threadsafe(self.client.close(), self.loop)
                        pending = asyncio.all_tasks(self.loop)
                        for task in pending:
                            task.cancel()
                        if self.client_task and self.client_task.is_alive():
                            self.client_task.join(timeout=2.0)
        except Exception as e:
            logger.error(f"关闭时发生错误: {e}", exc_info=True)
        finally:
            try:
                self.root.quit()
                self.root.destroy()
            except Exception as e:
                logger.error(f"关闭窗口时发生错误: {e}", exc_info=True)

def main():
    try:
        root = tk.Tk()
        app = VoiceAssistantGUI(root)
        root.mainloop()
    except Exception as e:
        logger.error(f"程序异常退出: {e}", exc_info=True)

if __name__ == "__main__":
    main()
