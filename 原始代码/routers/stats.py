from fastapi import APIRouter
from fastapi.responses import JSONResponse
from db import cursor
import time
from datetime import datetime, timedelta

router = APIRouter()


@router.get("/api/stats/daily")
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

    # 查询调用次数
    cursor.execute(
        """
        SELECT strftime('%H', datetime(call_time, 'unixepoch', 'localtime')) as hour,
               COUNT(*) as call_count
        FROM logs
        WHERE call_time >= ? AND call_time < ?
        GROUP BY hour
        """,
        (start_timestamp, end_timestamp),
    )

    for row in cursor.fetchall():
        hour = int(row[0])
        calls_by_hour[hour] = row[1]

    # 查询token消耗
    cursor.execute(
        """
        SELECT strftime('%H', datetime(call_time, 'unixepoch', 'localtime')) as hour,
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
        input_tokens_by_hour[hour] = row[1]
        output_tokens_by_hour[hour] = row[2]

    # 查询模型使用情况
    cursor.execute(
        """
        SELECT model, SUM(total_tokens) as tokens
        FROM logs
        WHERE call_time >= ? AND call_time < ?
        GROUP BY model
        ORDER BY tokens DESC
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

    # 查询调用次数
    cursor.execute(
        """
        SELECT strftime('%d', datetime(call_time, 'unixepoch', 'localtime')) as day,
               COUNT(*) as call_count
        FROM logs
        WHERE call_time >= ? AND call_time < ?
        GROUP BY day
        """,
        (start_timestamp, end_timestamp),
    )

    for row in cursor.fetchall():
        day = int(row[0])
        calls_by_day[day] = row[1]

    # 查询token消耗
    cursor.execute(
        """
        SELECT strftime('%d', datetime(call_time, 'unixepoch', 'localtime')) as day,
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
        input_tokens_by_day[day] = row[1]
        output_tokens_by_day[day] = row[2]

    # 查询模型使用情况
    cursor.execute(
        """
        SELECT model, SUM(total_tokens) as tokens
        FROM logs
        WHERE call_time >= ? AND call_time < ?
        GROUP BY model
        ORDER BY tokens DESC
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
