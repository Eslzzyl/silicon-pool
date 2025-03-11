import sqlite3
import time

# 全局数据库连接
conn = sqlite3.connect("pool.db", check_same_thread=False)
cursor = conn.cursor()


def init_db():
    """初始化数据库表结构"""
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS api_keys (
        key TEXT PRIMARY KEY,
        add_time REAL,
        balance REAL,
        usage_count INTEGER,
        enabled INTEGER DEFAULT 1
    )
    """)
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
        total_tokens INTEGER
    )
    """)
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
):
    """记录API调用日志"""
    cursor.execute(
        "INSERT INTO logs (used_key, model, call_time, input_tokens, output_tokens, total_tokens) VALUES (?, ?, ?, ?, ?, ?)",
        (used_key, model, call_time, input_tokens, output_tokens, total_tokens),
    )
    conn.commit()
