import time
import threading
import logging
from typing import Dict, List, Tuple
from db import cursor, conn

# 全局锁，用于保护速率限制数据结构
lock = threading.RLock()

# 用于跟踪每个API密钥的使用情况
class KeyUsageTracker:
    def __init__(self):
        # 结构: {key: [(timestamp, request_count, token_count), ...]}
        self.usage_data: Dict[str, List[Tuple[float, int, int]]] = {}
        # 缓存每个key的当前RPM和TPM
        self.current_rpm: Dict[str, int] = {}
        self.current_tpm: Dict[str, int] = {}
        # 记录被限制的key及其恢复时间
        self.limited_keys: Dict[str, float] = {}
        
    def track_usage(self, key: str, request_count: int = 1, token_count: int = 0):
        """记录API密钥的使用情况"""
        with lock:
            current_time = time.time()
            
            # 初始化key的数据结构（如果不存在）
            if key not in self.usage_data:
                self.usage_data[key] = []
                self.current_rpm[key] = 0
                self.current_tpm[key] = 0
            
            # 添加新的使用记录
            self.usage_data[key].append((current_time, request_count, token_count))
            
            # 清理一分钟前的数据
            self._cleanup_old_data(key, current_time)
            
            # 更新当前RPM和TPM
            self._update_current_rates(key)
    
    def _cleanup_old_data(self, key: str, current_time: float):
        """清理一分钟前的数据"""
        one_minute_ago = current_time - 60
        self.usage_data[key] = [data for data in self.usage_data[key] if data[0] > one_minute_ago]
    
    def _update_current_rates(self, key: str):
        """更新当前RPM和TPM"""
        total_requests = sum(data[1] for data in self.usage_data[key])
        total_tokens = sum(data[2] for data in self.usage_data[key])
        
        self.current_rpm[key] = total_requests
        self.current_tpm[key] = total_tokens
    
    def check_limits(self, key: str, rpm_limit: int, tpm_limit: int) -> bool:
        """检查API密钥是否超过RPM或TPM限制
        
        Args:
            key: API密钥
            rpm_limit: 每分钟请求数限制，0表示不限制
            tpm_limit: 每分钟处理令牌数限制，0表示不限制
            
        Returns:
            True表示未超过限制，False表示已超过限制
        """
        with lock:
            current_time = time.time()
            
            # 检查key是否在限制列表中，如果是，检查是否已经过了限制时间
            if key in self.limited_keys:
                if current_time < self.limited_keys[key]:
                    return False  # 仍在限制期内
                else:
                    # 已过限制期，从限制列表中移除
                    del self.limited_keys[key]
            
            # 如果key不在使用数据中，初始化它
            if key not in self.usage_data:
                self.usage_data[key] = []
                self.current_rpm[key] = 0
                self.current_tpm[key] = 0
                return True  # 新key肯定未超过限制
            
            # 清理旧数据并更新当前速率
            self._cleanup_old_data(key, current_time)
            self._update_current_rates(key)
            
            # 检查RPM限制
            if rpm_limit > 0 and self.current_rpm[key] >= rpm_limit:
                # 将key添加到限制列表，限制1分钟
                self.limited_keys[key] = current_time + 60
                logging.warning(f"API密钥 {key[:8]}*** 超过RPM限制 {rpm_limit}，当前RPM: {self.current_rpm[key]}")
                return False
            
            # 检查TPM限制
            if tpm_limit > 0 and self.current_tpm[key] >= tpm_limit:
                # 将key添加到限制列表，限制1分钟
                self.limited_keys[key] = current_time + 60
                logging.warning(f"API密钥 {key[:8]}*** 超过TPM限制 {tpm_limit}，当前TPM: {self.current_tpm[key]}")
                return False
            
            return True
    
    def get_available_keys(self, keys: List[str], rpm_limit: int, tpm_limit: int) -> List[str]:
        """获取未超过限制的可用API密钥列表
        
        Args:
            keys: 要检查的API密钥列表
            rpm_limit: 每分钟请求数限制，0表示不限制
            tpm_limit: 每分钟处理令牌数限制，0表示不限制
            
        Returns:
            未超过限制的API密钥列表
        """
        with lock:
            current_time = time.time()
            available_keys = []
            
            for key in keys:
                # 检查key是否在限制列表中，如果是，检查是否已经过了限制时间
                if key in self.limited_keys:
                    if current_time >= self.limited_keys[key]:
                        # 已过限制期，从限制列表中移除
                        del self.limited_keys[key]
                        available_keys.append(key)
                else:
                    # 如果key不在使用数据中，初始化它
                    if key not in self.usage_data:
                        self.usage_data[key] = []
                        self.current_rpm[key] = 0
                        self.current_tpm[key] = 0
                        available_keys.append(key)
                        continue
                    
                    # 清理旧数据并更新当前速率
                    self._cleanup_old_data(key, current_time)
                    self._update_current_rates(key)
                    
                    # 检查RPM和TPM限制
                    if (rpm_limit <= 0 or self.current_rpm[key] < rpm_limit) and \
                       (tpm_limit <= 0 or self.current_tpm[key] < tpm_limit):
                        available_keys.append(key)
            
            return available_keys

# 创建全局使用跟踪器实例
key_usage_tracker = KeyUsageTracker()

# 提供给外部使用的函数
def track_key_usage(key: str, request_count: int = 1, token_count: int = 0):
    """记录API密钥的使用情况"""
    key_usage_tracker.track_usage(key, request_count, token_count)

def check_key_limits(key: str, rpm_limit: int, tpm_limit: int) -> bool:
    """检查API密钥是否超过RPM或TPM限制"""
    return key_usage_tracker.check_limits(key, rpm_limit, tpm_limit)

def get_available_keys(keys: List[str], rpm_limit: int, tpm_limit: int) -> List[str]:
    """获取未超过限制的可用API密钥列表"""
    return key_usage_tracker.get_available_keys(keys, rpm_limit, tpm_limit)