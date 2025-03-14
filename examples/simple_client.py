import asyncio
import sys
from xiaozhi_client import XiaozhiClient, ClientConfig
from concurrent.futures import ThreadPoolExecutor

def print_message(message: str, end='\n'):
    """打印消息到控制台"""
    sys.stdout.write(message + end)
    sys.stdout.flush()

def prompt_input():
    """显示输入提示"""
    print_message("\n请输入消息(输入q退出)：", end='')


async def main():
    # 配置客户端
    config = ClientConfig(
        #ws_url="wss://api.tenclass.net/xiaozhi/v1/"  #感谢虾哥提供的服务
        ws_url = "ws://localhost:8000",
    )
    
    client = XiaozhiClient(config)
    #client.device_id = "xxx" # 已注册得设备id

    # 设置回调函数
    async def on_tts_start(msg):
        print_message("\n[系统] 开始播放语音...")
        
    async def on_tts_end(msg):
        print_message("\n[系统] 语音播放完成")
        prompt_input()
        
    client.on_tts_start = on_tts_start
    client.on_tts_end = on_tts_end
    
    try:
        await client.connect()
    except (websockets.exceptions.WebSocketException, ConnectionError) as e:
        print_message(f"\n[系统] 连接失败: {str(e)}")
        return
    
    # 创建输入循环
    executor = ThreadPoolExecutor(max_workers=1)
    loop = asyncio.get_event_loop()
    
    prompt_input()
    
    try:
        while True:
            message = await loop.run_in_executor(executor, sys.stdin.readline)
            message = message.strip()
            
            if message.lower() == 'q':
                break
                
            if message:
                print_message(f"\n[用户] {message}")
                await client.send_txt_message(message)
                
                # 等待音频播放完成
                while client.is_playing.is_set():
                    await asyncio.sleep(0.1)
    finally:
        await client.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n程序已退出")
