from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
import config
from config import update_call_strategy, update_custom_api_key

router = APIRouter()


@router.get("/config/strategy")
async def get_strategy():
    strategies = [
        {"value": "random", "label": "随机选择"},
        {"value": "high", "label": "优先消耗余额最多"},
        {"value": "low", "label": "优先消耗余额最少"},
        {"value": "least_used", "label": "优先消耗使用次数最少"},
        {"value": "most_used", "label": "优先消耗使用次数最多"},
        {"value": "oldest", "label": "优先消耗添加时间最旧"},
        {"value": "newest", "label": "优先消耗添加时间最新"},
    ]
    return JSONResponse(
        {"call_strategy": config.CALL_STRATEGY, "strategies": strategies}
    )


@router.post("/config/strategy")
async def set_strategy(request: Request):
    data = await request.json()
    new_strategy = data.get("call_strategy")
    valid_strategies = [
        "random",
        "high",
        "low",
        "least_used",
        "most_used",
        "oldest",
        "newest",
    ]
    if new_strategy not in valid_strategies:
        raise HTTPException(status_code=400, detail="无效的调用策略")
    update_call_strategy(new_strategy)
    return JSONResponse({"message": "调用策略更新成功", "call_strategy": new_strategy})


@router.get("/config/custom_api_key")
async def get_custom_api_key():
    return JSONResponse({"custom_api_key": config.CUSTOM_API_KEY})


@router.post("/config/custom_api_key")
async def set_custom_api_key(request: Request):
    data = await request.json()
    new_key = data.get("custom_api_key", "")
    update_custom_api_key(new_key)
    return JSONResponse(
        {"message": "自定义 api_key 更新成功", "custom_api_key": new_key}
    )
