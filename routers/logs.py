from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from db import conn, cursor
from datetime import datetime
import time

router = APIRouter()


from datetime import datetime
import time

# 缓存模型和接口列表，避免频繁查询
model_cache = {"data": [], "timestamp": 0, "ttl": 300}  # 5分钟缓存
endpoint_cache = {"data": [], "timestamp": 0, "ttl": 300}  # 5分钟缓存

@router.get("/logs")
async def get_logs(
    page: int = 1, date_filter: str = "all", model: str = "all", endpoint: str = "all"
):
    page_size = 10
    offset = (page - 1) * page_size

    # 构建查询条件
    query_conditions = []
    query_params = []

    # 日期过滤
    if date_filter == "today":
        # 获取今天的开始时间戳
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        start_timestamp = time.mktime(today.timetuple())
        query_conditions.append("call_time >= ?")
        query_params.append(start_timestamp)

    # 模型过滤
    if model != "all":
        query_conditions.append("model = ?")
        query_params.append(model)

    # 接口过滤
    if endpoint != "all":
        query_conditions.append("endpoint = ?")
        query_params.append(endpoint)

    # 组装WHERE子句
    where_clause = " AND ".join(query_conditions) if query_conditions else "1=1"

    # 获取总记录数 - 使用近似计数提高性能
    count_query = f"SELECT COUNT(*) FROM logs WHERE {where_clause}"
    cursor.execute(count_query, query_params)
    total = cursor.fetchone()[0]

    # 获取过滤后的日志 - 限制返回字段，只获取需要的数据
    logs_query = f"""
        SELECT used_key, model, call_time, input_tokens, output_tokens, total_tokens, endpoint 
        FROM logs 
        WHERE {where_clause} 
        ORDER BY call_time DESC 
        LIMIT ? OFFSET ?
    """
    cursor.execute(logs_query, query_params + [page_size, offset])
    logs = cursor.fetchall()

    # 将日志格式化为字典列表
    log_list = [
        {
            "used_key": row[0],
            "model": row[1],
            "call_time": row[2],
            "input_tokens": row[3] or 0,  # 防止NULL值
            "output_tokens": row[4] or 0,  # 防止NULL值
            "total_tokens": row[5] or 0,  # 防止NULL值
            "endpoint": row[6] or "未知",  # 为了向后兼容，对空值使用默认值
        }
        for row in logs
    ]

    # 使用缓存获取模型列表
    current_time = time.time()
    if current_time - model_cache["timestamp"] > model_cache["ttl"] or not model_cache["data"]:
        cursor.execute("SELECT DISTINCT model FROM logs ORDER BY model")
        model_cache["data"] = [row[0] for row in cursor.fetchall()]
        model_cache["timestamp"] = current_time
    available_models = model_cache["data"]

    # 使用缓存获取接口列表
    if current_time - endpoint_cache["timestamp"] > endpoint_cache["ttl"] or not endpoint_cache["data"]:
        cursor.execute(
            "SELECT DISTINCT endpoint FROM logs WHERE endpoint IS NOT NULL ORDER BY endpoint"
        )
        endpoint_cache["data"] = [row[0] for row in cursor.fetchall()]
        endpoint_cache["timestamp"] = current_time
    available_endpoints = endpoint_cache["data"]

    return JSONResponse(
        {
            "logs": log_list,
            "total": total,
            "page": page,
            "page_size": page_size,
            "available_models": available_models,
            "available_endpoints": available_endpoints,
        }
    )


@router.post("/clear_logs")
async def clear_logs():
    try:
        # 使用事务处理批量删除
        conn.execute("BEGIN TRANSACTION")
        cursor.execute("DELETE FROM logs")
        # 重置自增ID
        cursor.execute("DELETE FROM sqlite_sequence WHERE name='logs'")
        conn.commit()
        
        # 执行VACUUM操作释放空间
        cursor.execute("VACUUM")
        
        # 清空缓存
        model_cache["data"] = []
        model_cache["timestamp"] = 0
        endpoint_cache["data"] = []
        endpoint_cache["timestamp"] = 0
        
        return JSONResponse({"message": "日志已清空"})
    except Exception as e:
        # 发生错误时回滚事务
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"清空日志失败: {str(e)}")
