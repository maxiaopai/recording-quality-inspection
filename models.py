from sqlmodel import SQLModel, Field
from datetime import datetime
from typing import Optional


class Task(SQLModel, table=True):
    """质检任务：一个任务可包含多个录音"""
    __tablename__ = "tasks"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    status: str = "upload"  # upload / processing / completed / failed
    progress: int = 0  # 整体进度（所有录音的平均进度）
    progress_label: str = "等待处理"
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class Recording(SQLModel, table=True):
    """录音：属于某个任务，包含音频、转写、质检结果"""
    __tablename__ = "recordings"

    id: Optional[int] = Field(default=None, primary_key=True)
    task_id: int  # 外键，关联到 Task
    audio_filename: str
    audio_path: str
    transcript_json: Optional[str] = None  # JSON string
    status: str = "upload"  # upload / transcribing / quality_checking / completed / failed
    progress: int = 0
    progress_label: str = "等待处理"
    quality_report: Optional[str] = None  # JSON string
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class SensitiveWord(SQLModel, table=True):
    __tablename__ = "sensitive_words"

    id: Optional[int] = Field(default=None, primary_key=True)
    word: str = Field(unique=True)
    category: Optional[str] = ""
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class QualityRule(SQLModel, table=True):
    """质检规则"""
    __tablename__ = "quality_rules"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str  # 规则名称
    description: Optional[str] = ""  # 规则描述
    rule_type: str = "keyword"  # keyword / context / llm
    keywords: Optional[str] = ""  # 关键词列表，JSON格式
    context_words: Optional[str] = ""  # 上下文判定词，JSON格式
    action: str = "flag"  # flag(标记为疑似违规) / reject(标记为不合格) / ignore(忽略)
    enabled: bool = True  # 是否启用
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class QualityRuleHistory(SQLModel, table=True):
    """质检规则编辑历史记录"""
    __tablename__ = "quality_rule_history"

    id: Optional[int] = Field(default=None, primary_key=True)
    rule_id: int  # 关联到 QualityRule
    action: str  # create / update / delete
    snapshot: str  # 变更前后的JSON快照
    operator: str = "system"  # 操作人
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())