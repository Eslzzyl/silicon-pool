from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.requests import ClientDisconnect
import config
import json
import time
import aiohttp
from db import conn, cursor, log_completion
from utils import select_api_key, check_and_remove_key

router = APIRouter()

# API基础URL
BASE_URL = "https://api.siliconflow.cn"


@router.post("/v1/chat/completions")
async def chat_completions(request: Request, background_tasks: BackgroundTasks):
    # 检查是否应该使用余额为0的key
    use_zero_balance = False
    if config.FREE_MODEL_API_KEY and config.FREE_MODEL_API_KEY.strip():
        request_api_key = request.headers.get("Authorization", "")
        if request_api_key == f"Bearer {config.FREE_MODEL_API_KEY}":
            use_zero_balance = True

    # 如果不使用余额为0的key，检查自定义API KEY
    if not use_zero_balance and config.CUSTOM_API_KEY and config.CUSTOM_API_KEY.strip():
        request_api_key = request.headers.get("Authorization")
        if request_api_key != f"Bearer {config.CUSTOM_API_KEY}":
            raise HTTPException(status_code=403, detail="无效的API_KEY")

    cursor.execute("SELECT key, balance FROM api_keys WHERE enabled = 1")
    keys_with_balance = cursor.fetchall()
    if not keys_with_balance:
        raise HTTPException(status_code=500, detail="没有可用的api-key")

    selected = select_api_key(keys_with_balance, use_zero_balance)
    if not selected:
        if use_zero_balance:
            raise HTTPException(status_code=500, detail="没有余额为0的可用api-key")
        else:
            raise HTTPException(status_code=500, detail="没有可用的api-key")

    # 增加使用计数
    cursor.execute(
        "UPDATE api_keys SET usage_count = usage_count + 1 WHERE key = ?", (selected,)
    )
    conn.commit()

    # 使用选定的key转发请求到BASE_URL
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
                        f"{BASE_URL}/v1/chat/completions",
                        headers=forward_headers,
                        data=req_body,
                        timeout=1800,
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

                # 流结束后记录完整token数量
                log_completion(
                    selected,
                    model,
                    call_time_stamp,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    "chat_completions",
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
                    timeout=1800,
                ) as resp:
                    resp_json = await resp.json()
                    usage = resp_json.get("usage", {})
                    prompt_tokens = usage.get("prompt_tokens", 0)
                    completion_tokens = usage.get("completion_tokens", 0)
                    total_tokens = usage.get("total_tokens", 0)

                    # 记录完成调用
                    log_completion(
                        selected,
                        model,
                        call_time_stamp,
                        prompt_tokens,
                        completion_tokens,
                        total_tokens,
                        "chat_completions",
                    )

                    # 后台检查key余额
                    background_tasks.add_task(check_and_remove_key, selected)
                    return JSONResponse(content=resp_json, status_code=resp.status)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"请求转发失败: {str(e)}")


@router.post("/v1/embeddings")
async def embeddings(request: Request, background_tasks: BackgroundTasks):
    # 检查是否应该使用余额为0的key
    use_zero_balance = False
    if config.FREE_MODEL_API_KEY and config.FREE_MODEL_API_KEY.strip():
        request_api_key = request.headers.get("Authorization", "")
        if request_api_key == f"Bearer {config.FREE_MODEL_API_KEY}":
            use_zero_balance = True

    # 如果不使用余额为0的key，检查自定义API KEY
    if not use_zero_balance and config.CUSTOM_API_KEY and config.CUSTOM_API_KEY.strip():
        request_api_key = request.headers.get("Authorization")
        if request_api_key != f"Bearer {config.CUSTOM_API_KEY}":
            raise HTTPException(status_code=403, detail="无效的API_KEY")

    cursor.execute("SELECT key, balance FROM api_keys WHERE enabled = 1")
    keys_with_balance = cursor.fetchall()
    if not keys_with_balance:
        raise HTTPException(status_code=500, detail="没有可用的api-key")

    selected = select_api_key(keys_with_balance, use_zero_balance)
    if not selected:
        if use_zero_balance:
            raise HTTPException(status_code=500, detail="没有余额为0的可用api-key")
        else:
            raise HTTPException(status_code=500, detail="没有可用的api-key")

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
                # 记录嵌入调用
                req_json = await request.json()
                model = req_json.get("model", "unknown")
                resp_json = await resp.json()
                usage = resp_json.get("usage", {})
                prompt_tokens = usage.get("prompt_tokens", 0)
                call_time_stamp = time.time()

                log_completion(
                    selected,
                    model,
                    call_time_stamp,
                    prompt_tokens,
                    0,
                    prompt_tokens,
                    "embeddings",
                )

                # 后台检查key余额
                background_tasks.add_task(check_and_remove_key, selected)
                return JSONResponse(content=data, status_code=resp.status)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"请求转发失败: {str(e)}")


@router.post("/v1/completions")
async def completions(request: Request, background_tasks: BackgroundTasks):
    # 检查是否应该使用余额为0的key
    use_zero_balance = False
    if config.FREE_MODEL_API_KEY and config.FREE_MODEL_API_KEY.strip():
        request_api_key = request.headers.get("Authorization", "")
        if request_api_key == f"Bearer {config.FREE_MODEL_API_KEY}":
            use_zero_balance = True

    # 如果不使用余额为0的key，检查自定义API KEY
    if not use_zero_balance and config.CUSTOM_API_KEY and config.CUSTOM_API_KEY.strip():
        request_api_key = request.headers.get("Authorization")
        if request_api_key != f"Bearer {config.CUSTOM_API_KEY}":
            raise HTTPException(status_code=403, detail="无效的API_KEY")

    cursor.execute("SELECT key, balance FROM api_keys WHERE enabled = 1")
    keys_with_balance = cursor.fetchall()
    if not keys_with_balance:
        raise HTTPException(status_code=500, detail="没有可用的api-key")

    selected = select_api_key(keys_with_balance, use_zero_balance)
    if not selected:
        if use_zero_balance:
            raise HTTPException(status_code=500, detail="没有余额为0的可用api-key")
        else:
            raise HTTPException(status_code=500, detail="没有可用的api-key")

    # 增加使用计数
    cursor.execute(
        "UPDATE api_keys SET usage_count = usage_count + 1 WHERE key = ?", (selected,)
    )
    conn.commit()

    # 使用选定的key转发请求到BASE_URL
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

                # 流结束后记录完整token数量
                log_completion(
                    selected,
                    model,
                    call_time_stamp,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    "completions",
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

                    # 记录完成调用
                    log_completion(
                        selected,
                        model,
                        call_time_stamp,
                        prompt_tokens,
                        completion_tokens,
                        total_tokens,
                        "completions",
                    )

                    # 后台检查key余额
                    background_tasks.add_task(check_and_remove_key, selected)
                    return JSONResponse(content=resp_json, status_code=resp.status)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"请求转发失败: {str(e)}")


@router.post("/v1/images/generations")
async def images_generations(request: Request, background_tasks: BackgroundTasks):
    if config.CUSTOM_API_KEY and config.CUSTOM_API_KEY.strip():
        request_api_key = request.headers.get("Authorization")
        if request_api_key != f"Bearer {config.CUSTOM_API_KEY}":
            raise HTTPException(status_code=403, detail="无效的API_KEY")

    cursor.execute("SELECT key, balance FROM api_keys WHERE enabled = 1")
    keys_with_balance = cursor.fetchall()
    if not keys_with_balance:
        raise HTTPException(status_code=500, detail="没有可用的api-key")

    selected = select_api_key(keys_with_balance)
    if not selected:
        raise HTTPException(status_code=500, detail="没有可用的api-key")

    # 增加使用计数
    cursor.execute(
        "UPDATE api_keys SET usage_count = usage_count + 1 WHERE key = ?", (selected,)
    )
    conn.commit()

    forward_headers = dict(request.headers)
    forward_headers["Authorization"] = f"Bearer {selected}"

    try:
        req_body = await request.body()
        req_json = await request.json()
        model = req_json.get("model", "unknown")
        call_time_stamp = time.time()

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{BASE_URL}/v1/images/generations",
                headers=forward_headers,
                data=req_body,
                timeout=120,  # 图像生成可能需要更长时间
            ) as resp:
                data = await resp.json()

                # 图像生成接口可能没有token信息，设置为0
                prompt_tokens = 0
                completion_tokens = 0
                total_tokens = 0

                # 记录API调用
                log_completion(
                    selected,
                    model,
                    call_time_stamp,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    "images_generations",
                )

                # 后台检查key余额
                background_tasks.add_task(check_and_remove_key, selected)
                return JSONResponse(content=data, status_code=resp.status)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"请求转发失败: {str(e)}")


@router.options("/v1/images/generations")
async def options_images_generations():
    """处理CORS预检请求"""
    from fastapi.responses import Response

    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
        },
    )


@router.post("/v1/rerank")
async def rerank(request: Request, background_tasks: BackgroundTasks):
    # 检查是否应该使用余额为0的key
    use_zero_balance = False
    if config.FREE_MODEL_API_KEY and config.FREE_MODEL_API_KEY.strip():
        request_api_key = request.headers.get("Authorization", "")
        if request_api_key == f"Bearer {config.FREE_MODEL_API_KEY}":
            use_zero_balance = True

    # 如果不使用余额为0的key，检查自定义API KEY
    if not use_zero_balance and config.CUSTOM_API_KEY and config.CUSTOM_API_KEY.strip():
        request_api_key = request.headers.get("Authorization")
        if request_api_key != f"Bearer {config.CUSTOM_API_KEY}":
            raise HTTPException(status_code=403, detail="无效的API_KEY")

    cursor.execute("SELECT key, balance FROM api_keys WHERE enabled = 1")
    keys_with_balance = cursor.fetchall()
    if not keys_with_balance:
        raise HTTPException(status_code=500, detail="没有可用的api-key")

    selected = select_api_key(keys_with_balance, use_zero_balance)
    if not selected:
        if use_zero_balance:
            raise HTTPException(status_code=500, detail="没有余额为0的可用api-key")
        else:
            raise HTTPException(status_code=500, detail="没有可用的api-key")

    # 增加使用计数
    cursor.execute(
        "UPDATE api_keys SET usage_count = usage_count + 1 WHERE key = ?", (selected,)
    )
    conn.commit()

    # 使用选定的key转发请求到BASE_URL
    forward_headers = dict(request.headers)
    forward_headers["Authorization"] = f"Bearer {selected}"

    try:
        req_body = await request.body()
    except ClientDisconnect:
        return JSONResponse({"error": "客户端断开连接"}, status_code=499)

    req_json = await request.json()
    model = req_json.get("model", "unknown")
    call_time_stamp = time.time()

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{BASE_URL}/v1/rerank",
                headers=forward_headers,
                data=req_body,
                timeout=300,
            ) as resp:
                resp_json = await resp.json()
                meta_data = resp_json.get("meta", {})
                tokens_usage = meta_data.get("tokens", {})
                input_tokens = tokens_usage.get("input_tokens", 0)
                output_tokens = tokens_usage.get("output_tokens", 0)
                # 记录API调用
                log_completion(
                    selected,
                    model,
                    call_time_stamp,
                    input_tokens,  # prompt_tokens
                    output_tokens,  # completion_tokens
                    input_tokens + output_tokens,  # total_tokens
                    "rerank",
                )
                # 后台检查key余额
                background_tasks.add_task(check_and_remove_key, selected)
                return JSONResponse(content=resp_json, status_code=resp.status)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"请求转发失败: {str(e)}")


@router.get("/v1/models")
async def list_models(request: Request):
    cursor.execute("SELECT key, balance FROM api_keys WHERE enabled = 1")
    keys_with_balance = cursor.fetchall()
    if not keys_with_balance:
        raise HTTPException(status_code=500, detail="没有可用的api-key")

    selected = select_api_key(keys_with_balance)
    if not selected:
        raise HTTPException(status_code=500, detail="没有可用的api-key")

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
