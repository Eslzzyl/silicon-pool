import sqlite3
import time
from cache import DatabaseCache
import atexit

# 全局数据库连接
conn = sqlite3.connect("pool.db", check_same_thread=False)
cursor = conn.cursor()

# 初始化缓存系统
db_cache = DatabaseCache(conn, flush_interval=30, max_batch_size=100)

# 在程序退出时确保缓存被正确写入
def _shutdown_cache():
    db_cache.shutdown()

atexit.register(_shutdown_cache)

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
        api_endpoint TEXT,
        call_time REAL,
        input_tokens INTEGER,
        output_tokens INTEGER,
        total_tokens INTEGER
    )
    """)
    conn.commit()


def insert_api_key(api_key: str, balance: float):
    """向数据库中插入新的API密钥"""
    # 使用缓存系统而不是直接插入
    db_cache.queue_insert(
        "api_keys", 
        (api_key, time.time(), balance, 0, 1)
    )


def log_completion(
    used_key: str,
    model: str,
    api_endpoint: str,
    call_time: float,
    input_tokens: int,
    output_tokens: int,
    total_tokens: int,
):
    """记录API调用日志"""
    # 使用缓存系统而不是直接插入
    db_cache.queue_insert(
        "logs",
        (
            None,  # 自增ID为None
            used_key,
            model,
            api_endpoint,
            call_time,
            input_tokens,
            output_tokens,
            total_tokens,
        ),
    )


# 添加更新和删除的辅助函数，便于其他模块使用缓存
def update_api_key(update_data: dict, key: str):
    """更新API密钥信息"""
    db_cache.queue_update("api_keys", update_data, "key", key)


def delete_api_key(key: str):
    """删除API密钥"""
    db_cache.queue_delete("api_keys", "key", key)


def clear_logs():
    """清空日志表"""
    # 这个操作直接执行，不通过缓存
    cursor.execute("DELETE FROM logs")
    conn.commit()
    cursor.execute("VACUUM")
    conn.commit()


# 强制刷新缓存的函数
def flush_cache():
    """手动刷新缓存到数据库"""
    db_cache.flush()


# 获取缓存统计的函数
def get_cache_stats():
    """获取缓存统计信息"""
    return db_cache.get_stats()
