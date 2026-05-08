# 录音质检系统

基于 FastAPI + SQLite 的智能录音质检分析系统，支持 ASR 语音识别和 LLM 语义分析。

## 一键部署

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/maxiaopai/recording-quality-inspection)

点击上方按钮，即可自动部署到 Render 云平台，部署完成后会生成公网访问链接。

## 功能特性

- 📁 批量录音上传
- 🎙️ 自动 ASR 语音识别
- 🤖 LLM 智能质检分析
- 📊 质检规则管理
- 🔍 敏感词检测
- 📈 可视化质检报告

## 本地运行

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

访问 http://localhost:8000
