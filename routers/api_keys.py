from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse, Response
import asyncio
from db import cursor, update_api_key, delete_api_key, flush_cache
from utils import validate_key_async, validate_key_format, clean_key

router = APIRouter()


@router.get("/api/keys")
async def get_keys(
    page: int = 1, sort_field: str = "add_time", sort_order: str = "desc"
):
    allowed_fields = ["add_time", "balance", "usage_count", "enabled", "key"]
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
        f"SELECT key, add_time, balance, usage_count, enabled FROM api_keys ORDER BY {sort_field} {sort_order} LIMIT ? OFFSET ?",
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
            "enabled": bool(row[4]),
        }
        for row in keys
    ]

    return JSONResponse(
        {"keys": key_list, "total": total, "page": page, "page_size": page_size}
    )


@router.post("/api/refresh_key")
async def refresh_single_key(request: Request):
    data = await request.json()
    key = data.get("key")

    if not key:
        raise HTTPException(status_code=400, detail="未提供API密钥")

    try:
        valid, balance = await validate_key_async(key)

        if valid and float(balance) > 0:
            # 使用缓存系统更新
            update_api_key({"balance": balance}, key)
            return JSONResponse({"message": f"密钥更新成功，当前余额: ¥{balance}"})
        else:
            # 使用缓存系统删除
            delete_api_key(key)
            return JSONResponse({"message": "密钥已失效或余额为0，已从池中移除"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"刷新密钥失败: {str(e)}")


@router.post("/api/delete_key")
async def delete_key(request: Request):
    data = await request.json()
    key = data.get("key")

    if not key:
        raise HTTPException(status_code=400, detail="未提供API密钥")

    try:
        # 使用缓存系统删除
        delete_api_key(key)
        return JSONResponse({"message": "密钥已成功删除"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除密钥失败: {str(e)}")


@router.post("/api/toggle_key")
async def toggle_key(request: Request):
    data = await request.json()
    key = data.get("key")
    enabled = data.get("enabled")

    if not key:
        raise HTTPException(status_code=400, detail="未提供API密钥")

    if enabled is None:
        raise HTTPException(status_code=400, detail="未提供启用状态")

    try:
        # 使用缓存系统更新
        update_api_key({"enabled": 1 if enabled else 0}, key)
        status = "启用" if enabled else "禁用"
        return JSONResponse({"message": f"密钥已成功{status}"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"更新密钥状态失败: {str(e)}")


@router.post("/import_keys")
async def import_keys(request: Request):
    data = await request.json()
    keys_text = data.get("keys", "")

    # 清理和验证密钥
    raw_keys = [k.strip() for k in keys_text.splitlines() if k.strip()]
    cleaned_keys = [clean_key(k) for k in raw_keys]
    keys = [k for k in cleaned_keys if validate_key_format(k)]

    invalid_format_count = len(raw_keys) - len(keys)

    if not keys:
        return JSONResponse({"message": "未提供有效的 API Key"}, status_code=400)

    tasks = []
    # 准备任务：对于重复的密钥，添加一个返回标记的虚拟任务
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
                from db import insert_api_key

                insert_api_key(keys[idx], balance)
                imported_count += 1
            else:
                invalid_count += 1
    
    # 导入后刷新缓存，确保大量导入的数据被写入数据库
    flush_cache()

    return JSONResponse(
        {
            "message": f"导入成功 {imported_count} 个，有重复 {duplicate_count} 个，格式无效 {invalid_format_count} 个，API 验证失败 {invalid_count} 个"
        }
    )


@router.post("/refresh")
async def refresh_keys():
    cursor.execute("SELECT key, balance FROM api_keys")
    key_balance_map = {row[0]: row[1] for row in cursor.fetchall()}
    all_keys = list(key_balance_map.keys())

    # 获取初始总余额
    initial_balance = sum(key_balance_map.values())

    # 创建并行验证任务
    tasks = [validate_key_async(key) for key in all_keys]
    results = await asyncio.gather(*tasks)

    removed = 0
    for key, (valid, balance) in zip(all_keys, results):
        if valid and float(balance) > 0:
            # 使用缓存系统更新
            update_api_key({"balance": balance}, key)
        else:
            # 使用缓存系统删除
            delete_api_key(key)
            removed += 1

    # 刷新缓存，确保大量更新操作被写入数据库
    flush_cache()

    # 计算新的总余额
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


@router.get("/export_keys")
async def export_keys():
    cursor.execute("SELECT key FROM api_keys")
    all_keys = cursor.fetchall()
    keys = "\n".join(row[0] for row in all_keys)
    headers = {"Content-Disposition": "attachment; filename=keys.txt"}
    return Response(content=keys, media_type="text/plain", headers=headers)


@router.get("/stats")
async def stats():
    cursor.execute("SELECT COUNT(*), COALESCE(SUM(balance), 0) FROM api_keys")
    count, total_balance = cursor.fetchone()
    return JSONResponse({"key_count": count, "total_balance": total_balance})


# CORS预检请求处理
@router.options("/v1/chat/completions")
async def options_chat_completions():
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
        },
    )


@router.options("/v1/embeddings")
async def options_embeddings():
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
        },
    )


@router.options("/v1/completions")
async def options_completions():
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
        },
    )
