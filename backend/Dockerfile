# Multi-Agent Platform 后端镜像（纯 API；前端构建产物另行托管）
# 构建：  docker build -t agentchat-backend -f backend/Dockerfile .
# 运行：  docker run --env-file backend/.env -p 8000:8000 \
#           -v agentchat-data:/app/data \
#           -v agentchat-hf:/root/.cache/huggingface \
#           agentchat-backend
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/root/.cache/huggingface

WORKDIR /app

# 系统依赖：部分 Python wheel（如 pydantic-core）在 slim 镜像上需编译工具
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 先装依赖（利用 Docker 层缓存，代码变更不重复下载）
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --upgrade pip && pip install -r backend/requirements.txt

# 拷贝源码（app 包、MCP 自建服务器脚本、启动入口）
COPY backend/app ./app
COPY backend/scripts ./scripts
COPY backend/run.py ./run.py
# 自主任务 Agent 独立包（requirements 中 -e ../task-agent 相对 backend/ 解析到 /app/task-agent）
COPY task-agent ./task-agent

# 数据目录（上传文件/运行时产物；建议挂载卷持久化）
RUN mkdir -p /app/data

EXPOSE 8000

# Linux 下直接 uvicorn（run.py 的 SelectorEventLoop 仅 Windows 需要）
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
