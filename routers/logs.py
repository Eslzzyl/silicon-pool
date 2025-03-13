from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from db import cursor, clear_logs
from datetime import datetime
import time

router = APIRouter()


@router.get("/logs")
async def get_logs(page: int = 1, date_filter: str = "all", model: str = "all", endpoint: str = "all"):
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
        query_conditions.append("api_endpoint = ?")
        query_params.append(endpoint)

    # 组装WHERE子句
    where_clause = " AND ".join(query_conditions) if query_conditions else "1=1"

    # 获取总记录数
    count_query = f"SELECT COUNT(*) FROM logs WHERE {where_clause}"
    cursor.execute(count_query, query_params)
    total = cursor.fetchone()[0]

    # 获取过滤后的日志
    logs_query = f"""
        SELECT used_key, model, api_endpoint, call_time, input_tokens, output_tokens, total_tokens 
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
            "api_endpoint": row[2],
            "call_time": row[3],
            "input_tokens": row[4],
            "output_tokens": row[5],
            "total_tokens": row[6],
        }
        for row in logs
    ]

    # 获取所有可用的模型列表，用于前端过滤下拉框
    cursor.execute("SELECT DISTINCT model FROM logs ORDER BY model")
    available_models = [row[0] for row in cursor.fetchall()]
    
    # 获取所有可用的接口类型
    cursor.execute("SELECT DISTINCT api_endpoint FROM logs ORDER BY api_endpoint")
    available_endpoints = [row[0] for row in cursor.fetchall()]

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
async def clear_logs_route():
    try:
        # 使用db中定义的清空日志函数
        clear_logs()
        return JSONResponse({"message": "日志已清空"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"清空日志失败: {str(e)}")
