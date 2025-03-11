from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.requests import ClientDisconnect
import config
from config import (
    update_call_strategy,
    update_custom_api_key,
)
import sqlite3
import random
import time
import asyncio
import aiohttp
import json
import uvicorn
import logging
from uvicorn.config import LOGGING_CONFIG
import re

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


app = FastAPI()

# Mount static files
app.mount("/static", StaticFiles(directory="static"))

# SQLite DB initialization
conn = sqlite3.connect("pool.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute("""
CREATE TABLE IF NOT EXISTS api_keys (
    key TEXT PRIMARY KEY,
    add_time REAL,
    balance REAL,
    usage_count INTEGER
)
""")
conn.commit()

# Create logs table for recording completion calls
cursor.execute("""
CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    used_key TEXT,
    model TEXT,
    call_time REAL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    total_tokens INTEGER
)
""")
conn.commit()

BASE_URL = "https://api.siliconflow.cn"  # adjust if needed


async def validate_key_async(api_key: str):
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.siliconflow.cn/v1/user/info", headers=headers, timeout=10
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    return True, data.get("data", {}).get("totalBalance", 0)
                else:
                    data = await r.json()
                    return False, data.get("message", "验证失败")
    except Exception as e:
        return False, f"请求失败: {str(e)}"


def insert_api_key(api_key: str, balance: float):
    cursor.execute(
        "INSERT OR IGNORE INTO api_keys (key, add_time, balance, usage_count) VALUES (?, ?, ?, ?)",
        (api_key, time.time(), balance, 0),
    )
    conn.commit()


def log_completion(
    used_key: str,
    model: str,
    call_time: float,
    input_tokens: int,
    output_tokens: int,
    total_tokens: int,
):
    cursor.execute(
        "INSERT INTO logs (used_key, model, call_time, input_tokens, output_tokens, total_tokens) VALUES (?, ?, ?, ?, ?, ?)",
        (used_key, model, call_time, input_tokens, output_tokens, total_tokens),
    )
    conn.commit()


@app.get("/")
async def root():
    return FileResponse("static/index.html")


def validate_key_format(key: str) -> bool:
    """Validate if the key has the correct format (starts with 'sk-' followed by alphanumeric characters)."""
    return bool(re.match(r"^sk-[a-zA-Z0-9]+$", key))


def clean_key(key: str) -> str:
    """Clean the key by removing any trailing brackets or other content."""
    # Match the key pattern and return only that part
    match = re.search(r"(sk-[a-zA-Z0-9]+)", key)
    if match:
        return match.group(1)
    return key.strip()


@app.post("/import_keys")
async def import_keys(request: Request):
    data = await request.json()
    keys_text = data.get("keys", "")

    # Clean and validate keys
    raw_keys = [k.strip() for k in keys_text.splitlines() if k.strip()]
    cleaned_keys = [clean_key(k) for k in raw_keys]
    keys = [k for k in cleaned_keys if validate_key_format(k)]

    invalid_format_count = len(raw_keys) - len(keys)

    if not keys:
        return JSONResponse({"message": "未提供有效的 API Key"}, status_code=400)

    tasks = []
    # Prepare tasks: for duplicate keys, add a dummy task returning a marker.
    for key in keys:
        cursor.execute("SELECT key FROM api_keys WHERE key = ?", (key,))
        if cursor.fetchone():
            tasks.append(asyncio.sleep(0, result=("duplicate", key)))
        else:
            tasks.append(validate_key_async(key))

    results = await asyncio.gather(*tasks)
    imported_count = 0
    duplicate_count = 0
    invalid_count = 0

    for idx, result in enumerate(results):
        if result[0] == "duplicate":
            duplicate_count += 1
        else:
            valid, balance = result
            if valid and float(balance) > 0:
                insert_api_key(keys[idx], balance)
                imported_count += 1
            else:
                invalid_count += 1

    return JSONResponse(
        {
            "message": f"导入成功 {imported_count} 个，有重复 {duplicate_count} 个，格式无效 {invalid_format_count} 个，API 验证失败 {invalid_count} 个"
        }
    )


@app.post("/refresh")
async def refresh_keys():
    cursor.execute("SELECT key, balance FROM api_keys")
    key_balance_map = {row[0]: row[1] for row in cursor.fetchall()}
    all_keys = list(key_balance_map.keys())

    # Get initial total balance
    initial_balance = sum(key_balance_map.values())

    # Create tasks for parallel validation
    tasks = [validate_key_async(key) for key in all_keys]
    results = await asyncio.gather(*tasks)

    removed = 0
    for key, (valid, balance) in zip(all_keys, results):
        if valid and float(balance) > 0:
            cursor.execute(
                "UPDATE api_keys SET balance = ? WHERE key = ?", (balance, key)
            )
        else:
            cursor.execute("DELETE FROM api_keys WHERE key = ?", (key,))
            removed += 1

    conn.commit()

    # Calculate new total balance
    cursor.execute("SELECT COALESCE(SUM(balance), 0) FROM api_keys")
    new_balance = cursor.fetchone()[0]
    balance_change = new_balance - initial_balance

    message = ""
    if balance_change > 0:
        message = f"刷新完成，共移除 {removed} 个余额用尽或无效的 Key，余额增加了{round(balance_change, 2)}"
    else:
        balance_decrease = abs(balance_change)
        message = f"刷新完成，共移除 {removed} 个余额用尽或无效的 Key，余额减少了{round(balance_decrease, 2)}"

    return JSONResponse({"message": message})


def select_api_key(keys_with_balance):
    # keys_with_balance: list of (key, balance)
    if not keys_with_balance:
        return None

    # 基于余额的策略
    if config.CALL_STRATEGY == "high":
        return max(keys_with_balance, key=lambda x: x[1])[0]
    elif config.CALL_STRATEGY == "low":
        return min(keys_with_balance, key=lambda x: x[1])[0]

    # 基于使用次数的策略
    elif config.CALL_STRATEGY == "least_used":
        cursor.execute(
            "SELECT key, usage_count FROM api_keys WHERE key IN ({})".format(
                ",".join("?" for _ in range(len(keys_with_balance)))
            ),
            [k[0] for k in keys_with_balance],
        )
        usage_data = cursor.fetchall()
        return min(usage_data, key=lambda x: x[1])[0]
    elif config.CALL_STRATEGY == "most_used":
        cursor.execute(
            "SELECT key, usage_count FROM api_keys WHERE key IN ({})".format(
                ",".join("?" for _ in range(len(keys_with_balance)))
            ),
            [k[0] for k in keys_with_balance],
        )
        usage_data = cursor.fetchall()
        return max(usage_data, key=lambda x: x[1])[0]

    # 基于添加时间的策略
    elif config.CALL_STRATEGY == "oldest":
        cursor.execute(
            "SELECT key, add_time FROM api_keys WHERE key IN ({})".format(
                ",".join("?" for _ in range(len(keys_with_balance)))
            ),
            [k[0] for k in keys_with_balance],
        )
        time_data = cursor.fetchall()
        return min(time_data, key=lambda x: x[1])[0]
    elif config.CALL_STRATEGY == "newest":
        cursor.execute(
            "SELECT key, add_time FROM api_keys WHERE key IN ({})".format(
                ",".join("?" for _ in range(len(keys_with_balance)))
            ),
            [k[0] for k in keys_with_balance],
        )
        time_data = cursor.fetchall()
        return max(time_data, key=lambda x: x[1])[0]

    # 默认随机策略
    else:
        return random.choice(keys_with_balance)[0]


async def check_and_remove_key(key: str):
    valid, balance = await validate_key_async(key)
    logger = logging.getLogger(__name__)
    if valid:
        logger.info(f"Key validation successful: {key[:8]}*** - Balance: {balance}")
        if float(balance) <= 0:
            logger.warning(f"Removing key {key[:8]}*** due to zero balance")
            cursor.execute("DELETE FROM api_keys WHERE key = ?", (key,))
            conn.commit()
    else:
        logger.warning(f"Invalid key detected: {key[:8]}*** - Removing from pool")
        cursor.execute("DELETE FROM api_keys WHERE key = ?", (key,))
        conn.commit()


@app.post("/v1/chat/completions")
async def chat_completions(request: Request, background_tasks: BackgroundTasks):
    if config.CUSTOM_API_KEY and config.CUSTOM_API_KEY.strip():
        request_api_key = request.headers.get("Authorization")
        if request_api_key != f"Bearer {config.CUSTOM_API_KEY}":
            raise HTTPException(status_code=403, detail="无效的API_KEY")
    cursor.execute("SELECT key, balance FROM api_keys")
    keys_with_balance = cursor.fetchall()
    if not keys_with_balance:
        raise HTTPException(status_code=500, detail="没有可用的api-key")
    selected = select_api_key(keys_with_balance)
    # Increase usage count
    cursor.execute(
        "UPDATE api_keys SET usage_count = usage_count + 1 WHERE key = ?", (selected,)
    )
    conn.commit()
    # Forward the request to BASE_URL using the selected key
    forward_headers = dict(request.headers)
    forward_headers["Authorization"] = f"Bearer {selected}"
    try:
        req_body = await request.body()
    except ClientDisconnect:
        return JSONResponse({"error": "客户端断开连接"}, status_code=499)
    req_json = await request.json()
    model = req_json.get("model", "unknown")
    call_time_stamp = time.time()
    is_stream = req_json.get("stream", False)  # Changed default to False

    if is_stream:

        async def generate_stream():
            completion_tokens = 0
            prompt_tokens = 0
            total_tokens = 0
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"{BASE_URL}/v1/chat/completions",
                        headers=forward_headers,
                        data=req_body,
                        timeout=300,
                    ) as resp:
                        async for chunk in resp.content.iter_any():
                            try:
                                chunk_str = chunk.decode("utf-8")
                                if chunk_str == "[DONE]":
                                    continue
                                if chunk_str.startswith("data: "):
                                    data = json.loads(chunk_str[6:])
                                    usage = data.get("usage", {})
                                    prompt_tokens = usage.get("prompt_tokens", 0)
                                    completion_tokens = usage.get(
                                        "completion_tokens", 0
                                    )
                                    total_tokens = usage.get("total_tokens", 0)
                            except Exception:
                                pass
                            yield chunk
                # 流结束后记录完整 token 数量
                log_completion(
                    selected,
                    model,
                    call_time_stamp,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                )
                await check_and_remove_key(selected)
            except Exception as e:
                error_json = json.dumps({"error": f"请求失败: {str(e)}"}).encode(
                    "utf-8"
                )
                yield f"data: {error_json}\n\n".encode("utf-8")
                yield b"data: [DONE]\n\n"

        try:
            return StreamingResponse(
                generate_stream(),
                headers={"Content-Type": "application/octet-stream"},
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"请求转发失败: {str(e)}")
    else:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{BASE_URL}/v1/chat/completions",
                    headers=forward_headers,
                    data=req_body,
                    timeout=300,
                ) as resp:
                    resp_json = await resp.json()
                    usage = resp_json.get("usage", {})
                    prompt_tokens = usage.get("prompt_tokens", 0)
                    completion_tokens = usage.get("completion_tokens", 0)
                    total_tokens = usage.get("total_tokens", 0)
                    # Log completion call
                    log_completion(
                        selected,
                        model,
                        call_time_stamp,
                        prompt_tokens,
                        completion_tokens,
                        total_tokens,
                    )
                    # 后台检查 key 余额
                    background_tasks.add_task(check_and_remove_key, selected)
                    return JSONResponse(content=resp_json, status_code=resp.status)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"请求转发失败: {str(e)}")


@app.post("/v1/embeddings")
async def embeddings(request: Request, background_tasks: BackgroundTasks):
    if config.CUSTOM_API_KEY and config.CUSTOM_API_KEY.strip():
        request_api_key = request.headers.get("Authorization")
        if request_api_key != f"Bearer {config.CUSTOM_API_KEY}":
            raise HTTPException(status_code=403, detail="无效的API_KEY")
    cursor.execute("SELECT key, balance FROM api_keys")
    keys_with_balance = cursor.fetchall()
    if not keys_with_balance:
        raise HTTPException(status_code=500, detail="没有可用的api-key")
    selected = select_api_key(keys_with_balance)
    forward_headers = dict(request.headers)
    forward_headers["Authorization"] = f"Bearer {selected}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{BASE_URL}/v1/embeddings",
                headers=forward_headers,
                data=await request.body(),
                timeout=30,
            ) as resp:
                data = await resp.json()
                # 后台检查 key 余额
                background_tasks.add_task(check_and_remove_key, selected)
                return JSONResponse(content=data, status_code=resp.status)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"请求转发失败: {str(e)}")


@app.post("/v1/completions")
async def completions(request: Request, background_tasks: BackgroundTasks):
    if config.CUSTOM_API_KEY and config.CUSTOM_API_KEY.strip():
        request_api_key = request.headers.get("Authorization")
        if request_api_key != f"Bearer {config.CUSTOM_API_KEY}":
            raise HTTPException(status_code=403, detail="无效的API_KEY")

    cursor.execute("SELECT key, balance FROM api_keys")
    keys_with_balance = cursor.fetchall()
    if not keys_with_balance:
        raise HTTPException(status_code=500, detail="没有可用的api-key")

    selected = select_api_key(keys_with_balance)
    # Increase usage count
    cursor.execute(
        "UPDATE api_keys SET usage_count = usage_count + 1 WHERE key = ?", (selected,)
    )
    conn.commit()

    # Forward the request to BASE_URL using the selected key
    forward_headers = dict(request.headers)
    forward_headers["Authorization"] = f"Bearer {selected}"
    try:
        req_body = await request.body()
    except ClientDisconnect:
        return JSONResponse({"error": "客户端断开连接"}, status_code=499)
    req_json = await request.json()
    model = req_json.get("model", "unknown")
    call_time_stamp = time.time()
    is_stream = req_json.get("stream", False)

    if is_stream:

        async def generate_stream():
            completion_tokens = 0
            prompt_tokens = 0
            total_tokens = 0
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"{BASE_URL}/v1/completions",
                        headers=forward_headers,
                        data=req_body,
                        timeout=300,
                    ) as resp:
                        async for chunk in resp.content.iter_any():
                            try:
                                chunk_str = chunk.decode("utf-8")
                                if chunk_str == "[DONE]":
                                    continue
                                if chunk_str.startswith("data: "):
                                    data = json.loads(chunk_str[6:])
                                    usage = data.get("usage", {})
                                    prompt_tokens = usage.get("prompt_tokens", 0)
                                    completion_tokens = usage.get(
                                        "completion_tokens", 0
                                    )
                                    total_tokens = usage.get("total_tokens", 0)
                            except Exception:
                                pass
                            yield chunk
                # 流结束后记录完整 token 数量
                log_completion(
                    selected,
                    model,
                    call_time_stamp,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                )
                await check_and_remove_key(selected)
            except Exception as e:
                error_json = json.dumps({"error": f"请求失败: {str(e)}"}).encode(
                    "utf-8"
                )
                yield f"data: {error_json}\n\n".encode("utf-8")
                yield b"data: [DONE]\n\n"

        try:
            return StreamingResponse(
                generate_stream(),
                headers={"Content-Type": "application/octet-stream"},
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"请求转发失败: {str(e)}")
    else:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{BASE_URL}/v1/completions",
                    headers=forward_headers,
                    data=req_body,
                    timeout=300,
                ) as resp:
                    resp_json = await resp.json()
                    usage = resp_json.get("usage", {})
                    prompt_tokens = usage.get("prompt_tokens", 0)
                    completion_tokens = usage.get("completion_tokens", 0)
                    total_tokens = usage.get("total_tokens", 0)

                    # Log completion call
                    log_completion(
                        selected,
                        model,
                        call_time_stamp,
                        prompt_tokens,
                        completion_tokens,
                        total_tokens,
                    )

                    # 后台检查 key 余额
                    background_tasks.add_task(check_and_remove_key, selected)
                    return JSONResponse(content=resp_json, status_code=resp.status)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"请求转发失败: {str(e)}")


@app.get("/v1/models")
async def list_models(request: Request):
    cursor.execute("SELECT key, balance FROM api_keys")
    keys_with_balance = cursor.fetchall()
    if not keys_with_balance:
        raise HTTPException(status_code=500, detail="没有可用的api-key")
    selected = select_api_key(keys_with_balance)
    forward_headers = dict(request.headers)
    forward_headers["Authorization"] = f"Bearer {selected}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{BASE_URL}/v1/models", headers=forward_headers, timeout=30
            ) as resp:
                data = await resp.json()
                return JSONResponse(content=data, status_code=resp.status)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"请求转发失败: {str(e)}")


@app.get("/stats")
async def stats():
    cursor.execute("SELECT COUNT(*), COALESCE(SUM(balance), 0) FROM api_keys")
    count, total_balance = cursor.fetchone()
    return JSONResponse({"key_count": count, "total_balance": total_balance})


@app.get("/export_keys")
async def export_keys():
    cursor.execute("SELECT key FROM api_keys")
    all_keys = cursor.fetchall()
    keys = "\n".join(row[0] for row in all_keys)
    headers = {"Content-Disposition": "attachment; filename=keys.txt"}
    return Response(content=keys, media_type="text/plain", headers=headers)


@app.get("/logs")
async def get_logs(page: int = 1):
    page_size = 10
    offset = (page - 1) * page_size
    cursor.execute("SELECT COUNT(*) FROM logs")
    total = cursor.fetchone()[0]
    cursor.execute(
        "SELECT used_key, model, call_time, input_tokens, output_tokens, total_tokens FROM logs ORDER BY call_time DESC LIMIT ? OFFSET ?",
        (page_size, offset),
    )
    logs = cursor.fetchall()
    # Format logs as list of dicts
    log_list = [
        {
            "used_key": row[0],
            "model": row[1],
            "call_time": row[2],
            "input_tokens": row[3],
            "output_tokens": row[4],
            "total_tokens": row[5],
        }
        for row in logs
    ]
    return JSONResponse(
        {"logs": log_list, "total": total, "page": page, "page_size": page_size}
    )


@app.post("/clear_logs")
async def clear_logs():
    try:
        cursor.execute("DELETE FROM logs")
        conn.commit()
        cursor.execute("VACUUM")
        conn.commit()
        return JSONResponse({"message": "日志已清空"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"清空日志失败: {str(e)}")


@app.get("/config/strategy")
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


@app.post("/config/strategy")
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


# 新增接口：获取自定义 api_key 配置
@app.get("/config/custom_api_key")
async def get_custom_api_key():
    return JSONResponse({"custom_api_key": config.CUSTOM_API_KEY})


# 新增接口：更新自定义 api_key配置，{"custom_api_key": "值"}，空字符串表示不使用
@app.post("/config/custom_api_key")
async def set_custom_api_key(request: Request):
    data = await request.json()
    new_key = data.get("custom_api_key", "")
    update_custom_api_key(new_key)
    return JSONResponse(
        {"message": "自定义 api_key 更新成功", "custom_api_key": new_key}
    )


@app.options("/v1/chat/completions")
async def options_chat_completions():
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
        },
    )


@app.options("/v1/embeddings")
async def options_embeddings():
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
        },
    )


@app.options("/v1/completions")
async def options_completions():
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
        },
    )


@app.get("/api/keys")
async def get_keys(
    page: int = 1, sort_field: str = "add_time", sort_order: str = "desc"
):
    allowed_fields = ["add_time", "balance", "usage_count"]
    allowed_orders = ["asc", "desc"]

    if sort_field not in allowed_fields:
        sort_field = "add_time"
    if sort_order not in allowed_orders:
        sort_order = "desc"

    page_size = 10
    offset = (page - 1) * page_size

    cursor.execute("SELECT COUNT(*) FROM api_keys")
    total = cursor.fetchone()[0]

    cursor.execute(
        f"SELECT key, add_time, balance, usage_count FROM api_keys ORDER BY {sort_field} {sort_order} LIMIT ? OFFSET ?",
        (page_size, offset),
    )
    keys = cursor.fetchall()

    # Format keys as list of dicts
    key_list = [
        {
            "key": row[0],
            "add_time": row[1],
            "balance": row[2],
            "usage_count": row[3],
        }
        for row in keys
    ]

    return JSONResponse(
        {"keys": key_list, "total": total, "page": page, "page_size": page_size}
    )


@app.post("/api/refresh_key")
async def refresh_single_key(request: Request):
    data = await request.json()
    key = data.get("key")

    if not key:
        raise HTTPException(status_code=400, detail="未提供API密钥")

    try:
        valid, balance = await validate_key_async(key)

        if valid and float(balance) > 0:
            cursor.execute(
                "UPDATE api_keys SET balance = ? WHERE key = ?", (balance, key)
            )
            conn.commit()
            return JSONResponse({"message": f"密钥更新成功，当前余额: ¥{balance}"})
        else:
            cursor.execute("DELETE FROM api_keys WHERE key = ?", (key,))
            conn.commit()
            return JSONResponse({"message": "密钥已失效或余额为0，已从池中移除"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"刷新密钥失败: {str(e)}")


@app.post("/api/delete_key")
async def delete_key(request: Request):
    data = await request.json()
    key = data.get("key")

    if not key:
        raise HTTPException(status_code=400, detail="未提供API密钥")

    try:
        cursor.execute("DELETE FROM api_keys WHERE key = ?", (key,))
        conn.commit()
        return JSONResponse({"message": "密钥已成功删除"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除密钥失败: {str(e)}")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=7898)
