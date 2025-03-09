import asyncio
from xiaozhi_client import XiaozhiClient, ClientConfig, AudioConfig
from loguru import logger

async def start_voice_chat(
    ws_url: str = "wss://api.tenclass.net/xiaozhi/v1/", #感谢虾哥提供的服务
    #ws_url: str = "ws://localhost:8000",
    sample_rate: int = 16000,
    channels: int = 1
):
    """启动语音对话"""
    config = ClientConfig(ws_url=ws_url)
    audio_config = AudioConfig(sample_rate=sample_rate, channels=channels)
    client = XiaozhiClient(config, audio_config)
    
    # 配置静音检测
    client.enable_silence_detection(enabled=True, threshold=0.01, max_frames=150)
    
    # 设置回调
    async def on_tts_start(msg):
        print("\n[系统] AI开始说话...")
        client.pause_voice_input()
        
    async def on_tts_end(msg):
        print("\n[系统] AI说话结束")
        print("\n[系统] 继续聆听中... (q:退出 r:重置对话)")
        # 添加延迟，确保系统有时间处理状态转换
        await asyncio.sleep(0.5)  # 增加延迟时间
        client.resume_voice_input()
        # 重置静音检测计数器
        client._consecutive_silence_frames = 0
        client._last_audio_sent_time = asyncio.get_event_loop().time()
        logger.debug("已恢复语音输入，重置静音检测")
        
    # 添加连接断开回调
    async def on_connection_lost(reason):
        print(f"\n[系统] 连接断开: {reason}")
        print("[系统] 尝试重新连接...")
        try:
            await client.connect()
            print("[系统] 重新连接成功")
            await client.start_voice_input()
        except Exception as e:
            print(f"[系统] 重新连接失败: {e}")
    
    client.on_tts_start = on_tts_start
    client.on_tts_end = on_tts_end
    client.on_connection_lost = on_connection_lost
    
    try:
        # 配置日志级别以显示更多调试信息
        logger.add("audio_chat.log", rotation="10 MB", level="DEBUG")
        
        await client.connect()
        print("\n[系统] 已连接到服务器")
        
        # 启动语音输入
        await client.start_voice_input()
        print("[系统] 开始对话... (q:退出 r:重置对话 s:统计信息)\n")
        
        # 命令处理循环
        while True:
            cmd = await asyncio.get_event_loop().run_in_executor(None, input, "")
            cmd = cmd.strip().lower()
            
            if cmd == 'q':
                break
            elif cmd == 'r':
                print("\n[系统] 重置对话...")
                await client.abort()
                # 重置语音输入 - 完全停止并重新启动
                await client.stop_voice_input()
                await asyncio.sleep(1.0)  # 增加等待时间，确保彻底清理旧状态
                # 重新创建并启动语音输入
                await client.start_voice_input()
                # 重置静音检测
                client._consecutive_silence_frames = 0
                client._last_audio_sent_time = asyncio.get_event_loop().time()
                print("[系统] 对话已重置，开始新对话...")
                
    except Exception as e:
        logger.error(f"错误: {e}", exc_info=True)
    finally:
        await client.close()
        print("\n[系统] 程序已退出")

def main():
    try:
        asyncio.run(start_voice_chat())
    except KeyboardInterrupt:
        print("\n[系统] 程序已停止")

if __name__ == "__main__":
    main()
