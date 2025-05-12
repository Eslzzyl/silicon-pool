FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV TZ=Asia/Shanghai

# 设置时区并安装curl工具用于健康检查
RUN apt-get update && apt-get install -y curl \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 设置工作目录
WORKDIR /app

# 复制依赖文件并安装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目文件
COPY . .

# 暴露端口
EXPOSE 7898

# 添加执行权限并运行启动脚本
RUN chmod +x /app/start.sh
CMD ["/bin/bash", "/app/start.sh"]

# 添加健康检查 - 大幅增加重试次数和间隔时间，使其更加宽容
HEALTHCHECK --interval=60s --timeout=30s --start-period=60s --retries=5 CMD curl --fail http://localhost:7898/health || exit 1