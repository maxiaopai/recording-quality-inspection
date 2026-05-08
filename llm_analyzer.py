from typing import Optional

# ── 语境场景词 ──────────────────────────────────────────────────────────
# 家人生活场景：这些词出现说明是私人/生活语境，即使含敏感词也通常合格
FAMILY_LIFE_INDICATORS = [
    "爸爸", "妈妈", "爸", "妈", "爹", "娘", "父亲", "母亲",
    "老婆", "老公", "媳妇", "丈夫", "妻子", "爱人", "对象",
    "儿子", "女儿", "闺女", "小子", "孩子", "娃娃", "宝宝", "宝贝",
    "爷爷", "奶奶", "外公", "外婆", "姥姥", "姥爷",
    "哥哥", "弟弟", "姐姐", "妹妹", "兄弟", "姐妹",
    "亲戚", "朋友", "邻居", "同学", "同事",
    "生日", "过年", "节日", "礼物", "奖励", "庆祝", "恭喜",
    "吃饭", "回家", "陪你", "接你", "等你", "想你",
    "照顾孩子", "辅导作业", "带孩子", "上学", "幼儿园",
    "买菜", "做饭", "逛街", "旅游", "旅行", "出去玩",
    "周末", "放假", "下班", "明天见", "明天回", "回去",
    "身体", "健康", "生病", "吃药", "医院",
    "工资", "奖金", "加班", "上班", "工作", "工资条",
    "房贷", "车贷", "装修", "搬家", "买房", "买车",
    "小礼物", "小意思", "意思一下", "一点心意",
    "给你买", "送你", "带给你", "寄给你",
    # 补充：家庭生活场景
    "家里", "家里事", "家里活", "阳台", "衣服", "收衣服",
    "门窗", "锁门", "锁好", "收好", "挂念", "担心",
    "聚餐", "辛苦你", "放心", "不勉强", "认真工作",
    "公园", "周末", "老人",
]

# 评标业务场景：这些词说明是在评标工作语境中，敏感词风险较高
BID_EVALUATION_CONTEXT = [
    "评标", "评审", "打分", "打分", "投标", "投标方", "投标单位", "投标人", "招标",
    "专家费", "评标费", "评审费", "评标委员会", "评标室",
    "封闭评标", "封闭评标", "评标纪律", "评标规矩", "评标流程",
    "中标", "内定", "串标", "围标", "陪标", "泄密",
    "评分标准", "评分办法", "评审标准", "评审办法",
    "技术标", "商务标", "价格标", "投标文件",
    "招标代理", "招标人", "投标人代表", "评标专家",
    "评委", "评审专家", "评标委员", "专家组长",
    "照顾", "倾斜", "优势", "高分", "低分", "加分", "减分",
    "帮忙", "打招呼", "托人", "找关系", "请托",
    "好处费", "回扣", "感谢费", "辛苦费", "答谢",
    "中标后", "中标以后", "评标结束后", "评标之后",
    "这个项目", "这个标", "这个工程", "这个公司",
    "事成之后", "事后", "搞定", "安排好", "安排好",
    "综合评分", "综合实力", "淘汰", "淘汰劣势", "淘汰单位",
    "短板", "合理方向", "操作一下", "调整评分",
    "参评", "参评单位", "评分数", "出评分数",
]

# 明确违规词：直接判不合格
VIOLATION_WORDS = [
    "受贿", "行贿", "好处费", "回扣",
    "内定", "串标", "围标", "陪标", "泄密",
]

# 评分操控类
MANIPULATION_WORDS = ["倾斜", "优势分", "多给", "多拿", "倾向性", "打高分", "打低分", "照顾",
                     "淘汰", "淘汰劣势", "合理方向", "调整评分", "综合评分", "评分调整"]

# 请托说情类
FAVOR_WORDS = ["打招呼", "托人", "找关系"]

# 利益承诺类
PROMISE_WORDS = ["感谢", "答谢", "辛苦费", "意思意思", "事后"]


def _is_family_life_context(text: str) -> bool:
    """判断是否是家人/生活场景"""
    if not text:
        return False
    count = sum(1 for w in FAMILY_LIFE_INDICATORS if w in text)
    return count >= 2  # 至少出现2个生活场景词，才认为是生活语境


def _is_bid_evaluation_context(text: str) -> bool:
    """判断是否是评标/工作场景"""
    if not text:
        return False
    count = sum(1 for w in BID_EVALUATION_CONTEXT if w in text)
    return count >= 2  # 至少出现2个评标场景词，才认为是工作语境


def analyze_segment(sentence: str, sensitive_word: str, context: str = "", is_expert: bool = False) -> dict:
    """
    分析单个句子是否因含敏感词而不合格。

    核心原则：
    1. 含敏感词 ≠ 不合格，必须结合语义判断
    2. 先判断语境：家人/生活场景 vs 评标/工作场景
    3. 同一敏感词在不同语境下结论可能不同
    4. 三级判定：合格 / 疑似违规 / 不合格

    判定标准：
    - 合格：明确不是违规，属于正常评标讨论、生活场景用语
    - 疑似违规：拿不准，存在违规可能性，需要人工复核
    - 不合格：明确违规，属于投标串通、评分操控、利益输送等
    """
    word = sensitive_word.strip()
    full_text = sentence + " " + (context or "")

    # ── Step 1: 语境判断 ──────────────────────────────────────────────
    is_family = _is_family_life_context(full_text)
    is_bid = _is_bid_evaluation_context(full_text)

    # 如果是家人/生活场景，且不是明确的严重违法，通常合格
    if is_family and not is_bid:
        # 生活场景下，只有极少数严重违法词才判不合格
        severe_words = ["受贿", "行贿", "贪污", "挪用", "侵占", "盗窃"]
        if word in severe_words:
            return {
                "qualified": "不合格",
                "reason": f"即使在生活场景中提及严重违法行为（'{word}'），仍需关注",
                "severity": "high"
            }
        return {
            "qualified": "合格",
            "reason": f"语境为家人/生活交流（提及家人称呼、日常生活等），'{word}'在此为正常表达，无违规意图",
            "severity": "low"
        }

    # 如果是评标场景，需要更严格判断
    # 既非明确家人场景，也非明确评标场景，进入分类判断

    # ── Step 2: 分类判断 ──────────────────────────────────────────────

    # 第一类：明确违规，直接不合格
    if word in VIOLATION_WORDS:
        # 贿赂类词汇，如果在生活场景中且无评标上下文，放宽
        if is_family and not is_bid and word in ["红包"]:
            return {
                "qualified": "合格",
                "reason": f"语境为家人/生活交流，'{word}'在此为正常表达，无违规意图",
                "severity": "low"
            }
        # 明确违规
        return {
            "qualified": "不合格",
            "reason": f"句子涉及违规内容（'{word}'），违反评标廉洁纪律或法律法规",
            "severity": "high"
        }

    # 第二类：评分操控类（需结合评标上下文）
    if word in MANIPULATION_WORDS:
        is_scoring_context = any(c in full_text for c in ["评分", "打分", "评审", "评标", "分值", "分数", "优势", "劣势", "排名"])
        if is_scoring_context and is_bid:
            # 明确在评标语境中操控评分 → 不合格
            return {
                "qualified": "不合格",
                "reason": f"在评标语境中涉及评分倾向性言论（'{word}'），违反评标公正原则",
                "severity": "high"
            }
        elif is_scoring_context:
            # 有评分语境但不是明确评标场景 → 疑似
            return {
                "qualified": "疑似违规",
                "reason": f"句子涉及评分相关（'{word}'），存在评分操控嫌疑，建议人工复核",
                "severity": "medium"
            }
        return {
            "qualified": "合格",
            "reason": f"'{word}'在本句中无明显违规意图",
            "severity": "low"
        }

    # 第三类：请托说情类
    if word in FAVOR_WORDS:
        if is_bid and any(c in full_text for c in ["评标", "评审", "打分", "投标", "评分", "项目"]):
            return {
                "qualified": "不合格",
                "reason": f"在评标语境中涉及请托行为（'{word}'），违反评标独立性原则",
                "severity": "high"
            }
        elif is_bid:
            return {
                "qualified": "疑似违规",
                "reason": f"句子出现在工作语境中涉及'{word}'，存在请托嫌疑，建议人工复核",
                "severity": "medium"
            }
        return {
            "qualified": "合格",
            "reason": f"'{word}'在本句中为一般性陈述，无违规意图",
            "severity": "low"
        }

    # 第四类：利益承诺类
    if word in PROMISE_WORDS:
        if is_bid and any(c in full_text for c in ["评标", "评审", "打分", "帮忙", "照顾", "分数"]):
            return {
                "qualified": "不合格",
                "reason": f"在评标语境中疑似利益交换或请托（'{word}'），存在廉洁风险",
                "severity": "high"
            }
        elif is_bid:
            return {
                "qualified": "疑似违规",
                "reason": f"句子出现在工作语境中涉及'{word}'，存在利益输送嫌疑，建议人工复核",
                "severity": "medium"
            }
        return {
            "qualified": "合格",
            "reason": f"'{word}'在本句中为一般性用语，无违规意图",
            "severity": "low"
        }

    # 保密类敏感词：封闭式评标、封闭评标、评标纪律、独立评审等
    # 在评标语境中向外部人员提及 → 涉嫌泄密
    confidentiality_words = ["封闭式评标", "封闭评标", "评标纪律", "评标规矩", "独立评审",
                             "封闭", "不能用手机", "评标管控", "评标封闭"]
    if word in confidentiality_words and is_bid:
        # 检查是否有向外部人员透露信息的暗示
        leak_hints = ["跟你说", "心里有数", "之前提过", "不方便细说", "不多说",
                      "不多问", "知道就行", "你懂的", "那家", "哪家",
                      "聚一聚", "吃饭", "事成之后", "事后", "搞定"]
        leak_score = sum(1 for w in leak_hints if w in full_text)
        # 有泄密暗示 → 不合格
        if leak_score >= 1:
            return {
                "qualified": "不合格",
                "reason": f"在评标语境中向外部人员提及'{word}'，并伴有泄密暗示（'{[w for w in leak_hints if w in full_text][0]}'），违反评标保密规定",
                "severity": "high"
            }
        # 家人/生活场景下简单报备工作状态 → 合格
        if is_family:
            return {
                "qualified": "合格",
                "reason": f"语境为家人/生活交流，'{word}'在此为向家人说明工作状态，无泄密意图",
                "severity": "low"
            }
        # 无泄密暗示但提及保密要求 → 疑似违规
        return {
            "qualified": "疑似违规",
            "reason": f"在评标语境中提及'{word}'，建议人工确认是否存在泄密行为",
            "severity": "medium"
        }

    # 红包特殊处理
    if word == "红包":
        if is_family and not is_bid:
            return {
                "qualified": "合格",
                "reason": f"语境为家人/生活交流，'{word}'在此为正常表达，无违规意图",
                "severity": "low"
            }
        elif is_bid:
            return {
                "qualified": "不合格",
                "reason": f"在评标语境中提及'{word}'，涉嫌利益输送",
                "severity": "high"
            }
        return {
            "qualified": "疑似违规",
            "reason": f"'{word}'出现语境不明确，建议人工复核",
            "severity": "medium"
        }

    # 第五类：其他含敏感词 → 默认合格
    return {
        "qualified": "合格",
        "reason": f"'{word}'在本句中为正常表达，无违规意图",
        "severity": "low"
    }


def detect_expert_speaker(segments: list) -> Optional[str]:
    """
    根据对话内容推断哪个发言人是评标专家。

    评标专家的特征：
    - 主动提及"评标"、"封闭评标"、"评审"、"专家费"、"评标纪律"等
    - 主动提及自己是"评标专家"、"我在评标"
    - 说自己"封闭"、"不能用手机"（评标管理规定）
    - 提到"廉洁"、"纪律"、"公正"等（角色职责）

    逻辑：遍历所有句子，统计每个发言人含专家特征词的句子数量
    返回得分最高的发言人 id（S1/S2），无法判断时返回 None
    """
    expert_indicators = [
        "评标", "评审", "封闭评标", "评标纪律", "评标规矩",
        "专家费", "评审专家", "评标专家", "廉洁", "公正",
        "封闭", "不能用手机", "独立评审", "评审标准",
        "我在评标", "评标工作", "评标流程", "评审环节",
        "打分会", "评标管控", "评标封闭",
        "投标方", "投标单位", "投标", "综合评分", "综合实力",
        "淘汰劣势", "淘汰", "调整评分", "往合理方向调整",
        "评分调整", "评分数", "操作一下", "稳了",
        "封闭式评标", "参评", "评标委员会", "评委"
    ]

    scores = {}
    for seg in segments:
        speaker = seg.get("speaker", "")
        text = seg.get("text", "")
        score = sum(1 for w in expert_indicators if w in text)
        if score > 0:
            scores[speaker] = scores.get(speaker, 0) + score

    if not scores:
        return None

    return max(scores, key=scores.get)
