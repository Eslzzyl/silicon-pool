#!/bin/bash

# 设置环境变量
export PYTHONUNBUFFERED=1
export TZ=Asia/Shanghai

# 创建日志目录
mkdir -p /app/logs

# 设置日志文件
LOG_FILE="/app/logs/container.log"
TEST_KEY_LOG="/app/logs/test_key.log"
API_LOG="/app/logs/api.log"

# 添加日期时间到日志函数
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOG_FILE"
}

# 添加信号处理，确保容器不会意外关闭
trap "log '接收到终止信号，但容器将继续运行'; wait" SIGTERM SIGINT

log "容器启动，开始初始化服务..."

# 启动测试脚本作为后台进程，重定向日志
log "启动测试密钥验证脚本作为守护进程..."
python test_key.py > "$TEST_KEY_LOG" 2>&1 &
TEST_KEY_PID=$!
log "测试密钥验证脚本已启动，PID: $TEST_KEY_PID"

# 启动FastAPI应用，重定向日志
log "正在启动Silicon Pool API服务..."
uvicorn main:app --host 0.0.0.0 --port 7898 --reload > "$API_LOG" 2>&1 &

# 记录FastAPI应用的PID
FASTAPI_PID=$!
log "FastAPI应用已启动，PID: $FASTAPI_PID"

# 检查应用是否正常启动
sleep 5
if ! curl -s http://localhost:7898/health > /dev/null; then
    log "警告: FastAPI应用可能未正常启动，检查日志获取详细信息"
    log "最近的API日志:"
    tail -n 20 "$API_LOG" | tee -a "$LOG_FILE"
else
    log "FastAPI应用健康检查通过"
fi

# 无限循环保持容器运行，并监控所有应用状态
while true; do
    log "容器保持运行中..."
    
    # 检查FastAPI应用是否仍在运行
    if [ -n "$FASTAPI_PID" ] && ! ps -p $FASTAPI_PID > /dev/null; then
        log "检测到FastAPI应用已停止运行，记录最后日志"
        if [ -f "$API_LOG" ]; then
            log "FastAPI应用最后日志:"
            tail -n 50 "$API_LOG" | tee -a "$LOG_FILE"
        fi
        
        log "尝试重新启动FastAPI应用..."
        uvicorn main:app --host 0.0.0.0 --port 7898 --reload > "$API_LOG" 2>&1 &
        FASTAPI_PID=$!
        log "FastAPI应用已重新启动，新PID: $FASTAPI_PID"
        
        # 等待应用启动并检查健康状态
        sleep 5
        if curl -s http://localhost:7898/health > /dev/null; then
            log "FastAPI应用重启成功，健康检查通过"
        else
            log "警告: FastAPI应用重启后健康检查失败，但容器将继续运行"
            log "检查最新日志:"
            tail -n 20 "$API_LOG" | tee -a "$LOG_FILE"
        fi
    fi
    
    # 检查测试密钥脚本是否仍在运行
    if [ -n "$TEST_KEY_PID" ] && ! ps -p $TEST_KEY_PID > /dev/null; then
        log "检测到测试密钥验证脚本已停止运行，记录最后日志"
        if [ -f "$TEST_KEY_LOG" ]; then
            log "测试密钥脚本最后日志:"
            tail -n 50 "$TEST_KEY_LOG" | tee -a "$LOG_FILE"
        fi
        
        log "尝试重新启动测试密钥验证脚本..."
        python test_key.py > "$TEST_KEY_LOG" 2>&1 &
        TEST_KEY_PID=$!
        log "测试密钥验证脚本已重新启动，新PID: $TEST_KEY_PID"
    fi
    
    # 每2分钟检查一次，更频繁地监控服务状态
    sleep 120
done

# 此行永远不会执行
log "脚本执行完毕"