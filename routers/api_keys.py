from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse, Response
import asyncio
import logging
from db import conn, cursor
from utils import validate_key_async, validate_key_format, clean_key

logger = logging.getLogger(__name__)

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
    allowed_filters = ["positive", "zero", "invalid", "all"]

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
        filter_clause = "WHERE balance > 0 AND is_invalid = 0"
    elif balance_filter == "zero":
        filter_clause = "WHERE balance <= 0 AND is_invalid = 0"
    elif balance_filter == "invalid":
        filter_clause = "WHERE is_invalid = 1"

    # 计算总数
    count_sql = f"SELECT COUNT(*) FROM api_keys {filter_clause}"
    cursor.execute(count_sql)
    total = cursor.fetchone()[0]

    # 获取分页数据
    cursor.execute(
        f"SELECT key, add_time, balance, usage_count, enabled, is_invalid FROM api_keys {filter_clause} ORDER BY {sort_field} {sort_order} LIMIT ? OFFSET ?",
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
            "is_invalid": bool(row[5]),
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

    # 增加重试机制
    max_retries = 3
    retry_delay = 2  # 秒
    last_error = None
    
    for retry in range(max_retries):
        try:
            valid, balance = await validate_key_async(key)

            if valid:
                # 检查余额是否为数字
                if isinstance(balance, (int, float)) or (isinstance(balance, str) and balance.replace('.', '', 1).isdigit()):
                    # 有效密钥，更新余额并重置无效标记
                    balance_float = float(balance) if isinstance(balance, str) else balance
                    try:
                        cursor.execute(
                            "UPDATE api_keys SET balance = ?, is_invalid = 0, enabled = 1 WHERE key = ?", (balance_float, key)
                        )
                        conn.commit()
                        if balance_float > 0:
                            return JSONResponse({"message": f"密钥更新成功，当前余额: ¥{balance_float}"})
                        else:
                            return JSONResponse({"message": f"密钥更新成功，余额为0，已归类为zero类别"})
                    except Exception as db_error:
                        logger.error(f"数据库更新失败: {str(db_error)}")
                        raise
                else:
                    # 余额不是数字但验证成功，可能是余额用尽但可用的密钥
                    try:
                        cursor.execute(
                            "UPDATE api_keys SET balance = 0, is_invalid = 0, enabled = 1 WHERE key = ?", (key,)
                        )
                        conn.commit()
                        return JSONResponse({"message": "密钥更新成功，余额为0或非数字格式"})
                    except Exception as db_error:
                        logger.error(f"数据库更新失败: {str(db_error)}")
                        raise
            else:
                # 无效密钥，标记为无效
                try:
                    cursor.execute(
                        "UPDATE api_keys SET is_invalid = 1, enabled = 0 WHERE key = ?", (key,)
                    )
                    conn.commit()
                    return JSONResponse({"message": "密钥已失效，已标记为无效"})
                except Exception as db_error:
                    logger.error(f"数据库更新失败: {str(db_error)}")
                    raise
        except Exception as e:
            last_error = e
            logger.warning(f"刷新密钥失败(第{retry+1}次尝试): {str(e)}")
            if retry < max_retries - 1:
                await asyncio.sleep(retry_delay)
                retry_delay *= 2  # 指数退避
    
    # 所有重试都失败
    logger.error(f"刷新密钥失败，已重试{max_retries}次: {str(last_error)}")
    raise HTTPException(status_code=500, detail=f"刷新密钥失败: {str(last_error)}")




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
    """刷新所有密钥的状态和余额
    
    处理规则：
    1. 有效密钥且余额 > 0: 启用 (有余额的key)
    2. 有效密钥但余额 ≤ 0: 启用 (余额用尽的key)
    3. 无效密钥(无法连通): 禁用
    4. 网络错误: 保持原状态
    """
    # 创建新的游标避免递归使用
    local_cursor = conn.cursor()

    try:
        # 获取所有密钥
        local_cursor.execute("SELECT key, balance, is_invalid, enabled FROM api_keys")
        all_keys_data = local_cursor.fetchall()
        all_keys = [row[0] for row in all_keys_data]
        key_balance_map = {row[0]: row[1] for row in all_keys_data}
        key_invalid_map = {row[0]: row[2] for row in all_keys_data}
        key_enabled_map = {row[0]: row[3] for row in all_keys_data}

        # 获取初始总余额（只计算余额大于0的）
        initial_balance = sum(balance for balance in key_balance_map.values() if float(balance) > 0)

        # 创建并行验证任务
        tasks = [validate_key_async(key) for key in all_keys]
        results = await asyncio.gather(*tasks)

        updated = 0
        invalid_keys = 0
        zero_balance = 0
        network_errors = 0
        for key, result in zip(all_keys, results):
            # 解包结果，新的validate_key_async返回三个值
            if len(result) == 3:
                valid, balance, is_network_error = result
            else:
                # 兼容旧版本的validate_key_async（只返回两个值）
                valid, balance = result
                # 根据错误消息判断是否为网络错误
                error_message = str(balance).lower() if isinstance(balance, str) else ""
                is_network_error = any(err in error_message for err in ["timeout", "连接", "connection", "请求失败", "request failed", "network", "网络", "超时", "限流", "429"])
            
            if valid:
                # 密钥验证成功 - 无论余额多少都应该启用
                try:
                    # 尝试将balance转换为浮点数
                    if isinstance(balance, (int, float)):
                        balance_float = float(balance)
                    elif isinstance(balance, str) and balance.replace('.', '', 1).isdigit():
                        balance_float = float(balance)
                    else:
                        # 无法转换为浮点数，设为0但可用
                        balance_float = 0
                    
                    # 更新密钥状态：有效且启用
                    local_cursor.execute(
                        "UPDATE api_keys SET balance = ?, is_invalid = 0, enabled = 1 WHERE key = ?", 
                        (balance_float, key)
                    )
                    updated += 1
                    
                    # 记录余额为0的密钥数量
                    if balance_float <= 0:
                        zero_balance += 1
                        logger.info(f"密钥验证成功但余额为0或用尽: {key[:8]}*** - 已设为可用")
                    else:
                        logger.info(f"密钥验证成功且有余额: {key[:8]}*** - 余额: {balance_float}")
                        
                except Exception as e:
                    logger.error(f"处理密钥余额时出错: {key[:8]}*** - {str(e)}")
                    # 出错时仍然将密钥设为可用，但余额为0
                    local_cursor.execute(
                        "UPDATE api_keys SET balance = 0, is_invalid = 0, enabled = 1 WHERE key = ?", 
                        (key,)
                    )
                    updated += 1
                    zero_balance += 1
            else:
                # 验证失败，分为两种情况：网络错误或密钥确实无效
                if is_network_error:
                    # 网络错误，保持密钥状态不变
                    network_errors += 1
                    logger.warning(f"密钥验证网络错误: {key[:8]}*** - {balance} (保持原状态)")
                    continue
                
                # 获取当前密钥的状态
                current_balance = key_balance_map.get(key, 0)
                current_invalid = key_invalid_map.get(key, 0)
                current_enabled = key_enabled_map.get(key, 0)
                
                # 如果当前余额大于0，则保持密钥状态不变，避免错误地禁用有余额的密钥
                if float(current_balance) > 0:
                    network_errors += 1
                    logger.warning(f"密钥验证失败但有正余额 (¥{current_balance}): {key[:8]}*** - 保持原状态")
                    # 确保密钥保持启用状态
                    local_cursor.execute(
                        "UPDATE api_keys SET is_invalid = 0, enabled = 1 WHERE key = ?", (key,)
                    )
                    continue
                
                # 确认是无效密钥，标记为无效并禁用
                try:
                    # 检查密钥格式是否正确
                    if not validate_key_format(key):
                        logger.warning(f"密钥格式无效: {key[:8]}***")
                        local_cursor.execute(
                            "UPDATE api_keys SET is_invalid = 1, enabled = 0 WHERE key = ?", (key,)
                        )
                        invalid_keys += 1
                        continue
                    
                    logger.info(f"标记无效密钥: {key[:8]}***")
                    local_cursor.execute(
                        "UPDATE api_keys SET is_invalid = 1, enabled = 0 WHERE key = ?", (key,)
                    )
                    invalid_keys += 1
                except Exception as e:
                    logger.error(f"处理无效密钥时出错: {key[:8]}*** - {str(e)}")
                    network_errors += 1

        conn.commit()

        # 计算新的总余额（只计算余额大于0的）
        local_cursor.execute(
            "SELECT COALESCE(SUM(balance), 0) FROM api_keys WHERE balance > 0"
        )
        new_balance = local_cursor.fetchone()[0]
        balance_change = new_balance - initial_balance

        # 获取有效密钥数量（包括余额为0但可用的密钥）
        local_cursor.execute(
            "SELECT COUNT(*) FROM api_keys WHERE is_invalid = 0"
        )
        valid_keys_count = local_cursor.fetchone()[0]
        
        # 获取有余额的密钥数量
        local_cursor.execute(
            "SELECT COUNT(*) FROM api_keys WHERE balance > 0 AND is_invalid = 0"
        )
        positive_balance_count = local_cursor.fetchone()[0]

        message = f"刷新完成，更新 {updated} 个 Key（其中 {zero_balance} 个余额为0或用尽），发现 {invalid_keys} 个无效的 Key 并已自动禁用，{network_errors} 个 Key 因网络错误未能验证（已保持原状态）"
        message += f"\n当前共有 {valid_keys_count} 个有效密钥，其中 {positive_balance_count} 个有余额"
        if balance_change > 0:
            message += f"，总余额增加了 ¥{round(balance_change, 2)}"
        else:
            balance_decrease = abs(balance_change)
            message += f"，总余额减少了 ¥{round(balance_decrease, 2)}"

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
        filter_sql = "WHERE balance > 0 AND is_invalid = 0"
    elif filter == "zero":
        filter_sql = "WHERE balance <= 0 AND is_invalid = 0"
    elif filter == "invalid":
        filter_sql = "WHERE is_invalid = 1"

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
        "SELECT COUNT(*), COALESCE(SUM(balance), 0) FROM api_keys WHERE balance > 0 AND is_invalid = 0"
    )
    positive_count, total_balance = cursor.fetchone()

    # Get count of keys with zero balance
    cursor.execute("SELECT COUNT(*) FROM api_keys WHERE balance <= 0 AND is_invalid = 0")
    zero_balance_count = cursor.fetchone()[0]
    
    # Get count of invalid keys
    cursor.execute("SELECT COUNT(*) FROM api_keys WHERE is_invalid = 1")
    invalid_count = cursor.fetchone()[0]

    # Get total key count
    total_key_count = positive_count + zero_balance_count + invalid_count

    return JSONResponse(
        {
            "total_key_count": total_key_count,
            "positive_balance_count": positive_count,
            "zero_balance_count": zero_balance_count,
            "invalid_count": invalid_count,
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
