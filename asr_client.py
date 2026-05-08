"""ASR 客户端 - 调用外部语音识别服务"""
import requests
from typing import Optional

ASR_BASE_URL = "http://192.168.3.95:18303"

async def transcribe_audio(audio_path: str) -> Optional[dict]:
    """调用ASR服务转写音频

    Args:
        audio_path: 音频文件路径

    Returns:
        {"segments": [{"start": float, "end": float, "text": str, "speaker": str}]}
        或 None（失败）
    """
    try:
        with open(audio_path, "rb") as f:
            files = {"file": (audio_path.split("/")[-1], f, "audio/x-wav")}
            data = {
                "hotwords": "占位",
            }
            resp = requests.post(
                f"{ASR_BASE_URL}/audio/predict",
                files=files,
                data=data,
                timeout=300,
            )
            resp.raise_for_status()
            result = resp.json()

        if result.get("code") != 200:
            print(f"ASR 返回错误: {result.get('msg')}")
            return None

        # 将ASR输出转换为统一格式
        segments = []
        for item in result.get("data", []):
            segments.append({
                "start": item.get("start", 0) / 1000.0,  # ms → s
                "end": item.get("end", 0) / 1000.0,
                "text": item.get("text", "").strip(),
                "speaker": f"spk_{item.get('spk', 0)}",
            })

        return {"segments": segments, "speakers": []}

    except requests.exceptions.RequestException as e:
        print(f"ASR 请求失败: {e}")
        return None
    except Exception as e:
        print(f"ASR 处理异常: {e}")
        return None
