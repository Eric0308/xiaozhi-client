import os
import datetime

def save_wav(audio_dir, pcm_buffer):
        """异步保存完整的WAV文件"""
        if len(pcm_buffer) > 0:
            try:
                # Create WAV header
                wav_data = _create_wav_header(len(pcm_buffer) // 2)
                
                # Write WAV file
                file_name = os.path.join(audio_dir, f"{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}.wav")
                with open(file_name, 'wb') as f:
                    f.write(wav_data)
                    f.write(pcm_buffer)

            except Exception as e:
                print(f"保存WAV文件错误: {e}")

def _create_wav_header(total_samples):
    """创建WAV文件头"""
    header = bytearray(44)
    
    # RIFF header
    header[0:4] = b'RIFF'
    header[4:8] = (total_samples * 2 + 36).to_bytes(4, 'little')  # File size
    header[8:12] = b'WAVE'
    
    # fmt chunk
    header[12:16] = b'fmt '
    header[16:20] = (16).to_bytes(4, 'little')  # Chunk size
    header[20:22] = (1).to_bytes(2, 'little')  # Audio format (PCM)
    header[22:24] = (1).to_bytes(2, 'little')  # Num channels
    header[24:28] = (16000).to_bytes(4, 'little')  # Sample rate
    header[28:32] = (32000).to_bytes(4, 'little')  # Byte rate
    header[32:34] = (2).to_bytes(2, 'little')  # Block align
    header[34:36] = (16).to_bytes(2, 'little')  # Bits per sample
    
    # data chunk
    header[36:40] = b'data'
    header[40:44] = (total_samples * 2).to_bytes(4, 'little')  # Data size
    
    return header