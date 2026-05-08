"""
大模型质检客户端 — 直接调用 LLM API 进行语义分析。
"""
import json
import requests
from typing import Optional

# ── LLM 配置（与 OpenClaw 共享的 ToGuide 接口） ──────────────────────────
LLM_BASE_URL = "https://kb.toguide.cn:20443"
LLM_API_KEY = "sk-Ocy2rXa6sciSQ991cKABFUDKuUy1te59Z4sPUe0T4BntKeMT"  # TODO: 后续移至环境变量
LLM_MODEL = "qwen3.6-plus"


def analyze_with_llm(sentence: str, sensitive_word: str, context: str = "",
                     is_expert: bool = False, timeout: float = 30.0) -> dict:
    """
    调用大模型 API 分析含敏感词的句子是否违规。

    返回格式:
    {
        "qualified": "合格" | "疑似违规" | "不合格",
        "reason": "分析理由",
        "severity": "low" | "medium" | "high"
    }
    """
    expert_tag = "【发言人身份】评标专家\n" if is_expert else ""

    system_prompt = """你是一名评标质检专家，负责分析评标录音转写文本中是否存在违规行为。

## 判定标准
请结合**敏感词 + 整段对话上下文**，做出三级判定：

- **合格**（绿色）：明确不是违规。例如家人/生活场景下简单报备工作状态（"我在封闭评标"、"今天评审"），属于正常交流，无泄密意图。
- **疑似违规**（黄色）：拿不准，存在违规可能性，需要人工复核。
- **不合格**（红色）：明确违规。包括但不限于：
  - 评标专家向非相关人员透露评标信息（投标单位情况、评分结果等）
  - 评分操控（调整打分、倾向性评分、淘汰特定单位等）
  - 请托说情（打招呼、托人、找关系等）
  - 利益输送（事后感谢、请客吃饭、回扣等）
  - 串通投标（内定、串标、围标等）

## 输出要求
- 必须严格输出JSON，格式如下：
{"qualified": "合格/疑似违规/不合格", "reason": "简要分析理由（50字以内）", "severity": "low/medium/high"}
- reason字段用中文
- 不要输出任何其他内容，不要markdown包裹"""

    user_prompt = f"""请分析以下评标对话转写文本。

## 敏感词
{sensitive_word}

## 当前句子
{sentence}

## 上下文（前后对话）
{context or "无"}

{expert_tag}
请结合上下文判断该句子是否违规，输出JSON。"""

    try:
        resp = requests.post(
            f"{LLM_BASE_URL}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {LLM_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": LLM_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.1,
                "max_tokens": 500,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"].strip()

        # 清理可能的 markdown 包裹
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()

        result = json.loads(content)
        return {
            "qualified": result.get("qualified", "疑似违规"),
            "reason": result.get("reason", "大模型分析完成"),
            "severity": result.get("severity", "medium"),
        }

    except Exception as e:
        return {
            "qualified": "LLM调用失败",
            "reason": f"大模型调用异常：{str(e)[:100]}",
            "severity": "medium",
        }
