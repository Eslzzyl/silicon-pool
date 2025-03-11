from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from db import conn, cursor

router = APIRouter()


@router.get("/logs")
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
    # 将日志格式化为字典列表
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


@router.post("/clear_logs")
async def clear_logs():
    try:
        cursor.execute("DELETE FROM logs")
        conn.commit()
        cursor.execute("VACUUM")
        conn.commit()
        return JSONResponse({"message": "日志已清空"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"清空日志失败: {str(e)}")
