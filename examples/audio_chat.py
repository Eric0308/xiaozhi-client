import asyncio
from xiaozhi_client import XiaozhiClient, ClientConfig, AudioConfig
from loguru import logger

async def start_voice_chat(
    ws_url: str = "ws://localhost:8000",
    sample_rate: int = 16000,
    channels: int = 1
):
    """启动语音对话"""
    config = ClientConfig(ws_url=ws_url)
    audio_config = AudioConfig(sample_rate=sample_rate, channels=channels)
    client = XiaozhiClient(config, audio_config)
    
    # 设置回调
    async def on_tts_start(msg):
        print("\n[系统] AI开始说话...")
        client.pause_voice_input()
        
    async def on_tts_end(msg):
        print("\n[系统] AI说话结束")
        print("\n[系统] 继续聆听中... (q:退出 r:重置对话)")
        client.resume_voice_input()
        
    client.on_tts_start = on_tts_start
    client.on_tts_end = on_tts_end
    
    try:
        await client.connect()
        print("\n[系统] 已连接到服务器")
        
        # 启动语音输入
        await client.start_voice_input()
        print("[系统] 开始对话... (q:退出 r:重置对话)\n")
        
        # 命令处理循环
        while True:
            cmd = await asyncio.get_event_loop().run_in_executor(None, input, "")
            cmd = cmd.strip().lower()
            
            if cmd == 'q':
                break
            elif cmd == 'r':
                print("\n[系统] 重置对话...")
                await client.abort()
                await client.start_listen()
                client.resume_voice_input()
                
    except Exception as e:
        logger.error(f"错误: {e}")
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
