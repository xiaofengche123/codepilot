FROM python:3.12-slim

WORKDIR /app

# 系统依赖（git 用于 worktree、chromadb 需要 C++ 运行时）
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

# Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 代码（.dockerignore 已排除 .env / venv / .git 等）
COPY . .

EXPOSE 8000

ENV CODEPILOT_HOST=0.0.0.0
ENV CODEPILOT_PORT=8000

CMD ["python", "server.py"]
