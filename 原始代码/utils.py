import re
import random
import config
import aiohttp
import logging
from db import conn, cursor


async def validate_key_async(api_key: str):
    """异步验证API密钥的有效性并获取余额"""
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.siliconflow.cn/v1/user/info", headers=headers, timeout=10
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    return True, data.get("data", {}).get("totalBalance", 0)
                else:
                    data = await r.json()
                    return False, data.get("message", "验证失败")
    except Exception as e:
        return False, f"请求失败: {str(e)}"


def validate_key_format(key: str) -> bool:
    """验证密钥格式是否正确（以'sk-'开头，后跟字母数字字符）"""
    return bool(re.match(r"^sk-[a-zA-Z0-9]+$", key))


def clean_key(key: str) -> str:
    """清理密钥，移除尾部括号或其他内容"""
    # 匹配密钥模式并返回仅该部分
    match = re.search(r"(sk-[a-zA-Z0-9]+)", key)
    if match:
        return match.group(1)
    return key.strip()


def select_api_key(keys_with_balance, use_zero_balance=False):
    """根据配置策略选择一个API密钥
    
    Args:
        keys_with_balance: API密钥及余额的列表
        use_zero_balance: 是否优先使用余额为0的密钥
    
    Returns:
        选择的API密钥
    """
    # keys_with_balance: list of (key, balance)
    if not keys_with_balance:
        return None

    # 只选择启用的key
    cursor.execute(
        "SELECT key, balance FROM api_keys WHERE key IN ({}) AND enabled = 1".format(
            ",".join("?" for _ in range(len(keys_with_balance)))
        ),
        [k[0] for k in keys_with_balance],
    )
    enabled_keys = cursor.fetchall()

    if not enabled_keys:
        return None
    
    # 如果指定使用余额为0的key，则筛选出余额为0的key
    if use_zero_balance:
        zero_balance_keys = [k for k in enabled_keys if float(k[1]) <= 0]
        if zero_balance_keys:
            # 使用余额为0的key时，固定使用随机策略
            return random.choice(zero_balance_keys)[0]
        # 如果没有余额为0的key，则返回None，表示无法处理此请求
        return None
    
    # 如果不使用余额为0的key，则筛选出余额大于0的key
    positive_balance_keys = [k for k in enabled_keys if float(k[1]) > 0]
    if not positive_balance_keys:
        return None
    
    # 使用正常的选择策略
    enabled_keys = positive_balance_keys

    # 基于余额的策略
    if config.CALL_STRATEGY == "high":
        return max(enabled_keys, key=lambda x: x[1])[0]
    elif config.CALL_STRATEGY == "low":
        return min(enabled_keys, key=lambda x: x[1])[0]

    # 基于使用次数的策略
    elif config.CALL_STRATEGY == "least_used":
        cursor.execute(
            "SELECT key, usage_count FROM api_keys WHERE key IN ({}) AND enabled = 1".format(
                ",".join("?" for _ in range(len(enabled_keys)))
            ),
            [k[0] for k in enabled_keys],
        )
        usage_data = cursor.fetchall()
        return min(usage_data, key=lambda x: x[1])[0]
    elif config.CALL_STRATEGY == "most_used":
        cursor.execute(
            "SELECT key, usage_count FROM api_keys WHERE key IN ({}) AND enabled = 1".format(
                ",".join("?" for _ in range(len(enabled_keys)))
            ),
            [k[0] for k in enabled_keys],
        )
        usage_data = cursor.fetchall()
        return max(usage_data, key=lambda x: x[1])[0]

    # 基于添加时间的策略
    elif config.CALL_STRATEGY == "oldest":
        cursor.execute(
            "SELECT key, add_time FROM api_keys WHERE key IN ({}) AND enabled = 1".format(
                ",".join("?" for _ in range(len(enabled_keys)))
            ),
            [k[0] for k in enabled_keys],
        )
        time_data = cursor.fetchall()
        return min(time_data, key=lambda x: x[1])[0]
    elif config.CALL_STRATEGY == "newest":
        cursor.execute(
            "SELECT key, add_time FROM api_keys WHERE key IN ({}) AND enabled = 1".format(
                ",".join("?" for _ in range(len(enabled_keys)))
            ),
            [k[0] for k in enabled_keys],
        )
        time_data = cursor.fetchall()
        return max(time_data, key=lambda x: x[1])[0]

    # 默认随机策略
    else:
        return random.choice(enabled_keys)[0]


async def check_and_remove_key(key: str):
    """检查密钥的有效性并更新余额，但不删除余额为0的有效key"""
    valid, balance = await validate_key_async(key)
    logger = logging.getLogger(__name__)
    if valid:
        logger.info(f"Key validation successful: {key[:8]}*** - Balance: {balance}")
        # 更新余额
        cursor.execute("UPDATE api_keys SET balance = ? WHERE key = ?", (balance, key))
        conn.commit()
    else:
        logger.warning(f"Invalid key detected: {key[:8]}*** - Removing from pool")
        cursor.execute("DELETE FROM api_keys WHERE key = ?", (key,))
        conn.commit()
