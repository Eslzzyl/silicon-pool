import asyncio
import aiohttp
import time
import logging
from concurrent.futures import ThreadPoolExecutor
##功能：高并发测试脚本，特别关注EOF错误
##用途：评估系统在高负载下的稳定性和性能
##独立性：纯测试工具，不会影响主系统逻辑
# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 测试配置
TEST_URL = "http://host.docker.internal:7898/v1/chat/completions"  # 使用docker内部地址
CONCURRENT_REQUESTS = 100  # 并发请求数
TOTAL_REQUESTS = 500  # 总请求数
TEST_ROUNDS = 3  # 测试轮次

async def make_request(session, request_id):
    """发送单个请求并记录结果"""
    start_time = time.time()
    try:
        # 简单的请求体，实际使用时应替换为有效的请求
        payload = {
            "model": "THUDM/GLM-4-9B-0414",
            "messages": [{"role": "user", "content": f"测试请求 {request_id}"}]
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer xinchenmianfei"
        }
        
        async with session.post(TEST_URL, json=payload, headers=headers) as response:
            status = response.status
            if status == 200:
                # 成功请求
                return True, time.time() - start_time, None
            else:
                # 请求失败但有响应
                error_text = await response.text()
                return False, time.time() - start_time, f"状态码: {status}, 错误: {error_text}"
    except Exception as e:
        # 请求异常
        error_msg = str(e)
        is_eof = "EOF" in error_msg
        return False, time.time() - start_time, f"异常: {error_msg} {'[EOF错误]' if is_eof else ''}"

async def run_test_batch(batch_size, batch_id):
    """运行一批并发请求"""
    logger.info(f"开始批次 {batch_id}，并发数: {batch_size}")
    
    # 使用优化的连接池配置
    connector = aiohttp.TCPConnector(
        limit=batch_size,  # 限制连接数与并发数匹配
        force_close=False,
        enable_cleanup_closed=True,
        keepalive_timeout=120,  # 增加保持连接时间
        ttl_dns_cache=600,     # 增加DNS缓存时间
        limit_per_host=50000,  # 提高每个主机的最大连接数
        ssl=False,            # 禁用SSL以提高性能
        use_dns_cache=True    # 启用DNS缓存
    )
    
    timeout = aiohttp.ClientTimeout(total=60, connect=30, sock_connect=30, sock_read=60)
    
    # 初始化结果变量
    results = []
    success_count = 0
    eof_errors = 0
    other_errors = 0
    total_time = 0
    
    try:
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            tasks = [make_request(session, f"{batch_id}-{i}") for i in range(batch_size)]
            
            # 使用asyncio.shield防止任务被取消
            shielded_tasks = [asyncio.shield(task) for task in tasks]
            try:
                results = await asyncio.gather(*shielded_tasks, return_exceptions=True)
            except asyncio.CancelledError as e:
                logger.error(f"任务被取消: {str(e)}")
                logger.warning(f"批次 {batch_id} 被取消，正在清理资源...")
                # 取消所有剩余任务
                for task in shielded_tasks:
                    if not task.done():
                        task.cancel()
                # 等待所有任务完成
                results = await asyncio.gather(*shielded_tasks, return_exceptions=True)
    except Exception as e:
        logger.error(f"批次 {batch_id} 发生未预期的异常: {str(e)}")
        # 不再抛出异常，而是返回默认结果
    
    # 统计结果 - 正确的缩进，确保无论是否发生异常都会执行
    for result in results:
        if isinstance(result, Exception):
            error_msg = str(result)
            if "EOF" in error_msg:
                eof_errors += 1
            else:
                other_errors += 1
            logger.error(f"请求异常: {error_msg}")
        else:
            success, time_taken, error = result
            total_time += time_taken
            if success:
                success_count += 1
            else:
                if error and "EOF" in error:
                    eof_errors += 1
                else:
                    other_errors += 1
                logger.error(f"请求失败: {error}")
    
    avg_time = total_time / batch_size if batch_size > 0 else 0
    logger.info(f"批次 {batch_id} 完成: 成功 {success_count}/{batch_size}, EOF错误 {eof_errors}, 其他错误 {other_errors}, 平均响应时间 {avg_time:.4f}秒")
    
    return {
        "batch_id": batch_id,
        "success": success_count,
        "eof_errors": eof_errors,
        "other_errors": other_errors,
        "avg_time": avg_time,
        "batch_size": batch_size
        }

async def run_test_round(round_id, concurrent_requests, total_requests):
    """运行一轮测试，包含多个批次"""
    logger.info(f"===== 开始测试轮次 {round_id} =====")
    logger.info(f"并发请求数: {concurrent_requests}, 总请求数: {total_requests}")
    
    batches = []
    remaining = total_requests
    batch_id = 0
    
    while remaining > 0:
        batch_size = min(concurrent_requests, remaining)
        batches.append(run_test_batch(batch_size, f"{round_id}-{batch_id}"))
        remaining -= batch_size
        batch_id += 1
    
    batch_results = await asyncio.gather(*batches, return_exceptions=True)
    
    # 过滤掉None值和异常
    valid_results = []
    for r in batch_results:
        if r is not None and not isinstance(r, Exception):
            valid_results.append(r)
        elif isinstance(r, Exception):
            logger.error(f"批次执行异常: {str(r)}")
    
    # 汇总结果
    total_success = sum(r["success"] for r in valid_results)
    total_eof = sum(r["eof_errors"] for r in valid_results)
    total_other_errors = sum(r["other_errors"] for r in valid_results)
    total_requests_sent = sum(r["batch_size"] for r in valid_results)
    avg_time = sum(r["avg_time"] * r["batch_size"] for r in valid_results) / total_requests_sent if total_requests_sent > 0 else 0
    
    logger.info(f"===== 测试轮次 {round_id} 完成 =====")
    logger.info(f"总成功率: {total_success}/{total_requests_sent} ({total_success/total_requests_sent*100:.2f}%)")
    logger.info(f"EOF错误: {total_eof} ({total_eof/total_requests_sent*100:.2f}%)")
    logger.info(f"其他错误: {total_other_errors} ({total_other_errors/total_requests_sent*100:.2f}%)")
    logger.info(f"平均响应时间: {avg_time:.4f}秒")
    
    return {
        "round_id": round_id,
        "success": total_success,
        "eof_errors": total_eof,
        "other_errors": total_other_errors,
        "total_requests": total_requests_sent,
        "avg_time": avg_time
    }

async def main():
    """主测试函数"""
    logger.info("开始高并发测试，检测EOF错误修复效果")
    start_time = time.time()
    
    # 运行多轮测试
    rounds = []
    for i in range(TEST_ROUNDS):
        rounds.append(run_test_round(i+1, CONCURRENT_REQUESTS, TOTAL_REQUESTS))
        # 轮次之间稍微暂停，让系统恢复
        if i < TEST_ROUNDS - 1:
            await asyncio.sleep(5)
    
    round_results = await asyncio.gather(*rounds, return_exceptions=True)
    
    # 过滤掉None值和异常
    valid_round_results = []
    for r in round_results:
        if r is not None and not isinstance(r, Exception):
            valid_round_results.append(r)
        elif isinstance(r, Exception):
            logger.error(f"轮次执行异常: {str(r)}")
    
    # 汇总所有轮次结果
    total_success = sum(r["success"] for r in valid_round_results)
    total_eof = sum(r["eof_errors"] for r in valid_round_results)
    total_other_errors = sum(r["other_errors"] for r in valid_round_results)
    total_requests_sent = sum(r["total_requests"] for r in valid_round_results)
    avg_time = sum(r["avg_time"] * r["total_requests"] for r in valid_round_results) / total_requests_sent if total_requests_sent > 0 else 0
    
    logger.info("===== 测试完成 =====")
    logger.info(f"总测试轮次: {TEST_ROUNDS}")
    logger.info(f"总请求数: {total_requests_sent}")
    logger.info(f"总成功率: {total_success}/{total_requests_sent} ({total_success/total_requests_sent*100:.2f}%)")
    logger.info(f"EOF错误: {total_eof} ({total_eof/total_requests_sent*100:.2f}%)")
    logger.info(f"其他错误: {total_other_errors} ({total_other_errors/total_requests_sent*100:.2f}%)")
    logger.info(f"平均响应时间: {avg_time:.4f}秒")
    logger.info(f"总测试时间: {time.time() - start_time:.2f}秒")
    
    # 判断测试是否通过
    if total_eof == 0:
        logger.info("✅ 测试通过: 未检测到EOF错误，修复成功!")
    else:
        eof_rate = total_eof / total_requests_sent * 100
        if eof_rate < 1.0:  # 如果EOF错误率低于1%，也可以接受
            logger.info(f"⚠️ 测试基本通过: EOF错误率较低 ({eof_rate:.2f}%)，但仍有改进空间")
        else:
            logger.info(f"❌ 测试失败: EOF错误率过高 ({eof_rate:.2f}%)，需要进一步优化")

if __name__ == "__main__":
    # 在Windows环境中运行异步代码需要使用特定的事件循环策略
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())