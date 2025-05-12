from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse, Response
import asyncio
from db import conn, cursor
from utils import validate_key_async, validate_key_format, clean_key

router = APIRouter()


@router.get("/api/keys")
async def get_keys(
    page: int = 1,
    sort_field: str = "add_time",
    sort_order: str = "desc",
    balance_filter: str = "all",
):
    allowed_fields = ["add_time", "balance", "usage_count", "enabled", "key"]
    allowed_orders = ["asc", "desc"]
    allowed_filters = ["all", "positive", "zero"]

    if sort_field not in allowed_fields:
        sort_field = "add_time"
    if sort_order not in allowed_orders:
        sort_order = "desc"
    if balance_filter not in allowed_filters:
        balance_filter = "all"

    page_size = 10
    offset = (page - 1) * page_size

    # 根据余额筛选条件构建 SQL WHERE 子句
    filter_clause = ""
    if balance_filter == "positive":
        filter_clause = "WHERE balance > 0"
    elif balance_filter == "zero":
        filter_clause = "WHERE balance <= 0"

    # 计算总数
    count_sql = f"SELECT COUNT(*) FROM api_keys {filter_clause}"
    cursor.execute(count_sql)
    total = cursor.fetchone()[0]

    # 获取分页数据
    cursor.execute(
        f"SELECT key, add_time, balance, usage_count, enabled FROM api_keys {filter_clause} ORDER BY {sort_field} {sort_order} LIMIT ? OFFSET ?",
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


@router.post("/api/delete_key")
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
        cursor.execute(
            "UPDATE api_keys SET enabled = ? WHERE key = ?", (1 if enabled else 0, key)
        )
        conn.commit()
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
    zero_balance_count = 0

    for idx, result in enumerate(results):
        if result[0] == "duplicate":
            duplicate_count += 1
        else:
            valid, balance = result
            if valid:
                from db import insert_api_key

                insert_api_key(keys[idx], balance)
                imported_count += 1
                if float(balance) <= 0:
                    zero_balance_count += 1
            else:
                invalid_count += 1

    message = f"导入成功 {imported_count} 个"
    if zero_balance_count > 0:
        message += f"（其中 {zero_balance_count} 个余额用尽，可用于免费模型）"
    message += f"，有重复 {duplicate_count} 个，格式无效 {invalid_format_count} 个，API 验证失败 {invalid_count} 个"

    return JSONResponse({"message": message})


@router.post("/refresh")
async def refresh_keys():
    # 创建新的游标避免递归使用
    local_cursor = conn.cursor()

    try:
        # 在获取待筛选的key时仅获取余额大于0的key
        local_cursor.execute("SELECT key, balance FROM api_keys WHERE balance > 0")
        key_balance_map = {row[0]: row[1] for row in local_cursor.fetchall()}
        all_keys = list(key_balance_map.keys())

        # 获取初始总余额
        initial_balance = sum(key_balance_map.values())

        # 创建并行验证任务
        tasks = [validate_key_async(key) for key in all_keys]
        results = await asyncio.gather(*tasks)

        removed = 0
        updated = 0
        zero_balance = 0
        for key, (valid, balance) in zip(all_keys, results):
            if valid:
                local_cursor.execute(
                    "UPDATE api_keys SET balance = ? WHERE key = ?", (balance, key)
                )
                updated += 1
                if float(balance) <= 0:
                    zero_balance += 1
            else:
                local_cursor.execute("DELETE FROM api_keys WHERE key = ?", (key,))
                removed += 1

        conn.commit()

        # 计算新的总余额
        local_cursor.execute(
            "SELECT COALESCE(SUM(balance), 0) FROM api_keys WHERE balance > 0"
        )
        new_balance = local_cursor.fetchone()[0]
        balance_change = new_balance - initial_balance

        message = f"刷新完成，更新 {updated} 个 Key（其中 {zero_balance} 个余额用尽），移除 {removed} 个无效的 Key"
        if balance_change > 0:
            message += f"，余额增加了{round(balance_change, 2)}"
        else:
            balance_decrease = abs(balance_change)
            message += f"，余额减少了{round(balance_decrease, 2)}"

        return JSONResponse({"message": message})
    finally:
        # 确保游标被关闭
        local_cursor.close()


@router.get("/export_keys")
async def export_keys(
    format: str = "line", sort: str = "balance_desc", filter: str = "all"
):
    # 根据排序方式构建SQL语句
    sort_sql = ""
    if sort == "balance_desc":
        sort_sql = "ORDER BY balance DESC"
    elif sort == "balance_asc":
        sort_sql = "ORDER BY balance ASC"
    elif sort == "key_asc":
        sort_sql = "ORDER BY key ASC"
    elif sort == "key_desc":
        sort_sql = "ORDER BY key DESC"

    # 添加余额过滤
    filter_sql = ""
    if filter == "positive":
        filter_sql = "WHERE balance > 0"
    elif filter == "zero":
        filter_sql = "WHERE balance <= 0"

    # 执行查询
    cursor.execute(f"SELECT key, balance FROM api_keys {filter_sql} {sort_sql}")
    all_keys = cursor.fetchall()

    # 根据格式生成导出内容
    content = ""
    if format == "line":
        content = "\n".join(row[0] for row in all_keys)
    elif format == "line_with_balance":
        content = "\n".join(f"{row[0]} (余额: ¥{row[1]:.2f})" for row in all_keys)
    elif format == "csv":
        content = ",".join(row[0] for row in all_keys)

    headers = {"Content-Disposition": "attachment; filename=keys.txt"}
    return Response(content=content, media_type="text/plain", headers=headers)


@router.get("/stats")
async def stats():
    # Get count and total balance of keys with positive balance
    cursor.execute(
        "SELECT COUNT(*), COALESCE(SUM(balance), 0) FROM api_keys WHERE balance > 0"
    )
    positive_count, total_balance = cursor.fetchone()

    # Get count of keys with zero balance
    cursor.execute("SELECT COUNT(*) FROM api_keys WHERE balance <= 0")
    zero_balance_count = cursor.fetchone()[0]

    # Get total key count
    total_key_count = positive_count + zero_balance_count

    return JSONResponse(
        {
            "total_key_count": total_key_count,
            "positive_balance_count": positive_count,
            "zero_balance_count": zero_balance_count,
            "total_balance": total_balance,
        }
    )


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
