import asyncio
import logging
import time
from typing import Dict, List, Callable, Any, Awaitable, Optional, Tuple
import aiohttp

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class RequestQueueManager:
    """请求队列管理器，用于处理高并发请求"""
    
    def __init__(self, max_concurrent_requests: int = 400000, queue_timeout: float = 180.0):
        """初始化请求队列管理器
        
        Args:
            max_concurrent_requests: 最大并发请求数
            queue_timeout: 队列等待超时时间（秒）
        """
        self.max_concurrent_requests = max_concurrent_requests
        self.queue_timeout = queue_timeout
        self.active_requests = 0
        self.semaphore = asyncio.Semaphore(max_concurrent_requests)
        # 增加队列容量以支持高并发场景
        self.request_queue: asyncio.Queue = asyncio.Queue(maxsize=400000)  # 设置更大的队列容量
        self.processing = False
        self._task: Optional[asyncio.Task] = None
        self.client_session = None  # 共享的HTTP会话
        # 配置重试策略
        self.max_retries = 8  # 最大重试次数提升
        self.retry_delay_base = 0.5  # 基础重试延迟时间（秒）缩短
        # 健康检查状态
        self.downstream_healthy = True
        self.last_health_check = 0
        self.health_check_interval = 2  # 每2秒检测一次下游健康
        # 统计信息
        self.total_requests = 0
        self.successful_requests = 0
        self.failed_requests = 0
        self.queued_requests = 0
        self.last_reset_time = time.time()
    
    async def start_processing(self):
        """启动队列处理"""
        if self.processing:
            return
        self.processing = True
        # 创建共享的HTTP会话，用于所有请求
        # 优化连接池配置，解决EOF错误问题
        self.client_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=600, connect=30, sock_connect=30, sock_read=60),
            connector=aiohttp.TCPConnector(
                limit=min(self.max_concurrent_requests, 10000),  # 提高连接池上限以支持高并发
                force_close=False,     # 允许连接复用
                enable_cleanup_closed=True,
                keepalive_timeout=120,  # 进一步增加保持连接活跃时间
                ttl_dns_cache=600,    # 进一步增加DNS缓存时间
                limit_per_host=50000,   # 提高每个主机的最大连接数以支持50万并发
                ssl=False,            # 如果不需要SSL，禁用可提高性能
                use_dns_cache=True    # 启用DNS缓存
            )
        )
        self._task = asyncio.create_task(self._process_queue())
        logger.info(f"请求队列处理器已启动，最大并发数: {self.max_concurrent_requests}，队列超时: {self.queue_timeout}秒")
    
    async def stop_processing(self):
        """停止队列处理"""
        if not self.processing:
            return
            
        self.processing = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
            
        # 关闭共享的HTTP会话
        if self.client_session:
            await self.client_session.close()
            self.client_session = None
            
        logger.info("请求队列处理器已停止")
    
    async def _process_queue(self):
        """处理请求队列"""
        while self.processing:
            try:
                # 从队列获取请求
                request_item = await self.request_queue.get()
                func, args, kwargs, future = request_item
                
                # 使用信号量控制并发
                async with self.semaphore:
                    self.active_requests += 1
                    self.queued_requests -= 1
                    
                    # 执行请求，使用增强的重试机制
                    retry_count = 0
                    last_error = None
                    
                    while retry_count <= self.max_retries:
                        try:
                            # 检查是否有HTTP会话参数，如果有且我们有共享会话，则使用共享会话
                            if 'session' in kwargs and kwargs['session'] is None and self.client_session:
                                kwargs['session'] = self.client_session
                                
                            result = await func(*args, **kwargs)
                            if not future.done():
                                future.set_result(result)
                            self.successful_requests += 1
                            break  # 成功执行，跳出重试循环
                        except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionError) as e:
                            # 网络相关错误，可以重试
                            last_error = e
                            retry_count += 1
                            if retry_count <= self.max_retries:
                                # 检查是否为EOF错误，这通常是连接被对方关闭导致的
                                is_eof_error = str(e).endswith("EOF") or "EOF" in str(e)
                                if is_eof_error:
                                    logger.warning(f"检测到EOF错误，准备第{retry_count}次重试")
                                    # EOF错误时创建新的会话进行重试
                                    if 'session' in kwargs and self.client_session:
                                        # 关闭旧会话连接
                                        old_session = kwargs['session']
                                        if old_session == self.client_session:
                                            # 如果使用的是共享会话，创建一个新的临时会话
                                            kwargs['session'] = aiohttp.ClientSession(
                                                timeout=aiohttp.ClientTimeout(total=600, connect=30, sock_connect=30, sock_read=60),
                                                connector=aiohttp.TCPConnector(limit=10, force_close=True)
                                            )
                                    # EOF错误使用更短的延迟时间，但更多重试次数
                                    await asyncio.sleep(self.retry_delay_base)  # 固定短延迟
                                else:
                                    logger.warning(f"请求执行失败，准备第{retry_count}次重试: {str(e)}")
                                    await asyncio.sleep(self.retry_delay_base * retry_count)  # 指数退避
                            else:
                                # 重试次数用尽
                                if not future.done():
                                    future.set_exception(e)
                                self.failed_requests += 1
                                logger.error(f"请求执行失败，重试{self.max_retries}次后仍然失败: {str(e)}")
                        except Exception as e:
                            # 其他非网络错误，不重试
                            if not future.done():
                                future.set_exception(e)
                            self.failed_requests += 1
                            logger.error(f"请求执行失败(非网络错误): {str(e)}")
                            break
                    
                    self.active_requests -= 1
                    self.request_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"队列处理异常: {str(e)}")
                # 尝试恢复队列处理
                await asyncio.sleep(1)
    
    async def _check_downstream_health(self, url="http://localhost:7898/health"):
        """检测下游服务健康状态"""
        now = time.time()
        if now - self.last_health_check < self.health_check_interval:
            return self.downstream_healthy
        self.last_health_check = now
        
        # 健康检查失败次数计数，避免因短暂网络问题导致误判
        retry_count = 0
        max_retries = 3
        retry_delay = 1.0
        
        while retry_count < max_retries:
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2)) as session:
                    async with session.get(url) as resp:
                        status_code = resp.status
                        if status_code == 200:
                            # 健康检查成功，立即更新状态并返回
                            self.downstream_healthy = True
                            return self.downstream_healthy
                        else:
                            # 状态码不为200，记录警告但不立即判定为不健康
                            logger.warning(f"下游服务健康检查返回非200状态码，URL: {url}, 状态码: {status_code}, 重试 {retry_count+1}/{max_retries}")
            except Exception as e:
                # 发生异常，记录错误但不立即判定为不健康
                logger.warning(f"下游服务健康检查异常，URL: {url}, 错误: {str(e)}, 重试 {retry_count+1}/{max_retries}")
            
            # 增加重试次数并等待
            retry_count += 1
            if retry_count < max_retries:
                await asyncio.sleep(retry_delay)
        
        # 所有重试都失败后才将服务标记为不健康
        if self.downstream_healthy:  # 只有当之前是健康状态时才记录转变为不健康的日志
            logger.error(f"下游服务健康检查失败，经过 {max_retries} 次重试后仍然失败，URL: {url}")
        self.downstream_healthy = False
        return self.downstream_healthy
    
    async def enqueue_request(self, func: Callable[..., Awaitable[Any]], *args, **kwargs) -> Any:
        """将请求加入队列
        
        Args:
            func: 异步函数
            *args: 函数参数
            **kwargs: 函数关键字参数
            
        Returns:
            函数执行结果
            
        Raises:
            asyncio.TimeoutError: 如果请求在队列中等待超时
            Exception: 如果请求执行失败
        """
        self.total_requests += 1
        
        # 空KEY等无效请求提前拦截，避免无意义请求进入队列
        if 'api_key' in kwargs and (not kwargs['api_key'] or str(kwargs['api_key']).strip() == ""):
            logger.warning("检测到无效API KEY，直接拒绝请求，不进入队列")
            raise ValueError("无效API KEY，拒绝请求")
        
        # 如果队列未启动，则启动
        if not self.processing:
            await self.start_processing()
        
        # 创建Future对象
        future = asyncio.get_event_loop().create_future()
        
        # 检查是否有aiohttp.ClientSession参数，如果有，考虑使用共享会话
        if 'session' in kwargs and kwargs['session'] is None and self.client_session:
            # 标记使用共享会话，但在实际执行时才替换，避免会话被过早关闭
            kwargs['session'] = None
        
        # 请求前健康检查，若下游不健康则自适应降载
        healthy = await self._check_downstream_health()
        if not healthy:
            logger.warning("下游服务不健康，主动降载，延迟处理请求")
            await asyncio.sleep(2)
        # 动态调整并发阈值，防止下游服务被打爆
        direct_execution_threshold = max(1, int(self.max_concurrent_requests * 0.2))
        if self.active_requests < direct_execution_threshold and self.request_queue.empty():
            self.active_requests += 1
            try:
                if 'session' in kwargs and kwargs['session'] is None and self.client_session:
                    kwargs['session'] = self.client_session
                retry_count = 0
                last_error = None
                while retry_count <= self.max_retries:
                    try:
                        result = await func(*args, **kwargs)
                        future.set_result(result)
                        self.successful_requests += 1
                        break
                    except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionError) as e:
                        last_error = e
                        retry_count += 1
                        if retry_count <= self.max_retries:
                            is_eof_error = str(e).endswith("EOF") or "EOF" in str(e)
                            if is_eof_error:
                                logger.warning(f"直接执行时检测到EOF错误，准备第{retry_count}次重试")
                                # EOF错误时创建新的会话进行重试
                                if 'session' in kwargs and self.client_session:
                                    # 关闭旧会话连接
                                    old_session = kwargs['session']
                                    if old_session == self.client_session:
                                        # 如果使用的是共享会话，创建一个新的临时会话
                                        kwargs['session'] = aiohttp.ClientSession(
                                            timeout=aiohttp.ClientTimeout(total=600, connect=30, sock_connect=30, sock_read=60),
                                            connector=aiohttp.TCPConnector(limit=10, force_close=True)
                                        )
                                # EOF错误使用更短的延迟时间
                                await asyncio.sleep(self.retry_delay_base)
                            else:
                                logger.warning(f"直接执行请求失败，准备第{retry_count}次重试: {str(e)}")
                                await asyncio.sleep(self.retry_delay_base * retry_count)
                        else:
                            raise
                    except Exception as e:
                        raise
            except Exception as e:
                future.set_exception(e)
                self.failed_requests += 1
                raise
            finally:
                self.active_requests -= 1
        else:
            try:
                self.queued_requests += 1
                try:
                    await asyncio.wait_for(self.request_queue.put((func, args, kwargs, future)), 5.0)
                except asyncio.TimeoutError:
                    self.queued_requests -= 1
                    logger.warning("队列已满，无法加入新请求，尝试直接执行")
                    if 'session' in kwargs and kwargs['session'] is None and self.client_session:
                        kwargs['session'] = self.client_session
                    result = await func(*args, **kwargs)
                    future.set_result(result)
                    self.successful_requests += 1
                    return result
                try:
                    return await asyncio.wait_for(future, self.queue_timeout)
                except asyncio.TimeoutError:
                    self.failed_requests += 1
                    logger.warning(f"请求在队列中等待超时(超过{self.queue_timeout}秒)")
                    raise
            except Exception as e:
                if not isinstance(e, asyncio.TimeoutError):
                    logger.error(f"队列处理异常: {str(e)}")
                raise
        return await future
    
    def get_stats(self) -> Dict[str, Any]:
        """获取队列统计信息"""
        current_time = time.time()
        elapsed = current_time - self.last_reset_time
        
        return {
            "active_requests": self.active_requests,
            "queued_requests": self.queued_requests,
            "total_requests": self.total_requests,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "requests_per_second": self.total_requests / elapsed if elapsed > 0 else 0,
            "success_rate": (self.successful_requests / self.total_requests * 100) if self.total_requests > 0 else 0,
            "uptime_seconds": elapsed
        }
    
    def reset_stats(self):
        """重置统计信息"""
        self.total_requests = 0
        self.successful_requests = 0
        self.failed_requests = 0
        self.last_reset_time = time.time()

    async def _cleanup_connections(self):
        """清理连接池中的空闲连接"""
        while self.processing:
            await asyncio.sleep(30)  # 每30秒清理一次
            if self.client_session:
                try:
                    # 清理所有空闲超过30秒的连接
                    await self.client_session._connector._cleanup()
                    # 强制关闭空闲连接
                    for key, conns in list(self.client_session._connector._conns.items()):
                        for conn in conns:
                            if conn.idle_time() > 30:
                                conn.close()
                    logger.debug("连接池清理完成")
                except Exception as e:
                    logger.warning(f"清理连接池时发生错误: {str(e)}")

    async def start_processing(self):
        """启动队列处理"""
        if self.processing:
            return
        self.processing = True
        # 创建共享的HTTP会话，用于所有请求
        # 优化连接池配置，解决EOF错误问题
        self.client_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=600, connect=30, sock_connect=30, sock_read=60),
            connector=aiohttp.TCPConnector(
                limit=min(self.max_concurrent_requests, 10000),  # 提高连接池上限以支持高并发
                force_close=False,     # 允许连接复用
                enable_cleanup_closed=True,
                keepalive_timeout=300,  # 进一步增加保持连接活跃时间
                ttl_dns_cache=600,    # 进一步增加DNS缓存时间
                limit_per_host=50000,    # 提高每个主机的最大连接数以支持50万并发
                ssl=False,            # 如果不需要SSL，禁用可提高性能
                use_dns_cache=True    # 启用DNS缓存
            )
        )
        self._task = asyncio.create_task(self._process_queue())
        # 启动连接池清理任务
        asyncio.create_task(self._cleanup_connections())
        logger.info(f"请求队列处理器已启动，最大并发数: {self.max_concurrent_requests}，队列超时: {self.queue_timeout}秒")

# 创建全局请求队列管理器实例
# 设置合理的并发限制和超时时间，解决高并发请求时的EOF错误
# 调整为更合理的并发值，避免系统资源耗尽的同时支持高并发
request_queue_manager = RequestQueueManager(max_concurrent_requests=50000, queue_timeout=180.0)