from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.requests import ClientDisconnect
import config
import json
import time
import aiohttp
import asyncio
from db import conn, cursor, log_completion
from utils import select_api_key, check_and_remove_key
from rate_limiter import track_key_usage, check_key_limits
from request_queue import request_queue_manager
import logging

# 配置日志
logger = logging.getLogger(__name__)

router = APIRouter()

# API基础URL
BASE_URL = "https://api.siliconflow.cn"


# 封装API请求函数，用于队列管理
async def _execute_chat_completion(request_body, forward_headers, is_stream, selected, use_zero_balance, model, call_time_stamp, background_tasks):
    """执行实际的API请求
    
    Args:
        request_body: 请求体
        forward_headers: 转发的请求头
        is_stream: 是否为流式请求
        selected: 选择的API密钥
        use_zero_balance: 是否使用余额为0的密钥
        model: 模型名称
        call_time_stamp: 调用时间戳
        background_tasks: 后台任务
        
    Returns:
        API响应
    """
    if is_stream:
        async def generate_stream():
            completion_tokens = 0
            prompt_tokens = 0
            total_tokens = 0

            try:
                async with aiohttp.ClientSession() as session:
                    try:
                        async with session.post(
                            f"{BASE_URL}/v1/chat/completions",
                            headers=forward_headers,
                            data=request_body,
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
                    except Exception as api_error:
                        # API调用失败，禁用当前API密钥
                        from utils import disable_api_key
                        disable_api_key(selected)
                        
                        # 如果是轮询策略，尝试使用下一个可用的API密钥
                        if config.CALL_STRATEGY == "round_robin":
                            # 重新获取可用的API密钥
                            cursor.execute("SELECT key, balance FROM api_keys WHERE enabled = 1")
                            new_keys_with_balance = cursor.fetchall()
                            if new_keys_with_balance:
                                new_selected = select_api_key(new_keys_with_balance, use_zero_balance)
                                if new_selected:
                                    # 使用新选择的key重新尝试请求
                                    forward_headers["Authorization"] = f"Bearer {new_selected}"
                                    async with session.post(
                                        f"{BASE_URL}/v1/chat/completions",
                                        headers=forward_headers,
                                        data=request_body,
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
                                            new_selected,
                                            model,
                                            call_time_stamp,
                                            prompt_tokens,
                                            completion_tokens,
                                            total_tokens,
                                            "chat_completions",
                                        )
                                        # 记录API密钥使用情况，用于RPM和TPM限制
                                        track_key_usage(new_selected, 1, total_tokens)
                                        await check_and_remove_key(new_selected)
                                        return
                        # 如果不是轮询策略或者重试失败，则抛出原始异常
                        raise api_error

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
                # 记录API密钥使用情况，用于RPM和TPM限制
                track_key_usage(selected, 1, total_tokens)
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
                try:
                    async with session.post(
                        f"{BASE_URL}/v1/chat/completions",
                        headers=forward_headers,
                        data=request_body,
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
                        # 记录API密钥使用情况，用于RPM和TPM限制
                        track_key_usage(selected, 1, total_tokens)

                        # 后台检查key余额
                        background_tasks.add_task(check_and_remove_key, selected)
                        return JSONResponse(content=resp_json, status_code=resp.status)
                except Exception as api_error:
                    # API调用失败，禁用当前API密钥
                    from utils import disable_api_key
                    disable_api_key(selected)
                    
                    # 如果是轮询策略，尝试使用下一个可用的API密钥
                    if config.CALL_STRATEGY == "round_robin":
                        # 重新获取可用的API密钥
                        cursor.execute("SELECT key, balance FROM api_keys WHERE enabled = 1")
                        new_keys_with_balance = cursor.fetchall()
                        if new_keys_with_balance:
                            new_selected = select_api_key(new_keys_with_balance, use_zero_balance)
                            if new_selected:
                                # 使用新选择的key重新尝试请求
                                forward_headers["Authorization"] = f"Bearer {new_selected}"
                                async with session.post(
                                    f"{BASE_URL}/v1/chat/completions",
                                    headers=forward_headers,
                                    data=request_body,
                                    timeout=1800,
                                ) as resp:
                                    resp_json = await resp.json()
                                    usage = resp_json.get("usage", {})
                                    prompt_tokens = usage.get("prompt_tokens", 0)
                                    completion_tokens = usage.get("completion_tokens", 0)
                                    total_tokens = usage.get("total_tokens", 0)
                                    # 记录API调用
                                    log_completion(
                                        new_selected,
                                        model,
                                        call_time_stamp,
                                        prompt_tokens,
                                        completion_tokens,
                                        total_tokens,
                                        "chat_completions",
                                    )
                                    # 记录API密钥使用情况，用于RPM和TPM限制
                                    track_key_usage(new_selected, 1, total_tokens)
                                    # 后台检查key余额
                                    background_tasks.add_task(check_and_remove_key, new_selected)
                                    return JSONResponse(content=resp_json, status_code=resp.status)
                    # 如果不是轮询策略或者重试失败，则抛出原始异常
                    raise api_error
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"请求转发失败: {str(e)}")

@router.post("/v1/chat/completions")
async def chat_completions(request: Request, background_tasks: BackgroundTasks):
    """处理聊天完成请求，使用请求队列管理高并发"""
    try:
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
        
        # 使用请求队列管理器处理请求，避免高并发导致的EOF错误
        logger.info(f"将chat_completions请求加入队列处理，当前队列状态: {request_queue_manager.get_stats()}")
        try:
            # 将请求加入队列处理
            return await request_queue_manager.enqueue_request(
                _execute_chat_completion,
                req_body,
                forward_headers,
                is_stream,
                selected,
                use_zero_balance,
                model,
                call_time_stamp,
                background_tasks
            )
        except asyncio.TimeoutError:
            # 请求在队列中等待超时
            raise HTTPException(status_code=503, detail="服务器繁忙，请稍后重试")
        except Exception as e:
            logger.error(f"队列处理chat_completions请求失败: {str(e)}")
            raise HTTPException(status_code=500, detail=f"请求处理失败: {str(e)}")
    except Exception as e:
        logger.error(f"处理聊天完成请求失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"请求处理失败: {str(e)}")

        async def generate_stream():
            completion_tokens = 0
            prompt_tokens = 0
            total_tokens = 0

            try:
                async with aiohttp.ClientSession() as session:
                    try:
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
                    except Exception as api_error:
                        # API调用失败，禁用当前API密钥
                        from utils import disable_api_key
                        disable_api_key(selected)
                        
                        # 如果是轮询策略，尝试使用下一个可用的API密钥
                        if config.CALL_STRATEGY == "round_robin":
                            # 重新获取可用的API密钥
                            cursor.execute("SELECT key, balance FROM api_keys WHERE enabled = 1")
                            new_keys_with_balance = cursor.fetchall()
                            if new_keys_with_balance:
                                new_selected = select_api_key(new_keys_with_balance, use_zero_balance)
                                if new_selected:
                                    # 使用新选择的key重新尝试请求
                                    forward_headers["Authorization"] = f"Bearer {new_selected}"
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
                                            new_selected,
                                            model,
                                            call_time_stamp,
                                            prompt_tokens,
                                            completion_tokens,
                                            total_tokens,
                                            "chat_completions",
                                        )
                                        # 记录API密钥使用情况，用于RPM和TPM限制
                                        track_key_usage(new_selected, 1, total_tokens)
                                        await check_and_remove_key(new_selected)
                                        return
                        # 如果不是轮询策略或者重试失败，则抛出原始异常
                        raise api_error

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
                # 记录API密钥使用情况，用于RPM和TPM限制
                track_key_usage(selected, 1, total_tokens)
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
                try:
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
                        # 记录API密钥使用情况，用于RPM和TPM限制
                        track_key_usage(selected, 1, total_tokens)

                        # 后台检查key余额
                        background_tasks.add_task(check_and_remove_key, selected)
                        return JSONResponse(content=resp_json, status_code=resp.status)
                except Exception as api_error:
                    # API调用失败，禁用当前API密钥
                    from utils import disable_api_key
                    disable_api_key(selected)
                    
                    # 如果是轮询策略，尝试使用下一个可用的API密钥
                    if config.CALL_STRATEGY == "round_robin":
                        # 重新获取可用的API密钥
                        cursor.execute("SELECT key, balance FROM api_keys WHERE enabled = 1")
                        new_keys_with_balance = cursor.fetchall()
                        if new_keys_with_balance:
                            new_selected = select_api_key(new_keys_with_balance, use_zero_balance)
                            if new_selected:
                                # 使用新选择的key重新尝试请求
                                forward_headers["Authorization"] = f"Bearer {new_selected}"
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
                                    # 记录API调用
                                    log_completion(
                                        new_selected,
                                        model,
                                        call_time_stamp,
                                        prompt_tokens,
                                        completion_tokens,
                                        total_tokens,
                                        "chat_completions",
                                    )
                                    # 记录API密钥使用情况，用于RPM和TPM限制
                                    track_key_usage(new_selected, 1, total_tokens)
                                    # 后台检查key余额
                                    background_tasks.add_task(check_and_remove_key, new_selected)
                                    return JSONResponse(content=resp_json, status_code=resp.status)
                    # 如果不是轮询策略或者重试失败，则抛出原始异常
                    raise api_error
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"请求转发失败: {str(e)}")


# 封装embeddings API请求函数，用于队列管理
async def _execute_embeddings(request_body, forward_headers, selected, model, call_time_stamp, background_tasks):
    """执行实际的embeddings API请求
    
    Args:
        request_body: 请求体
        forward_headers: 转发的请求头
        selected: 选择的API密钥
        model: 模型名称
        call_time_stamp: 调用时间戳
        background_tasks: 后台任务
        
    Returns:
        API响应
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{BASE_URL}/v1/embeddings",
                headers=forward_headers,
                data=request_body,
                timeout=30,
            ) as resp:
                resp_json = await resp.json()
                usage = resp_json.get("usage", {})
                prompt_tokens = usage.get("prompt_tokens", 0)
                total_tokens = usage.get("total_tokens", 0)
                
                # 记录嵌入调用
                log_completion(
                    selected,
                    model,
                    call_time_stamp,
                    prompt_tokens,
                    0,  # 嵌入没有完成令牌
                    total_tokens,
                    "embeddings",
                )
                
                # 记录API密钥使用情况
                track_key_usage(selected, 1, total_tokens)
                
                # 后台检查key余额
                background_tasks.add_task(check_and_remove_key, selected)
                
                return JSONResponse(content=resp_json, status_code=resp.status)
    except Exception as e:
        # API调用失败，禁用当前API密钥
        from utils import disable_api_key
        disable_api_key(selected)
        raise HTTPException(status_code=500, detail=f"请求转发失败: {str(e)}")

@router.post("/v1/embeddings")
async def embeddings(request: Request, background_tasks: BackgroundTasks):
    """处理嵌入请求，使用请求队列管理高并发"""
    try:
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

        forward_headers = dict(request.headers)
        forward_headers["Authorization"] = f"Bearer {selected}"

        try:
            req_body = await request.body()
        except ClientDisconnect:
            return JSONResponse({"error": "客户端断开连接"}, status_code=499)

        req_json = await request.json()
        model = req_json.get("model", "unknown")
        call_time_stamp = time.time()
        
        # 使用请求队列管理器处理请求，避免高并发导致的EOF错误
        logger.info(f"将嵌入请求加入队列处理，当前队列状态: {request_queue_manager.get_stats()}")
        try:
            # 将请求加入队列处理
            return await request_queue_manager.enqueue_request(
                _execute_embeddings,
                req_body,
                forward_headers,
                selected,
                model,
                call_time_stamp,
                background_tasks
            )
        except asyncio.TimeoutError:
            # 请求在队列中等待超时
            raise HTTPException(status_code=503, detail="服务器繁忙，请稍后重试")
        except Exception as e:
            logger.error(f"队列处理嵌入请求失败: {str(e)}")
            raise HTTPException(status_code=500, detail=f"请求处理失败: {str(e)}")
    except Exception as e:
        logger.error(f"处理嵌入请求失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"请求处理失败: {str(e)}")


# 封装completions API请求函数，用于队列管理
async def _execute_completions(request_body, forward_headers, is_stream, selected, use_zero_balance, model, call_time_stamp, background_tasks):
    """执行实际的completions API请求
    
    Args:
        request_body: 请求体
        forward_headers: 转发的请求头
        is_stream: 是否为流式请求
        selected: 选择的API密钥
        use_zero_balance: 是否使用余额为0的密钥
        model: 模型名称
        call_time_stamp: 调用时间戳
        background_tasks: 后台任务
        
    Returns:
        API响应
    """
    if is_stream:
        async def generate_stream():
            completion_tokens = 0
            prompt_tokens = 0
            total_tokens = 0

            try:
                async with aiohttp.ClientSession() as session:
                    try:
                        async with session.post(
                            f"{BASE_URL}/v1/completions",
                            headers=forward_headers,
                            data=request_body,
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
                    except Exception as api_error:
                        # API调用失败，禁用当前API密钥
                        from utils import disable_api_key
                        disable_api_key(selected)
                        
                        # 如果是轮询策略，尝试使用下一个可用的API密钥
                        if config.CALL_STRATEGY == "round_robin":
                            # 重新获取可用的API密钥
                            cursor.execute("SELECT key, balance FROM api_keys WHERE enabled = 1")
                            new_keys_with_balance = cursor.fetchall()
                            if new_keys_with_balance:
                                new_selected = select_api_key(new_keys_with_balance, use_zero_balance)
                                if new_selected:
                                    # 使用新选择的key重新尝试请求
                                    forward_headers["Authorization"] = f"Bearer {new_selected}"
                                    async with session.post(
                                        f"{BASE_URL}/v1/completions",
                                        headers=forward_headers,
                                        data=request_body,
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
                                            new_selected,
                                            model,
                                            call_time_stamp,
                                            prompt_tokens,
                                            completion_tokens,
                                            total_tokens,
                                            "completions",
                                        )
                                        # 记录API密钥使用情况，用于RPM和TPM限制
                                        track_key_usage(new_selected, 1, total_tokens)
                                        await check_and_remove_key(new_selected)
                                        return
                        # 如果不是轮询策略或者重试失败，则抛出原始异常
                        raise api_error

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
                # 记录API密钥使用情况，用于RPM和TPM限制
                track_key_usage(selected, 1, total_tokens)
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
                try:
                    async with session.post(
                        f"{BASE_URL}/v1/completions",
                        headers=forward_headers,
                        data=request_body,
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
                            "completions",
                        )
                        # 记录API密钥使用情况，用于RPM和TPM限制
                        track_key_usage(selected, 1, total_tokens)

                        # 后台检查key余额
                        background_tasks.add_task(check_and_remove_key, selected)
                        return JSONResponse(content=resp_json, status_code=resp.status)
                except Exception as api_error:
                    # API调用失败，禁用当前API密钥
                    from utils import disable_api_key
                    disable_api_key(selected)
                    
                    # 如果是轮询策略，尝试使用下一个可用的API密钥
                    if config.CALL_STRATEGY == "round_robin":
                        # 重新获取可用的API密钥
                        cursor.execute("SELECT key, balance FROM api_keys WHERE enabled = 1")
                        new_keys_with_balance = cursor.fetchall()
                        if new_keys_with_balance:
                            new_selected = select_api_key(new_keys_with_balance, use_zero_balance)
                            if new_selected:
                                # 使用新选择的key重新尝试请求
                                forward_headers["Authorization"] = f"Bearer {new_selected}"
                                async with session.post(
                                    f"{BASE_URL}/v1/completions",
                                    headers=forward_headers,
                                    data=request_body,
                                    timeout=1800,
                                ) as resp:
                                    resp_json = await resp.json()
                                    usage = resp_json.get("usage", {})
                                    prompt_tokens = usage.get("prompt_tokens", 0)
                                    completion_tokens = usage.get("completion_tokens", 0)
                                    total_tokens = usage.get("total_tokens", 0)
                                    # 记录API调用
                                    log_completion(
                                        new_selected,
                                        model,
                                        call_time_stamp,
                                        prompt_tokens,
                                        completion_tokens,
                                        total_tokens,
                                        "completions",
                                    )
                                    # 记录API密钥使用情况，用于RPM和TPM限制
                                    track_key_usage(new_selected, 1, total_tokens)
                                    # 后台检查key余额
                                    background_tasks.add_task(check_and_remove_key, new_selected)
                                    return JSONResponse(content=resp_json, status_code=resp.status)
                    # 如果不是轮询策略或者重试失败，则抛出原始异常
                    raise api_error
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"请求转发失败: {str(e)}")

@router.post("/v1/completions")
async def completions(request: Request, background_tasks: BackgroundTasks):
    """处理文本补全请求，使用请求队列管理高并发"""
    try:
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
        
        # 使用请求队列管理器处理请求，避免高并发导致的EOF错误
        logger.info(f"将completions请求加入队列处理，当前队列状态: {request_queue_manager.get_stats()}")
        try:
            # 将请求加入队列处理
            return await request_queue_manager.enqueue_request(
                _execute_completions,
                req_body,
                forward_headers,
                is_stream,
                selected,
                use_zero_balance,
                model,
                call_time_stamp,
                background_tasks
            )
        except asyncio.TimeoutError:
            # 请求在队列中等待超时
            raise HTTPException(status_code=503, detail="服务器繁忙，请稍后重试")
        except Exception as e:
            logger.error(f"队列处理completions请求失败: {str(e)}")
            raise HTTPException(status_code=500, detail=f"请求处理失败: {str(e)}")
    except Exception as e:
        logger.error(f"处理completions请求失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"请求处理失败: {str(e)}")


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
                # 记录API密钥使用情况，用于RPM和TPM限制
                track_key_usage(selected, 1, 0)  # 图像生成没有token信息，只记录请求数

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
                # 记录API密钥使用情况，用于RPM和TPM限制
                track_key_usage(selected, 1, input_tokens + output_tokens)
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
