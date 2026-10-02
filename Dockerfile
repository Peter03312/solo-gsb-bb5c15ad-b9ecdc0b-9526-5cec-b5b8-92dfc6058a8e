# ---------- 阶段一：构建 React 前端 ----------
FROM node:20-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---------- 阶段二：Python 后端 + SQLite ----------
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 \
    PORT=8000 \
    DB_PATH=/data/weave.db

WORKDIR /app

# 安装 Python 依赖（flask / python-sat / waitress）
COPY server/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 后端代码与前端构建产物
COPY server/ ./
COPY --from=frontend /build/dist ./static

# SQLite 数据目录（首次启动时自动建表并写入默认原稿）
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8000
CMD ["python", "app.py"]
