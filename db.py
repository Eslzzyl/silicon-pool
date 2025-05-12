import sqlite3
import time

# 全局数据库连接
conn = sqlite3.connect("pool.db", check_same_thread=False)
cursor = conn.cursor()


def init_db():
    """初始化数据库表结构"""
    # 启用外键约束
    cursor.execute("PRAGMA foreign_keys = ON")
    
    # 启用WAL模式提高并发性能
    cursor.execute("PRAGMA journal_mode = WAL")
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS api_keys (
        key TEXT PRIMARY KEY,
        add_time REAL,
        balance REAL,
        usage_count INTEGER,
        enabled INTEGER DEFAULT 1,
        is_invalid INTEGER DEFAULT 0
    )
    """)
    conn.commit()
    
    # 检查是否需要添加is_invalid列
    cursor.execute("PRAGMA table_info(api_keys)")
    columns = [column[1] for column in cursor.fetchall()]
    if 'is_invalid' not in columns:
        cursor.execute("ALTER TABLE api_keys ADD COLUMN is_invalid INTEGER DEFAULT 0")
        conn.commit()

    # 创建日志表以记录API调用
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        used_key TEXT,
        model TEXT,
        call_time REAL,
        input_tokens INTEGER,
        output_tokens INTEGER,
        total_tokens INTEGER,
        endpoint TEXT
    )
    """)
    conn.commit()
    
    # 为logs表添加索引以提高查询性能
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_logs_call_time ON logs(call_time)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_logs_model ON logs(model)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_logs_endpoint ON logs(endpoint)")
    conn.commit()

    # 创建会话表以存储用户会话
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        token TEXT PRIMARY KEY,
        expiry_time REAL,
        created_at REAL
    )
    """)
    conn.commit()
    
    # 为sessions表添加索引
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expiry_time)")
    conn.commit()


def insert_api_key(api_key: str, balance: float):
    """向数据库中插入新的API密钥"""
    cursor.execute(
        "INSERT OR IGNORE INTO api_keys (key, add_time, balance, usage_count, enabled) VALUES (?, ?, ?, ?, 1)",
        (api_key, time.time(), balance, 0),
    )
    conn.commit()


def log_completion(
    used_key: str,
    model: str,
    call_time: float,
    input_tokens: int,
    output_tokens: int,
    total_tokens: int,
    endpoint: str,
):
    """记录API调用日志"""
    cursor.execute(
        "INSERT INTO logs (used_key, model, call_time, input_tokens, output_tokens, total_tokens, endpoint) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            used_key,
            model,
            call_time,
            input_tokens,
            output_tokens,
            total_tokens,
            endpoint,
        ),
    )
    conn.commit()


def create_session(token: str, expiry_time: float):
    """创建新的会话记录"""
    cursor.execute(
        "INSERT INTO sessions (token, expiry_time, created_at) VALUES (?, ?, ?)",
        (token, expiry_time, time.time()),
    )
    conn.commit()


def get_session(token: str):
    """获取会话信息"""
    cursor.execute("SELECT expiry_time FROM sessions WHERE token = ?", (token,))
    result = cursor.fetchone()
    return result[0] if result else None


def update_session_expiry(token: str, new_expiry_time: float):
    """更新会话过期时间"""
    cursor.execute(
        "UPDATE sessions SET expiry_time = ? WHERE token = ?", (new_expiry_time, token)
    )
    conn.commit()


def delete_session(token: str):
    """删除会话"""
    cursor.execute("DELETE FROM sessions WHERE token = ?", (token,))
    conn.commit()


def cleanup_expired_sessions():
    """清理所有过期会话"""
    current_time = time.time()
    cursor.execute("DELETE FROM sessions WHERE expiry_time < ?", (current_time,))
    conn.commit()
