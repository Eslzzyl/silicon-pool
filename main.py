from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import uvicorn
import logging
from uvicorn.config import LOGGING_CONFIG
from contextlib import asynccontextmanager
from db import init_db
from routers import api_keys, generate, logs, config, static, stats, auth
from request_queue import request_queue_manager

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


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        # 启动请求队列处理器
        await request_queue_manager.start_processing()
        logging.info("请求队列处理器已启动")
        
        yield
    except Exception as e:
        logging.error(f"FastAPI生命周期发生异常: {str(e)}")
        # 即使发生异常也继续执行，不要让容器退出
        yield
    finally:
        try:
            # 停止请求队列处理器
            await request_queue_manager.stop_processing()
            logging.info("请求队列处理器已停止")
            
            # 停止调度器
            config.stop_scheduler()
        except Exception as e:
            logging.error(f"停止服务时发生异常: {str(e)}")
            # 记录异常但不抛出，避免容器退出


# 创建FastAPI应用
app = FastAPI(
    title="Silicon Pool API",
    description="硅基流动 API Key 池管理工具",
    lifespan=lifespan,
)

# 初始化数据库
init_db()

# 挂载静态文件
app.mount("/static", StaticFiles(directory="static"))

# 包含所有路由模块
app.include_router(api_keys.router, tags=["API密钥管理"])
app.include_router(logs.router, tags=["日志管理"])
app.include_router(config.router, tags=["配置管理"])
app.include_router(generate.router, tags=["API转发"])
app.include_router(static.router, tags=["静态文件"])
app.include_router(stats.router, tags=["统计数据"])
app.include_router(auth.router, tags=["认证"])

@app.get("/health", status_code=200)
async def health_check():
    return {"status": "healthy"}


# 启动入口
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=7898)
