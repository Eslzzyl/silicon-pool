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
    "rpm_limit": 0,  # 每分钟请求数限制，0表示不限制
    "tpm_limit": 0,  # 每分钟处理令牌数限制，0表示不限制
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
            # 增加重试机制，避免因短暂网络问题导致任务失败
            max_retries = 3
            retry_delay = 60  # 失败后等待60秒再重试
            success = False
            
            for retry in range(max_retries):
                try:
                    if retry == 0:
                        logging.info("执行自动刷新API密钥任务")
                    else:
                        logging.info(f"第{retry+1}次尝试执行自动刷新API密钥任务")
                    
                    # 使用超时控制，避免刷新操作无限等待
                    refresh_timeout = 300  # 5分钟超时
                    try:
                        await asyncio.wait_for(refresh_keys(), timeout=refresh_timeout)
                        logging.info(f"自动刷新API密钥任务完成，等待{interval}分钟后再次执行")
                        success = True
                        break  # 成功完成，跳出重试循环
                    except asyncio.TimeoutError:
                        logging.error(f"自动刷新API密钥任务超时（超过{refresh_timeout}秒）")
                        # 超时也视为失败，继续重试
                        
                except Exception as e:
                    logging.error(f"自动刷新API密钥任务失败: {str(e)}")
                    if retry < max_retries - 1:  # 如果不是最后一次重试
                        logging.info(f"将在{retry_delay}秒后重试")
                        await asyncio.sleep(retry_delay)
            
            if not success:
                logging.error(f"自动刷新API密钥任务在尝试{max_retries}次后仍然失败，将在下一个周期重试")
            
            # 等待下一个刷新周期
            wait_seconds = interval * 60
            logging.debug(f"等待{wait_seconds}秒后执行下一次刷新")
            
            # 分段等待，便于及时响应停止信号
            for _ in range(wait_seconds):
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
        "round_robin",
    ]

    if strategy not in allowed_strategies:
        return JSONResponse({"message": "无效的策略选项"}, status_code=400)

    config = read_config()
    config["call_strategy"] = strategy
    write_config(config)
    
    # 同步更新全局变量
    try:
        import sys
        sys.path.append("..")
        import config
        config.CALL_STRATEGY = strategy
    except Exception as e:
        logging.error(f"更新全局策略变量失败: {str(e)}")

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


@router.get("/config/rpm_tpm_limits")
async def get_rpm_tpm_limits():
    config = read_config()
    return JSONResponse({
        "rpm_limit": config.get("rpm_limit", 0),
        "tpm_limit": config.get("tpm_limit", 0)
    })


@router.post("/config/rpm_limit")
async def update_rpm_limit(request: Request):
    data = await request.json()
    rpm_limit = int(data.get("rpm_limit", 0))

    config = read_config()
    config["rpm_limit"] = rpm_limit
    write_config(config)
    
    # 同步更新全局变量
    try:
        import sys
        sys.path.append("..")
        import config
        config.update_rpm_limit(rpm_limit)
    except Exception as e:
        logging.error(f"更新全局RPM限制变量失败: {str(e)}")

    return JSONResponse({"message": f"RPM限制已更新为: {rpm_limit}" if rpm_limit > 0 else "RPM限制已关闭"})


@router.post("/config/tpm_limit")
async def update_tpm_limit(request: Request):
    data = await request.json()
    tpm_limit = int(data.get("tpm_limit", 0))

    config = read_config()
    config["tpm_limit"] = tpm_limit
    write_config(config)
    
    # 同步更新全局变量
    try:
        import sys
        sys.path.append("..")
        import config
        config.update_tpm_limit(tpm_limit)
    except Exception as e:
        logging.error(f"更新全局TPM限制变量失败: {str(e)}")

    return JSONResponse({"message": f"TPM限制已更新为: {tpm_limit}" if tpm_limit > 0 else "TPM限制已关闭"})
