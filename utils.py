import re
import random
import config
import aiohttp
import logging
from db import conn, cursor

# 用于轮询策略的全局计数器
round_robin_counter = 0


async def validate_key_async(api_key: str):
    """异步验证API密钥的有效性并获取余额
    
    返回值说明：
    - (valid, balance, is_network_error): 
      - valid: 密钥是否有效
      - balance: 余额（可能为0或正数）
      - is_network_error: 是否为网络错误
    """
    headers = {"Authorization": f"Bearer {api_key}"}
    max_retries = 3  # 增加最大重试次数
    retry_delay = 3  # 增加重试间隔（秒）
    timeout = 30  # 增加超时时间
    
    logger = logging.getLogger(__name__)
    logger.debug(f"开始验证密钥: {api_key[:8]}***")
    
    for attempt in range(max_retries + 1):
        try:
            # 使用自定义的超时设置
            timeout_obj = aiohttp.ClientTimeout(total=timeout)
            async with aiohttp.ClientSession(timeout=timeout_obj) as session:
                async with session.get(
                    "https://api.siliconflow.cn/v1/user/info", headers=headers
                ) as r:
                    if r.status == 200:
                        try:
                            data = await r.json()
                            # 检查响应格式是否正确
                            if "data" in data and "totalBalance" in data.get("data", {}):
                                balance = data["data"]["totalBalance"]
                                # 详细记录余额信息
                                logger.info(f"获取到原始余额: {balance}, 类型: {type(balance)}")
                                # 确保余额是数字
                                try:
                                    if isinstance(balance, (int, float)):
                                        balance_float = float(balance)
                                        if balance_float > 0:
                                            logger.debug(f"密钥验证成功: {api_key[:8]}*** - 有余额: {balance_float}")
                                        else:
                                            logger.debug(f"密钥验证成功: {api_key[:8]}*** - 余额用尽: {balance_float}")
                                        return (True, balance_float, False)  # 确保返回float类型
                                    elif isinstance(balance, str):
                                        # 尝试将字符串转换为浮点数
                                        try:
                                            balance_float = float(balance)
                                            if balance_float > 0:
                                                logger.debug(f"密钥验证成功: {api_key[:8]}*** - 有余额(转换后): {balance_float}")
                                            else:
                                                logger.debug(f"密钥验证成功: {api_key[:8]}*** - 余额用尽(转换后): {balance_float}")
                                            return True, balance_float, False
                                        except ValueError:
                                            # 如果无法转换为浮点数，但API响应成功，是余额用尽但可用的密钥
                                            logger.info(f"密钥验证成功但余额无法转换为数字: {api_key[:8]}*** - 设为余额0但可用")
                                            return True, 0, False
                                    else:
                                        # 余额不是数字，但API响应成功，是余额用尽但可用的密钥
                                        logger.info(f"密钥验证成功但余额格式异常: {api_key[:8]}*** - 设为余额0但可用")
                                        return True, 0, False
                                except Exception as e:
                                    # 处理余额转换过程中的任何异常
                                    logger.warning(f"处理余额时出现异常: {api_key[:8]}*** - {str(e)}")
                                    return True, 0, False
                            else:
                                # API响应成功但没有余额信息，是余额用尽但可用的密钥
                                logger.info(f"密钥验证成功但无余额信息: {api_key[:8]}*** - 设为余额0但可用")
                                return True, 0, False
                        except Exception as e:
                            # JSON解析错误，但API响应成功，是余额用尽但可用的密钥
                            logger.warning(f"密钥验证成功但解析响应失败: {api_key[:8]}*** - {str(e)}")
                            return True, 0, False
                    elif r.status == 401 or r.status == 403:
                        # 401/403 表示密钥确实无效，不需要重试
                        error_msg = ""
                        try:
                            data = await r.json()
                            error_msg = data.get("message", f"HTTP错误 {r.status}")
                        except:
                            error_msg = f"HTTP错误 {r.status}"
                        logger.debug(f"密钥无效: {api_key[:8]}*** - {error_msg}")
                        return (False, error_msg, False)  # 最后一个False表示不是网络错误
                    elif r.status == 429:
                        # 429表示请求过多，需要延长重试间隔
                        error_msg = "请求过多，API限流"
                        logger.warning(f"密钥验证受限: {api_key[:8]}*** - {error_msg} (尝试 {attempt+1}/{max_retries+1})")
                        if attempt < max_retries:
                            import asyncio
                            # 对于限流错误，使用更长的等待时间
                            await asyncio.sleep(retry_delay * 2)
                            continue
                        return (False, error_msg, True)  # 最后一个True表示是网络错误
                    else:
                        # 其他HTTP错误，可能是临时性的
                        error_msg = ""
                        try:
                            data = await r.json()
                            error_msg = data.get("message", f"HTTP错误 {r.status}")
                        except:
                            error_msg = f"HTTP错误 {r.status}"
                        
                        logger.warning(f"密钥验证HTTP错误: {api_key[:8]}*** - {error_msg} (尝试 {attempt+1}/{max_retries+1})")
                        # 如果还有重试机会，则继续重试
                        if attempt < max_retries:
                            import asyncio
                            await asyncio.sleep(retry_delay)
                            continue
                        return (False, error_msg, True)  # 最后一个True表示是网络错误
        except asyncio.TimeoutError:
            # 超时错误单独处理
            error_msg = "请求超时"
            logger.warning(f"密钥验证超时: {api_key[:8]}*** (尝试 {attempt+1}/{max_retries+1})")
            if attempt < max_retries:
                import asyncio
                await asyncio.sleep(retry_delay)
                continue
            return (False, error_msg, True)  # 最后一个True表示是网络错误
        except Exception as e:
            # 其他网络错误，如果还有重试机会，则继续重试
            error_msg = f"请求失败: {str(e)}"
            logger.warning(f"密钥验证异常: {api_key[:8]}*** - {error_msg} (尝试 {attempt+1}/{max_retries+1})")
            if attempt < max_retries:
                import asyncio
                await asyncio.sleep(retry_delay)
                continue
            return (False, error_msg, True)  # 最后一个True表示是网络错误


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
            # 应用RPM和TPM限制
            if config.RPM_LIMIT > 0 or config.TPM_LIMIT > 0:
                from rate_limiter import get_available_keys
                available_keys = get_available_keys([k[0] for k in zero_balance_keys], config.RPM_LIMIT, config.TPM_LIMIT)
                if available_keys:
                    return random.choice(available_keys)
                # 如果所有key都超过限制，返回None
                return None
            return random.choice(zero_balance_keys)[0]
        # 如果没有余额为0的key，则返回None，表示无法处理此请求
        return None
    
    # 如果不使用余额为0的key，则筛选出余额大于0的key
    positive_balance_keys = [k for k in enabled_keys if float(k[1]) > 0]
    if not positive_balance_keys:
        return None
    
    # 使用正常的选择策略
    enabled_keys = positive_balance_keys
    
    # 应用RPM和TPM限制，筛选出未超过限制的key
    if config.RPM_LIMIT > 0 or config.TPM_LIMIT > 0:
        from rate_limiter import get_available_keys
        available_keys = get_available_keys([k[0] for k in enabled_keys], config.RPM_LIMIT, config.TPM_LIMIT)
        if not available_keys:
            # 如果所有key都超过限制，返回None
            return None
        # 使用可用的key列表替换原来的enabled_keys
        enabled_keys = [(k, next(b for a, b in enabled_keys if a == k)) for k in available_keys]

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
    
    # 轮询策略 - 按顺序依次分配请求到每个API账号
    elif config.CALL_STRATEGY == "round_robin":
        global round_robin_counter
        # 获取所有可用的key
        keys = [k[0] for k in enabled_keys]
        # 如果计数器超出范围，重置为0
        if round_robin_counter >= len(keys):
            round_robin_counter = 0
        # 选择当前计数器位置的key
        selected_key = keys[round_robin_counter]
        # 增加计数器，为下一次请求做准备
        round_robin_counter += 1
        return selected_key

    # 默认随机策略
    else:
        return random.choice(enabled_keys)[0]


async def check_and_remove_key(key: str):
    """检查密钥的有效性并更新余额，但不删除余额为0的有效key"""
    valid, balance, is_network_error = await validate_key_async(key)
    logger = logging.getLogger(__name__)
    if valid:
        logger.info(f"Key validation successful: {key[:8]}*** - Balance: {balance}")
        # 更新余额
        cursor.execute("UPDATE api_keys SET balance = ? WHERE key = ?", (balance, key))
        conn.commit()
    else:
        # 检查是否是网络错误
        error_message = str(balance).lower() if isinstance(balance, str) else ""
        is_network_error = any(err in error_message for err in ["timeout", "连接", "connection", "请求失败", "request failed", "network", "网络", "超时", "限流", "429"])
        
        # 获取当前密钥的余额
        cursor.execute("SELECT balance FROM api_keys WHERE key = ?", (key,))
        result = cursor.fetchone()
        current_balance = result[0] if result else 0
        
        if is_network_error:
            # 如果是网络错误，保持密钥状态不变
            logger.warning(f"Key validation failed with network error: {key[:8]}*** - Keeping in pool")
            return
        elif float(current_balance) > 0:
            # 如果当前余额大于0，保持密钥状态不变，避免错误地删除有余额的密钥
            logger.warning(f"Key validation failed but has positive balance (¥{current_balance}): {key[:8]}*** - Keeping in pool")
            return
        else:
            # 确认是无效密钥，从池中删除
            logger.warning(f"Invalid key detected: {key[:8]}*** - Removing from pool")
            cursor.execute("DELETE FROM api_keys WHERE key = ?", (key,))
            conn.commit()


def disable_api_key(key: str):
    """禁用API密钥但不删除，用于处理API调用失败的情况"""
    logger = logging.getLogger(__name__)
    
    # 获取当前密钥的余额
    cursor.execute("SELECT balance FROM api_keys WHERE key = ?", (key,))
    result = cursor.fetchone()
    current_balance = result[0] if result else 0
    
    # 如果密钥有余额，记录警告但不禁用
    if float(current_balance) > 0:
        logger.warning(f"API call failed with key: {key[:8]}*** - Key has positive balance (¥{current_balance}), not disabling")
        return
    
    # 无余额的密钥才禁用，但不删除
    logger.warning(f"API call failed with key: {key[:8]}*** - Disabling key (balance: ¥{current_balance})")
    cursor.execute("UPDATE api_keys SET enabled = 0 WHERE key = ?", (key,))
    conn.commit()
