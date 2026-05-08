import os
import uuid
import json
import asyncio
import shutil
import tempfile
import re
from typing import Optional
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, HTTPException, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import select
from docx import Document

from db import engine, create_db_and_tables, Session
from models import Task, Recording, SensitiveWord, QualityRule, QualityRuleHistory
from llm_analyzer import detect_expert_speaker
from llm_client import analyze_with_llm
from asr_client import transcribe_audio

# ── 应用初始化 ──────────────────────────────────────────────────────────────
app = FastAPI(title="评标质检系统")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")
app.mount("/www", StaticFiles(directory=str(BASE_DIR / "www")), name="www")

create_db_and_tables()

# ── DOCX 转写解析 ───────────────────────────────────────────────────────────
def parse_docx_transcript(content: bytes) -> dict:
    """解析评标转写 DOCX 文件，返回标准 transcript 结构。"""
    if isinstance(content, bytes):
        tmp = tempfile.NamedTemporaryFile(suffix='.docx', delete=False)
        tmp.write(content)
        tmp.flush()
        tmp.close()
        doc = Document(tmp.name)
        os.unlink(tmp.name)
    else:
        doc = content
    paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    if not paragraphs:
        return {"segments": [], "speakers": []}

    segments = []
    speaker_map = {}
    next_speaker_id = 1
    speaker_pattern = re.compile(r"^([^\s]+)\s+(\d{1,2}:\d{2}(?::\d{2})?)")

    pending_speaker = None
    pending_start = 0
    pending_texts = []

    def parse_time(t_str):
        parts = t_str.split(":")
        if len(parts) == 3:
            return int(parts[0])*3600 + int(parts[1])*60 + int(parts[2])
        elif len(parts) == 2:
            return int(parts[0])*60 + int(parts[1])
        return 0

    def flush_pending():
        nonlocal pending_speaker, pending_start, pending_texts, next_speaker_id, speaker_map
        if pending_speaker and pending_texts:
            sid = speaker_map.get(pending_speaker)
            if sid is None:
                sid = f"S{next_speaker_id}"
                speaker_map[pending_speaker] = sid
                next_speaker_id += 1
            segments.append({
                "speaker": sid,
                "name": pending_speaker,
                "start": pending_start,
                "text": "".join(pending_texts),
            })
        pending_speaker = None
        pending_start = 0
        pending_texts = []

    i = 1
    if len(paragraphs) > 1 and not speaker_pattern.match(paragraphs[1]):
        i = 2

    while i < len(paragraphs):
        line = paragraphs[i]
        m = speaker_pattern.match(line)
        if m:
            flush_pending()
            pending_speaker = m.group(1)
            pending_start = parse_time(m.group(2))
            rest = line[m.end():].strip()
            if rest:
                pending_texts.append(rest)
        else:
            if pending_speaker:
                pending_texts.append(line)
        i += 1

    flush_pending()
    speakers = [{"id": sid, "name": sname, "role": "other"}
                for sname, sid in speaker_map.items()]
    return {"segments": segments, "speakers": speakers}

# ── 路由：静态页面 ──────────────────────────────────────────────────────────
@app.get("/")
async def index():
    return RedirectResponse(url="/tasks")

@app.get("/tasks", response_class=HTMLResponse)
async def task_list_page():
    return FileResponse(str(BASE_DIR / "www" / "recording_list.html"))

@app.get("/task/{task_id}", response_class=HTMLResponse)
async def task_detail(task_id: int):
    return FileResponse(str(BASE_DIR / "www" / "task_detail.html"))

@app.get("/recording/{recording_id}", response_class=HTMLResponse)
async def recording_detail(recording_id: int):
    return FileResponse(str(BASE_DIR / "www" / "recording_detail.html"))

@app.get("/sensitive-words", response_class=HTMLResponse)
async def sensitive_words_page():
    return FileResponse(str(BASE_DIR / "www" / "sensitive_words.html"))

@app.get("/quality-rules", response_class=HTMLResponse)
async def quality_rules_page():
    return FileResponse(str(BASE_DIR / "www" / "quality_rules.html"))

# ── API: 任务 ─────────────────────────────────────────────────────────────────
@app.get("/api/tasks")
async def list_tasks():
    with Session(engine) as session:
        tasks = session.exec(select(Task).order_by(Task.created_at.desc())).all()
        result = []
        for t in tasks:
            # 统计该任务下的录音数量
            recordings = session.exec(select(Recording).where(Recording.task_id == t.id)).all()
            recording_count = len(recordings)
            # 计算整体进度
            if recordings:
                total_progress = sum(r.progress for r in recordings)
                avg_progress = total_progress // len(recordings)
                # 整体状态
                all_completed = all(r.status == "completed" for r in recordings)
                any_failed = any(r.status == "failed" for r in recordings)
                any_processing = any(r.status in ("upload", "transcribing", "quality_checking") for r in recordings)
                if any_failed:
                    overall_status = "failed"
                    overall_label = "部分失败"
                elif all_completed:
                    overall_status = "completed"
                    overall_label = "已完成"
                elif any_processing:
                    overall_status = "processing"
                    overall_label = "处理中"
                else:
                    overall_status = t.status
                    overall_label = t.progress_label
            else:
                avg_progress = 0
                overall_status = t.status
                overall_label = t.progress_label
            
            # 统计不合格、疑似违规和LLM调用失败的录音数
            def _count_qualified(r):
                if not r.quality_report:
                    return (0, 0, 0)
                report = json.loads(r.quality_report)
                bad = sum(1 for i in report.get("issues", []) if i.get("qualified") == "不合格")
                suspect = sum(1 for i in report.get("issues", []) if i.get("qualified") == "疑似违规")
                llm_failed = sum(1 for i in report.get("issues", []) if i.get("qualified") == "LLM调用失败")
                return (bad, suspect, llm_failed)
            
            counts = [_count_qualified(r) for r in recordings]
            bad_count = sum(1 for b, s, l in counts if b > 0)
            suspect_count = sum(1 for b, s, l in counts if s > 0 and b == 0 and l == 0)
            llm_failed_count = sum(1 for b, s, l in counts if l > 0 and b == 0 and s == 0)
            
            result.append({
                "id": t.id,
                "name": t.name,
                "status": overall_status,
                "progress_label": overall_label,
                "progress": avg_progress,
                "recording_count": recording_count,
                "bad_count": bad_count,
                "suspect_count": suspect_count,
                "llm_failed_count": llm_failed_count,
                "created_at": t.created_at,
                "updated_at": t.updated_at,
            })
        return result

@app.post("/api/tasks")
async def create_task(
    name: str = Form(""),
    audio_files: list[UploadFile] = File([]),
    transcript_files: list[UploadFile] = File([]),
):
    if not audio_files:
        raise HTTPException(status_code=400, detail="请至少上传一个音频文件")
    if len(transcript_files) != len(audio_files):
        raise HTTPException(status_code=400, detail="音频文件和转写文件数量必须一致")

    # 创建任务
    with Session(engine) as session:
        task_name = name.strip() or f"质检任务 {datetime.now().strftime('%Y%m%d_%H%M')}"
        task = Task(name=task_name, status="upload", progress=0, progress_label="等待处理")
        session.add(task)
        session.commit()
        session.refresh(task)
        task_id = task.id

    # 为每个音频创建 Recording
    for idx, audio in enumerate(audio_files):
        rec_id = str(uuid.uuid4())[:8]
        audio_dir = UPLOAD_DIR / str(task_id) / rec_id
        audio_dir.mkdir(parents=True, exist_ok=True)
        audio_path = audio_dir / audio.filename
        with open(audio_path, "wb") as f:
            shutil.copyfileobj(audio.file, f)

        # 解析转写文件
        transcript_file = transcript_files[idx] if idx < len(transcript_files) else None
        if transcript_file:
            content = await transcript_file.read()
            if transcript_file.filename.lower().endswith('.json'):
                try:
                    transcript_data = json.loads(content)
                except json.JSONDecodeError:
                    transcript_data = {"segments": [], "speakers": []}
            elif transcript_file.filename.lower().endswith(('.doc', '.docx')):
                transcript_data = parse_docx_transcript(content)
            else:
                transcript_data = {"segments": [], "speakers": []}
        else:
            transcript_data = {"segments": [], "speakers": []}

        with Session(engine) as session:
            rec = Recording(
                task_id=task_id,
                audio_filename=audio.filename,
                audio_path=str(audio_path),
                transcript_json=json.dumps(transcript_data, ensure_ascii=False),
                status="upload",
                progress=0,
                progress_label="等待处理",
            )
            session.add(rec)
            session.commit()
            session.refresh(rec)
            # 触发异步质检
            asyncio.create_task(run_quality_check(rec.id))

    return {"id": task_id, "recording_count": len(audio_files)}

@app.get("/api/tasks/{task_id}")
async def get_task(task_id: int):
    with Session(engine) as session:
        task = session.get(Task, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        
        recordings = session.exec(select(Recording).where(Recording.task_id == task_id)).all()
        
        rec_list = []
        for r in recordings:
            audio_url = None
            if r.audio_path:
                parts = str(r.audio_path).split("/uploads/")
                audio_url = "/uploads/" + parts[1] if len(parts) > 1 else "/uploads/" + Path(r.audio_path).name
            
            rec_list.append({
                "id": r.id,
                "audio_filename": r.audio_filename,
                "audio_url": audio_url,
                "status": r.status,
                "progress": r.progress,
                "progress_label": r.progress_label,
                "transcript": json.loads(r.transcript_json) if r.transcript_json else {"segments": [], "speakers": []},
                "quality_report": json.loads(r.quality_report) if r.quality_report else None,
                "created_at": r.created_at,
            })
        
        return {
            "id": task.id,
            "name": task.name,
            "status": task.status,
            "progress": task.progress,
            "progress_label": task.progress_label,
            "recordings": rec_list,
            "created_at": task.created_at,
        }

@app.delete("/api/tasks/{task_id}")
async def delete_task(task_id: int):
    with Session(engine) as session:
        task = session.get(Task, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        
        # 删除关联的录音文件
        recordings = session.exec(select(Recording).where(Recording.task_id == task_id)).all()
        for r in recordings:
            audio_dir = Path(r.audio_path).parent if r.audio_path else None
            if audio_dir and audio_dir.exists():
                shutil.rmtree(audio_dir, ignore_errors=True)
        
        # 删除录音记录
        for r in recordings:
            session.delete(r)
        
        session.delete(task)
        session.commit()
        return {"ok": True}

# ── API: 录音 ─────────────────────────────────────────────────────────────────
@app.get("/api/recordings/{recording_id}")
async def get_recording(recording_id: int):
    with Session(engine) as session:
        rec = session.get(Recording, recording_id)
        if not rec:
            raise HTTPException(status_code=404, detail="录音不存在")
        
        audio_url = None
        if rec.audio_path:
            parts = str(rec.audio_path).split("/uploads/")
            audio_url = "/uploads/" + parts[1] if len(parts) > 1 else "/uploads/" + Path(rec.audio_path).name
        
        return {
            "id": rec.id,
            "task_id": rec.task_id,
            "audio_filename": rec.audio_filename,
            "audio_url": audio_url,
            "status": rec.status,
            "progress": rec.progress,
            "progress_label": rec.progress_label,
            "transcript": json.loads(rec.transcript_json) if rec.transcript_json else {"segments": [], "speakers": []},
            "quality_report": json.loads(rec.quality_report) if rec.quality_report else None,
            "created_at": rec.created_at,
        }

# ── 重新质检 ─────────────────────────────────────────────────────────────
@app.post("/api/recordings/{recording_id}/recheck")
async def recheck_recording(recording_id: int):
    """重新执行质检（使用最新规则）"""
    with Session(engine) as session:
        rec = session.get(Recording, recording_id)
        if not rec:
            raise HTTPException(status_code=404, detail="录音不存在")
    asyncio.create_task(run_quality_check(recording_id, recheck=True))
    return {"ok": True, "message": "重新质检已启动"}

# ── 辅助函数 ─────────────────────────────────────────────────────────────
async def _create_recording_record(task_id: int, filename: str, audio_file: UploadFile) -> int:
    """创建录音记录并保存音频文件，返回录音ID"""
    rec_id = str(uuid.uuid4())[:8]
    audio_dir = UPLOAD_DIR / str(task_id) / rec_id
    audio_dir.mkdir(parents=True, exist_ok=True)
    audio_path = audio_dir / filename
    with open(audio_path, "wb") as f:
        shutil.copyfileobj(audio_file.file, f)
    
    with Session(engine) as session:
        rec = Recording(
            task_id=task_id,
            audio_filename=filename,
            audio_path=str(audio_path),
            transcript_json=json.dumps({"segments": [], "speakers": []}, ensure_ascii=False),
            status="upload",
            progress=0,
            progress_label="等待处理",
        )
        session.add(rec)
        session.commit()
        session.refresh(rec)
        return rec.id

async def _parse_transcript(transcript_file: UploadFile) -> dict:
    """解析转写文件，返回标准transcript结构"""
    content = await transcript_file.read()
    if transcript_file.filename.lower().endswith('.json'):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {"segments": [], "speakers": []}
    elif transcript_file.filename.lower().endswith(('.doc', '.docx')):
        return parse_docx_transcript(content)
    else:
        return {"segments": [], "speakers": []}

# ── API: 录音列表（扁平化） ─────────────────────────────────────────────────
@app.get("/api/recordings")
async def list_recordings():
    """获取所有录音列表（跨任务）"""
    with Session(engine) as session:
        recordings = session.exec(select(Recording).order_by(Recording.created_at.desc())).all()
        result = []
        for r in recordings:
            audio_url = ""
            if r.audio_path:
                parts = str(r.audio_path).split("/uploads/")
                audio_url = "/uploads/" + parts[1] if len(parts) > 1 else "/uploads/" + Path(r.audio_path).name
            result.append({
                "id": r.id,
                "audio_filename": r.audio_filename,
                "audio_url": audio_url,
                "status": r.status,
                "progress": r.progress,
                "progress_label": r.progress_label,
                "quality_report": json.loads(r.quality_report) if r.quality_report else None,
                "created_at": r.created_at,
            })
        return result

@app.post("/api/recordings/upload")
async def upload_recording(
    name: str = Form(""),
    audio_file: UploadFile = File(...),
):
    """上传单个录音（自动创建默认任务，自动调用ASR转写）"""
    # 创建或获取默认任务
    with Session(engine) as session:
        default_task = session.exec(select(Task).where(Task.name == "默认任务")).first()
        if not default_task:
            default_task = Task(name="默认任务", status="upload", progress=0, progress_label="待处理")
            session.add(default_task)
            session.commit()
            session.refresh(default_task)
        task_id = default_task.id

    # 创建录音记录
    task_id_int = task_id
    rec_id = await _create_recording_record(task_id_int, name or audio_file.filename, audio_file)

    # 自动调用ASR转写和质检
    asyncio.create_task(run_asr_and_check(rec_id))

    return {"ok": True, "recording_id": rec_id}

@app.delete("/api/recordings/{recording_id}")
async def delete_recording(recording_id: int):
    """删除录音"""
    with Session(engine) as session:
        rec = session.get(Recording, recording_id)
        if not rec:
            raise HTTPException(status_code=404, detail="录音不存在")
        # 删除音频文件
        if rec.audio_path:
            audio_path = Path(rec.audio_path)
            if audio_path.exists():
                audio_path.unlink()
            # 删除所属目录（如果为空）
            audio_dir = audio_path.parent
            if audio_dir.exists() and not any(audio_dir.iterdir()):
                audio_dir.rmdir()
        session.delete(rec)
        session.commit()
        return {"ok": True}

@app.post("/api/tasks/{task_id}/recheck-all")
async def recheck_all_recordings(task_id: int):
    """重新执行任务下所有录音的质检"""
    with Session(engine) as session:
        task = session.get(Task, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        recordings = session.exec(select(Recording).where(Recording.task_id == task_id)).all()
    for rec in recordings:
        asyncio.create_task(run_quality_check(rec.id, recheck=True))
    return {"ok": True, "message": f"已启动 {len(recordings)} 条录音的重新质检"}

# ── 异步质检流程 ─────────────────────────────────────────────────────────────
async def run_asr_and_check(recording_id: int):
    """调用ASR转写，然后质检"""
    audio_path = None
    with Session(engine) as session:
        rec = session.get(Recording, recording_id)
        if not rec or not rec.audio_path:
            return
        audio_path = rec.audio_path
        rec.status = "transcribing"
        rec.progress = 10
        rec.progress_label = "转写中"
        session.add(rec)
        session.commit()

    # 调用ASR（在session外，避免DetachedInstanceError）
    transcript = await transcribe_audio(audio_path)

    with Session(engine) as session:
        rec = session.get(Recording, recording_id)
        if not rec:
            return
        if transcript:
            rec.transcript_json = json.dumps(transcript, ensure_ascii=False)
            rec.status = "transcribing"
            rec.progress = 30
            rec.progress_label = "转写完成"
            session.add(rec)
            session.commit()
            # 转写成功，继续质检
            asyncio.create_task(run_quality_check(recording_id))
        else:
            rec.status = "failed"
            rec.progress = 0
            rec.progress_label = "转写失败"
            session.add(rec)
            session.commit()

async def run_quality_check(recording_id: int, recheck: bool = False):
    """异步质检：敏感词 → LLM分析"""
    # 重新质检时跳过等待，直接处理
    if not recheck:
        await asyncio.sleep(1)

    with Session(engine) as session:
        rec = session.get(Recording, recording_id)
        if not rec:
            return
        
        rec.status = "quality_checking"
        rec.progress = 50
        rec.progress_label = "重新质检中" if recheck else "质检中"
        session.add(rec)
        session.commit()

        # 加载敏感词
        sw_records = session.exec(select(SensitiveWord)).all()
        sensitive_words = [w.word for w in sw_records]
        if not sensitive_words:
            sensitive_words = ["受贿", "行贿", "内定", "串标", "围标", "泄密", "好处费", "回扣"]

        transcript = json.loads(rec.transcript_json or "{}")
        segments = transcript.get("segments", [])

        # 专家检测
        expert_speaker = detect_expert_speaker(segments)
        for seg in segments:
            seg["is_expert"] = (seg.get("speaker") == expert_speaker)

        # 收集全文作为上下文
        all_text = " ".join([seg.get("text", "") for seg in segments])

        # 按段落分组收集敏感词命中
        segment_hits = {}  # segment_index -> list of hit info
        total_hits = 0

        for i, seg in enumerate(segments):
            text = seg.get("text", "")

            for sw in sensitive_words:
                if sw in text:
                    total_hits += 1
                    # 判断当前说话人是否是专家
                    is_expert = seg.get("is_expert", False)
                    # 调用大模型进行语义分析
                    result = analyze_with_llm(text, sw, context=all_text, is_expert=is_expert)
                    if i not in segment_hits:
                        segment_hits[i] = {
                            "text": text,
                            "speaker": seg.get("speaker", ""),
                            "start": seg.get("start", 0),
                            "end": seg.get("end", 0),
                            "hits": []
                        }
                    segment_hits[i]["hits"].append({
                        "word": sw,
                        "qualified": result["qualified"],
                        "reason": result["reason"],
                        "severity": result["severity"],
                    })
                    
        # 辅助函数：判断qualified状态
        def _is_qualified(hit):
            return hit["qualified"] == "合格"
        
        def _is_unqualified(hit):
            return hit["qualified"] == "不合格"
        
        def _is_suspect(hit):
            return hit["qualified"] == "疑似违规"
        
        def _is_llm_failed(hit):
            return hit["qualified"] == "LLM调用失败"

        # 合并同一段落的多敏感词为一个issue
        issues = []
        for seg_idx, seg_info in segment_hits.items():
            hits = seg_info["hits"]
            bad_hits = [h for h in hits if _is_unqualified(h)]
            suspect_hits = [h for h in hits if _is_suspect(h)]
            llm_failed_hits = [h for h in hits if _is_llm_failed(h)]
            ok_hits = [h for h in hits if _is_qualified(h)]
            words = [h["word"] for h in hits]
            
            # 优先级：不合格 > LLM调用失败 > 疑似违规 > 合格
            if bad_hits:
                # 有不合格的，以不合格为准
                bad_words = [h["word"] for h in bad_hits]
                severity_map = {"low": 0, "medium": 1, "high": 2}
                severity = max([h["severity"] for h in bad_hits], 
                              key=lambda s: severity_map.get(s, 0))
                issues.append({
                    "segment_index": seg_idx,
                    "sentence": seg_info["text"],
                    "sensitive_word": "、".join(bad_words),
                    "sensitive_words": bad_words,
                    "speaker": seg_info["speaker"],
                    "start": seg_info["start"],
                    "end": seg_info["end"],
                    "qualified": "不合格",
                    "llm_reason": bad_hits[0]["reason"],
                    "severity": severity,
                })
            elif suspect_hits:
                # 有疑似违规的
                suspect_words = [h["word"] for h in suspect_hits]
                severity_map = {"low": 0, "medium": 1, "high": 2}
                severity = max([h["severity"] for h in suspect_hits], 
                              key=lambda s: severity_map.get(s, 0))
                issues.append({
                    "segment_index": seg_idx,
                    "sentence": seg_info["text"],
                    "sensitive_word": "、".join(suspect_words),
                    "sensitive_words": suspect_words,
                    "speaker": seg_info["speaker"],
                    "start": seg_info["start"],
                    "end": seg_info["end"],
                    "qualified": "疑似违规",
                    "llm_reason": suspect_hits[0]["reason"],
                    "severity": severity,
                })
            elif llm_failed_hits:
                # LLM 调用失败
                failed_words = [h["word"] for h in llm_failed_hits]
                issues.append({
                    "segment_index": seg_idx,
                    "sentence": seg_info["text"],
                    "sensitive_word": "、".join(failed_words),
                    "sensitive_words": failed_words,
                    "speaker": seg_info["speaker"],
                    "start": seg_info["start"],
                    "end": seg_info["end"],
                    "qualified": "LLM调用失败",
                    "llm_reason": llm_failed_hits[0]["reason"],
                    "severity": "medium",
                })
            else:
                # 全部合格
                issues.append({
                    "segment_index": seg_idx,
                    "sentence": seg_info["text"],
                    "sensitive_word": "、".join(words),
                    "sensitive_words": words,
                    "speaker": seg_info["speaker"],
                    "start": seg_info["start"],
                    "end": seg_info["end"],
                    "qualified": "合格",
                    "llm_reason": hits[0]["reason"],
                    "severity": "low",
                })

        bad_count = sum(1 for i in issues if i["qualified"] == "不合格")
        suspect_count = sum(1 for i in issues if i["qualified"] == "疑似违规")
        llm_failed_count = sum(1 for i in issues if i["qualified"] == "LLM调用失败")
        quality_report = {
            "total_sensitive_hits": total_hits,
            "qualified": "不合格" if bad_count > 0 else ("LLM调用失败" if llm_failed_count > 0 else ("疑似违规" if suspect_count > 0 else "合格")),
            "issues": issues,
            "bad_count": bad_count,
            "suspect_count": suspect_count,
            "llm_failed_count": llm_failed_count,
        }

        transcript["segments"] = segments
        rec.transcript_json = json.dumps(transcript, ensure_ascii=False)
        rec.quality_report = json.dumps(quality_report, ensure_ascii=False)
        rec.status = "completed"
        rec.progress = 100
        rec.progress_label = "已完成"
        session.add(rec)
        session.commit()

        # 更新任务整体状态
        task = session.get(Task, rec.task_id)
        if task:
            all_recs = session.exec(select(Recording).where(Recording.task_id == rec.task_id)).all()
            all_completed = all(r.status == "completed" for r in all_recs)
            if all_completed:
                task.status = "completed"
                task.progress_label = "已完成"
            else:
                task.status = "processing"
                task.progress_label = "处理中"
            session.add(task)
            session.commit()

# ── API: 敏感词 ───────────────────────────────────────────────────────────────
@app.get("/api/sensitive-words")
async def list_sensitive_words(skip: int = 0, limit: int = 50, search: str = ""):
    with Session(engine) as session:
        query = select(SensitiveWord)
        if search:
            query = query.where(SensitiveWord.word.contains(search))
        all_words = session.exec(query.order_by(SensitiveWord.id.asc())).all()
        words = session.exec(query.order_by(SensitiveWord.id.asc()).offset(skip).limit(limit)).all()
        return {
            "total": len(all_words),
            "words": [{"id": w.id, "word": w.word, "category": w.category or "", "created_at": w.created_at} for w in words]
        }

@app.post("/api/sensitive-words")
async def create_sensitive_word(word: str = Form(...), category: str = Form("")):
    with Session(engine) as session:
        existing = session.exec(select(SensitiveWord).where(SensitiveWord.word == word)).first()
        if existing:
            raise HTTPException(status_code=400, detail="敏感词已存在")
        sw = SensitiveWord(word=word.strip(), category=category.strip())
        session.add(sw)
        session.commit()
        session.refresh(sw)
        return {"id": sw.id, "word": sw.word, "category": sw.category or ""}

@app.post("/api/sensitive-words/batch")
async def batch_import_sensitive_words(file: UploadFile = File(None), request: Request = None):
    # 支持两种方式：文件上传 或 JSON 格式
    words = []
    
    # 方式1：JSON 格式（前端使用）
    if request:
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            body = await request.json()
            words = body.get("words", [])
    
    # 方式2：文件上传
    if not words and file:
        content = await file.read()
        lines = content.decode("utf-8").splitlines()
        words = [w.strip() for w in lines if w.strip() and not w.startswith("#")]
    
    added = 0
    with Session(engine) as session:
        for w in words:
            if not w:
                continue
            existing = session.exec(select(SensitiveWord).where(SensitiveWord.word == w)).first()
            if not existing:
                session.add(SensitiveWord(word=w))
                added += 1
        session.commit()
    return {"total": len(words), "imported": added, "added": added}

@app.put("/api/sensitive-words/{sw_id}")
async def update_sensitive_word(sw_id: int, request: Request):
    # 支持 JSON 格式
    body = await request.json()
    word = body.get("word", "").strip()
    category = body.get("category", "").strip()
    description = body.get("description", "").strip()
    
    if not word:
        raise HTTPException(status_code=400, detail="敏感词不能为空")
    
    with Session(engine) as session:
        sw = session.get(SensitiveWord, sw_id)
        if not sw:
            raise HTTPException(status_code=404, detail="敏感词不存在")
        sw.word = word
        sw.category = category
        session.add(sw)
        session.commit()
        return {"id": sw.id, "word": sw.word, "category": sw.category or ""}

@app.delete("/api/sensitive-words/{sw_id}")
async def delete_sensitive_word(sw_id: int):
    with Session(engine) as session:
        sw = session.get(SensitiveWord, sw_id)
        if not sw:
            raise HTTPException(status_code=404, detail="敏感词不存在")
        session.delete(sw)
        session.commit()
        return {"ok": True}

# ── API: 质检规则 ─────────────────────────────────────────────────────────────
@app.get("/api/quality-rules")
async def list_quality_rules():
    with Session(engine) as session:
        rules = session.exec(select(QualityRule).order_by(QualityRule.created_at.desc())).all()
        return [{
            "id": r.id,
            "name": r.name,
            "description": r.description or "",
            "rule_type": r.rule_type,
            "keywords": r.keywords or "",
            "context_words": r.context_words or "",
            "action": r.action,
            "enabled": r.enabled,
            "created_at": r.created_at,
            "updated_at": r.updated_at,
        } for r in rules]

@app.post("/api/quality-rules")
async def create_quality_rule(request: dict):
    with Session(engine) as session:
        rule = QualityRule(
            name=request.get("name", ""),
            description=request.get("description", ""),
            rule_type=request.get("rule_type", "keyword"),
            keywords=request.get("keywords", ""),
            context_words=request.get("context_words", ""),
            action=request.get("action", "reject"),
            enabled=request.get("enabled", True),
        )
        session.add(rule)
        session.commit()
        session.refresh(rule)
        # 记录历史
        history = QualityRuleHistory(
            rule_id=rule.id,
            action="create",
            snapshot=json.dumps({"name": rule.name, "rule_type": rule.rule_type, "action": rule.action, "enabled": rule.enabled}),
        )
        session.add(history)
        session.commit()
        return {"id": rule.id, "name": rule.name}

@app.get("/api/quality-rules/{rule_id}")
async def get_quality_rule(rule_id: int):
    with Session(engine) as session:
        rule = session.get(QualityRule, rule_id)
        if not rule:
            raise HTTPException(status_code=404, detail="规则不存在")
        return {
            "id": rule.id,
            "name": rule.name,
            "description": rule.description or "",
            "rule_type": rule.rule_type,
            "keywords": rule.keywords or "",
            "context_words": rule.context_words or "",
            "action": rule.action,
            "enabled": rule.enabled,
            "created_at": rule.created_at,
            "updated_at": rule.updated_at,
        }

@app.put("/api/quality-rules/{rule_id}")
async def update_quality_rule(rule_id: int, request: dict):
    with Session(engine) as session:
        rule = session.get(QualityRule, rule_id)
        if not rule:
            raise HTTPException(status_code=404, detail="规则不存在")
        # 记录变更前快照
        old_snapshot = {
            "name": rule.name,
            "description": rule.description,
            "rule_type": rule.rule_type,
            "keywords": rule.keywords,
            "context_words": rule.context_words,
            "action": rule.action,
            "enabled": rule.enabled,
        }
        # 更新
        rule.name = request.get("name", rule.name)
        rule.description = request.get("description", rule.description)
        rule.rule_type = request.get("rule_type", rule.rule_type)
        rule.keywords = request.get("keywords", rule.keywords)
        rule.context_words = request.get("context_words", rule.context_words)
        rule.action = request.get("action", rule.action)
        rule.enabled = request.get("enabled", rule.enabled)
        rule.updated_at = datetime.now().isoformat()
        session.add(rule)
        # 记录历史
        new_snapshot = {
            "name": rule.name,
            "description": rule.description,
            "rule_type": rule.rule_type,
            "keywords": rule.keywords,
            "context_words": rule.context_words,
            "action": rule.action,
            "enabled": rule.enabled,
        }
        history = QualityRuleHistory(
            rule_id=rule.id,
            action="update",
            snapshot=json.dumps({"before": old_snapshot, "after": new_snapshot}),
        )
        session.add(history)
        session.commit()
        return {"ok": True}

@app.post("/api/quality-rules/{rule_id}/toggle")
async def toggle_quality_rule(rule_id: int, request: dict):
    with Session(engine) as session:
        rule = session.get(QualityRule, rule_id)
        if not rule:
            raise HTTPException(status_code=404, detail="规则不存在")
        old_enabled = rule.enabled
        rule.enabled = request.get("enabled", not rule.enabled)
        rule.updated_at = datetime.now().isoformat()
        session.add(rule)
        # 记录历史
        history = QualityRuleHistory(
            rule_id=rule.id,
            action="update",
            snapshot=json.dumps({"before": {"enabled": old_enabled}, "after": {"enabled": rule.enabled}}),
        )
        session.add(history)
        session.commit()
        return {"ok": True}

@app.delete("/api/quality-rules/{rule_id}")
async def delete_quality_rule(rule_id: int):
    with Session(engine) as session:
        rule = session.get(QualityRule, rule_id)
        if not rule:
            raise HTTPException(status_code=404, detail="规则不存在")
        rule_name = rule.name
        session.delete(rule)
        # 记录历史
        history = QualityRuleHistory(
            rule_id=rule_id,
            action="delete",
            snapshot=json.dumps({"name": rule_name}),
        )
        session.add(history)
        session.commit()
        return {"ok": True}

@app.get("/api/quality-rules/history")
async def get_quality_rule_history():
    with Session(engine) as session:
        history = session.exec(select(QualityRuleHistory).order_by(QualityRuleHistory.created_at.desc()).limit(50)).all()
        result = []
        for h in history:
            # 获取规则名称
            rule = session.get(QualityRule, h.rule_id)
            rule_name = rule.name if rule else "已删除规则"
            result.append({
                "id": h.id,
                "rule_id": h.rule_id,
                "rule_name": rule_name,
                "action": h.action,
                "snapshot": h.snapshot,
                "operator": h.operator,
                "created_at": h.created_at,
            })
        return result

# ── 启动 ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5173)
