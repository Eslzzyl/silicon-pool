from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
import config
import secrets
import time
import db  # 导入数据库模块

router = APIRouter()

# 会话过期时间
SESSION_EXPIRY = 60 * 60 * 48


@router.post("/api/login")
async def login(request: Request):
    data = await request.json()
    username = data.get("username", "")
    password = data.get("password", "")

    if username == config.ADMIN_USERNAME and password == config.ADMIN_PASSWORD:
        # 清理过期会话
        db.cleanup_expired_sessions()

        # 生成会话令牌
        session_token = secrets.token_urlsafe(32)
        expiry_time = time.time() + SESSION_EXPIRY

        # 将会话存储到数据库
        db.create_session(session_token, expiry_time)

        # 设置响应和Cookie
        response = JSONResponse({"status": "success", "message": "登录成功"})
        response.set_cookie(
            key="session_token",
            value=session_token,
            httponly=True,
            max_age=SESSION_EXPIRY,
            samesite="lax",
        )
        return response
    else:
        raise HTTPException(status_code=401, detail="用户名或密码错误")


@router.post("/api/logout")
async def logout(request: Request):
    session_token = request.cookies.get("session_token")

    if session_token:
        # 从数据库中删除会话
        db.delete_session(session_token)

    response = JSONResponse({"status": "success", "message": "已退出登录"})
    response.delete_cookie(key="session_token")
    return response


@router.get("/api/check_auth")
async def check_auth(request: Request):
    session_token = request.cookies.get("session_token")

    if not session_token:
        return JSONResponse({"authenticated": False})

    # 从数据库查询会话
    expiry_time = db.get_session(session_token)

    if not expiry_time:
        return JSONResponse({"authenticated": False})

    current_time = time.time()

    # 检查会话是否过期
    if expiry_time < current_time:
        # 删除过期会话
        db.delete_session(session_token)
        return JSONResponse({"authenticated": False})

    # 更新会话过期时间
    new_expiry_time = current_time + SESSION_EXPIRY
    db.update_session_expiry(session_token, new_expiry_time)

    return JSONResponse({"authenticated": True})


@router.post("/api/update_credentials")
async def update_credentials(request: Request):
    # 先验证当前会话
    if not validate_session(request):
        raise HTTPException(status_code=401, detail="未认证")

    data = await request.json()
    new_username = data.get("username")
    new_password = data.get("password")

    if not new_password:
        raise HTTPException(status_code=400, detail="密码不能为空")

    # 如果用户名为空，则默认使用当前的用户名
    if not new_username:
        new_username = config.ADMIN_USERNAME

    config.update_admin_credentials(new_username, new_password)
    return JSONResponse({"status": "success", "message": "管理员凭据已更新"})


def validate_session(request: Request):
    """验证会话有效性的辅助函数"""
    session_token = request.cookies.get("session_token")

    if not session_token:
        return False

    # 从数据库查询会话
    expiry_time = db.get_session(session_token)

    if not expiry_time:
        return False

    current_time = time.time()

    # 检查是否过期
    if expiry_time < current_time:
        # 删除过期会话
        db.delete_session(session_token)
        return False

    # 更新会话过期时间
    new_expiry_time = current_time + SESSION_EXPIRY
    db.update_session_expiry(session_token, new_expiry_time)

    return True
