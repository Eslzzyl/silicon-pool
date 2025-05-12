from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
import json
from pathlib import Path
from typing import Dict, Any
import threading
import asyncio
import logging
from routers.api_keys import refresh_keys

router = APIRouter()
config_file = Path("config.json")
scheduler_thread = None
stop_event = threading.Event()

# 初始化配置
default_config = {
    "call_strategy": "random",
    "custom_api_key": "",
    "free_model_api_key": "",
    "refresh_interval": 0,  # 单位: 分钟，0表示不自动刷新
}

# 确保配置文件存在
if not config_file.exists():
    with open(config_file, "w", encoding="utf-8") as f:
        json.dump(default_config, f, ensure_ascii=False, indent=4)


# 读取配置
def read_config() -> Dict[str, Any]:
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            config = json.load(f)
        # 确保配置项完整
        for key, value in default_config.items():
            if key not in config:
                config[key] = value
        return config
    except Exception as e:
        logging.error(f"读取配置文件失败: {str(e)}")
        return default_config.copy()


# 写入配置
def write_config(config: Dict[str, Any]):
    try:
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logging.error(f"写入配置文件失败: {str(e)}")


# 刷新定时任务函数
async def refresh_task():
    while not stop_event.is_set():
        config = read_config()
        interval = config.get("refresh_interval", 0)

        if interval > 0:
            try:
                logging.debug("执行自动刷新API密钥任务")
                await refresh_keys()
                logging.debug(f"自动刷新API密钥任务完成，等待{interval}分钟后再次执行")
            except Exception as e:
                logging.error(f"自动刷新API密钥任务失败: {str(e)}")
                
            for _ in range(interval * 60):  # 将分钟转换为秒
                if stop_event.is_set():
                    break
                await asyncio.sleep(1)
        else:
            # 如果间隔为0，则休眠一段时间后再次检查配置
            await asyncio.sleep(60)


# 启动定时任务
def start_scheduler():
    global scheduler_thread, stop_event
    if scheduler_thread and scheduler_thread.is_alive():
        return

    stop_event.clear()

    def run_async_task():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(refresh_task())

    scheduler_thread = threading.Thread(target=run_async_task)
    scheduler_thread.daemon = True
    scheduler_thread.start()
    logging.info("API密钥自动刷新任务已启动")


# 停止定时任务
def stop_scheduler():
    global stop_event
    stop_event.set()
    logging.info("API密钥自动刷新任务已停止")


# 在应用启动时启动定时任务
start_scheduler()


@router.get("/config/strategy")
async def get_strategy():
    config = read_config()
    return JSONResponse({"call_strategy": config.get("call_strategy", "random")})


@router.post("/config/strategy")
async def update_strategy(request: Request):
    data = await request.json()
    strategy = data.get("call_strategy")

    allowed_strategies = [
        "random",
        "high",
        "low",
        "least_used",
        "most_used",
        "oldest",
        "newest",
    ]

    if strategy not in allowed_strategies:
        return JSONResponse({"message": "无效的策略选项"}, status_code=400)

    config = read_config()
    config["call_strategy"] = strategy
    write_config(config)

    return JSONResponse({"message": f"调用策略已更新为: {strategy}"})


@router.get("/config/custom_api_key")
async def get_custom_api_key():
    config = read_config()
    return JSONResponse({"custom_api_key": config.get("custom_api_key", "")})


@router.post("/config/custom_api_key")
async def update_custom_api_key(request: Request):
    data = await request.json()
    key = data.get("custom_api_key", "")

    config = read_config()
    config["custom_api_key"] = key
    write_config(config)

    if key:
        return JSONResponse({"message": "转发 API token 已成功设置"})
    else:
        return JSONResponse({"message": "转发 API token 已清除"})


@router.get("/config/free_model_api_key")
async def get_free_model_api_key():
    config = read_config()
    return JSONResponse({"free_model_api_key": config.get("free_model_api_key", "")})


@router.post("/config/free_model_api_key")
async def update_free_model_api_key(request: Request):
    data = await request.json()
    key = data.get("free_model_api_key", "")

    config = read_config()
    config["free_model_api_key"] = key
    write_config(config)

    if key:
        return JSONResponse({"message": "免费模型 API token 已成功设置"})
    else:
        return JSONResponse({"message": "免费模型 API token 已清除"})


@router.get("/config/refresh_interval")
async def get_refresh_interval():
    config = read_config()
    return JSONResponse({"refresh_interval": config.get("refresh_interval", 0)})


@router.post("/config/refresh_interval")
async def update_refresh_interval(request: Request):
    data = await request.json()
    interval = data.get("refresh_interval", 0)

    if not isinstance(interval, int) or interval < 0:
        return JSONResponse({"message": "刷新间隔必须是非负整数"}, status_code=400)

    config = read_config()
    config["refresh_interval"] = interval
    write_config(config)

    # 更新后重启定时任务
    stop_scheduler()
    if interval > 0:
        start_scheduler()
        return JSONResponse({"message": f"自动刷新间隔已设置为 {interval} 分钟"})
    else:
        return JSONResponse({"message": "已关闭自动刷新"})
