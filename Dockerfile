FROM python:3.12-slim

WORKDIR /app

# 复制项目文件
COPY . /app/

# 安装依赖
RUN pip install --no-cache-dir -r requirements.txt

# 暴露应用端口
EXPOSE 7898

# 启动应用
CMD ["python", "main.py"]