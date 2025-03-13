from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import uvicorn
import logging
from uvicorn.config import LOGGING_CONFIG
from db import init_db
from routers import api_keys, logs, config, completions, static, stats, auth

# 配置日志格式
LOGGING_CONFIG["formatters"]["default"]["fmt"] = (
    "%(asctime)s - %(levelprefix)s %(message)s"
)
LOGGING_CONFIG["formatters"]["access"]["fmt"] = (
    "%(asctime)s - %(levelprefix)s %(message)s"
)
LOGGING_CONFIG["formatters"]["default"]["use_colors"] = None
LOGGING_CONFIG["formatters"]["access"]["use_colors"] = None
LOGGING_CONFIG["loggers"]["root"] = {
    "handlers": ["default"],
    "level": "INFO",
}

logging.config.dictConfig(LOGGING_CONFIG)
logging.basicConfig(level=logging.INFO)

# 创建FastAPI应用
app = FastAPI(
    title="Silicon Pool API", description="硅基流动 API Key 池管理工具", version="0.1.0"
)

# 初始化数据库
init_db()

# 挂载静态文件
app.mount("/static", StaticFiles(directory="static"))

# 包含所有路由模块
app.include_router(api_keys.router, tags=["API密钥管理"])
app.include_router(logs.router, tags=["日志管理"])
app.include_router(config.router, tags=["配置管理"])
app.include_router(completions.router, tags=["API转发"])
app.include_router(static.router, tags=["静态文件"])
app.include_router(stats.router, tags=["统计数据"])
app.include_router(auth.router, tags=["认证"])

# 启动入口
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=7898)
