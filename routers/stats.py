from fastapi import APIRouter
from fastapi.responses import JSONResponse
from db import cursor, conn
import time
from datetime import datetime, timedelta
import functools
import asyncio

router = APIRouter()

# 缓存装饰器
def cache_with_timeout(timeout_seconds=60):
    """带有超时的缓存装饰器"""
    cache = {}
    
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # 生成缓存键
            key = f"{func.__name__}:{str(args)}:{str(kwargs)}"
            
            # 检查缓存是否存在且未过期
            if key in cache:
                result, timestamp = cache[key]
                if time.time() - timestamp < timeout_seconds:
                    return result
            
            # 执行原始函数
            result = await func(*args, **kwargs)
            
            # 更新缓存
            cache[key] = (result, time.time())
            
            return result
        return wrapper
    return decorator


@router.get("/api/stats/daily")
@cache_with_timeout(timeout_seconds=60)  # 缓存60秒
async def get_daily_stats():
    """获取当天按小时统计的API调用数据"""
    # 获取今天的开始时间戳
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start_timestamp = time.mktime(today.timetuple())
    end_timestamp = time.mktime((today + timedelta(days=1)).timetuple())

    # 准备24小时的数据结构
    hours = list(range(24))
    calls_by_hour = {hour: 0 for hour in hours}
    input_tokens_by_hour = {hour: 0 for hour in hours}
    output_tokens_by_hour = {hour: 0 for hour in hours}

    # 使用单个查询获取所有数据，减少数据库交互次数
    cursor.execute(
        """
        SELECT 
            strftime('%H', datetime(call_time, 'unixepoch', 'localtime')) as hour,
            COUNT(*) as call_count,
            SUM(input_tokens) as total_input,
            SUM(output_tokens) as total_output
        FROM logs
        WHERE call_time >= ? AND call_time < ?
        GROUP BY hour
        """,
        (start_timestamp, end_timestamp),
    )

    for row in cursor.fetchall():
        hour = int(row[0])
        calls_by_hour[hour] = row[1]
        input_tokens_by_hour[hour] = row[2] or 0  # 防止NULL值
        output_tokens_by_hour[hour] = row[3] or 0  # 防止NULL值

    # 查询模型使用情况
    cursor.execute(
        """
        SELECT model, SUM(total_tokens) as tokens
        FROM logs
        WHERE call_time >= ? AND call_time < ?
        GROUP BY model
        ORDER BY tokens DESC
        LIMIT 10  -- 限制返回的模型数量，提高性能
        """,
        (start_timestamp, end_timestamp),
    )

    models = []
    model_tokens = []

    for row in cursor.fetchall():
        models.append(row[0])
        model_tokens.append(row[1])

    return JSONResponse(
        {
            "labels": hours,
            "calls": list(calls_by_hour.values()),
            "input_tokens": list(input_tokens_by_hour.values()),
            "output_tokens": list(output_tokens_by_hour.values()),
            "model_labels": models,
            "model_tokens": model_tokens,
        }
    )


@router.get("/api/stats/monthly")
@cache_with_timeout(timeout_seconds=300)  # 缓存5分钟，月度数据变化较慢
async def get_monthly_stats():
    """获取当月按天统计的API调用数据"""
    # 获取当月第一天
    today = datetime.now()
    first_day = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # 计算下个月第一天
    if first_day.month == 12:
        next_month = first_day.replace(year=first_day.year + 1, month=1)
    else:
        next_month = first_day.replace(month=first_day.month + 1)

    start_timestamp = time.mktime(first_day.timetuple())
    end_timestamp = time.mktime(next_month.timetuple())

    # 计算当月天数
    days_in_month = (next_month - first_day).days
    days = list(range(1, days_in_month + 1))

    calls_by_day = {day: 0 for day in days}
    input_tokens_by_day = {day: 0 for day in days}
    output_tokens_by_day = {day: 0 for day in days}

    # 使用单个查询获取所有数据，减少数据库交互次数
    cursor.execute(
        """
        SELECT 
            strftime('%d', datetime(call_time, 'unixepoch', 'localtime')) as day,
            COUNT(*) as call_count,
            SUM(input_tokens) as total_input,
            SUM(output_tokens) as total_output
        FROM logs
        WHERE call_time >= ? AND call_time < ?
        GROUP BY day
        """,
        (start_timestamp, end_timestamp),
    )

    for row in cursor.fetchall():
        day = int(row[0])
        calls_by_day[day] = row[1]
        input_tokens_by_day[day] = row[2] or 0  # 防止NULL值
        output_tokens_by_day[day] = row[3] or 0  # 防止NULL值

    # 查询模型使用情况
    cursor.execute(
        """
        SELECT model, SUM(total_tokens) as tokens
        FROM logs
        WHERE call_time >= ? AND call_time < ?
        GROUP BY model
        ORDER BY tokens DESC
        LIMIT 10  -- 限制返回的模型数量，提高性能
        """,
        (start_timestamp, end_timestamp),
    )

    models = []
    model_tokens = []

    for row in cursor.fetchall():
        models.append(row[0])
        model_tokens.append(row[1])

    return JSONResponse(
        {
            "labels": days,
            "calls": list(calls_by_day.values()),
            "input_tokens": list(input_tokens_by_day.values()),
            "output_tokens": list(output_tokens_by_day.values()),
            "model_labels": models,
            "model_tokens": model_tokens,
        }
    )
