import asyncio
import aiohttp
import time
import argparse
import random
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor
import multiprocessing as mp

# 默认配置
DEFAULT_CONFIG = {
    "base_url": "http://localhost:7898",
    "api_key": "",  # 如果配置了custom_api_key，则这里需要填写对应的值
    "concurrency": 10,  # 并发连接数
    "total_requests": 100,  # 总请求数
    "endpoints": ["chat", "embeddings"],  # 要测试的端点
    "stream": True,  # 是否使用流式响应
}

# 请求模板
CHAT_REQUEST = {
    "model": "Qwen/Qwen2.5-14B-Instruct",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": True,  # 会根据命令行参数修改
}

EMBEDDING_REQUEST = {
    "model": "BAAI/bge-large-zh-v1.5",
    "input": "这是一个嵌入测试",
}


async def send_chat_request(session, url, api_key, stream=True):
    """发送聊天请求"""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    # 随机选择一些提示词
    prompts = [
        "你好，请介绍一下你自己。",
        "请用300字描述一下人工智能的发展历史。",
        "解释一下量子力学的基本原理。",
        "什么是大语言模型？",
        "写一首关于春天的诗。",
        "解释一下地球为什么是圆的？",
        "编写一个简单的Python函数来计算斐波那契数列。",
    ]

    payload = CHAT_REQUEST.copy()
    payload["stream"] = stream
    payload["messages"][0]["content"] = random.choice(prompts)

    start_time = time.time()
    try:
        if stream:
            async with session.post(url, json=payload, headers=headers) as response:
                if response.status == 200:
                    # 读取流式响应
                    async for chunk in response.content.iter_any():
                        pass  # 只消耗流，不处理内容
                else:
                    error_text = await response.text()
                    return False, response.status, time.time() - start_time, error_text
        else:
            async with session.post(url, json=payload, headers=headers) as response:
                await response.json()
                return True, response.status, time.time() - start_time, None
    except Exception as e:
        return False, 0, time.time() - start_time, str(e)

    return True, 200, time.time() - start_time, None


async def send_embedding_request(session, url, api_key):
    """发送嵌入请求"""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    # 随机选择一些文本
    texts = [
        "这是一个嵌入测试",
        "人工智能正在迅速发展",
        "大语言模型可以处理自然语言",
        "Python是一种流行的编程语言",
        "机器学习使计算机能够从数据中学习",
    ]

    payload = EMBEDDING_REQUEST.copy()
    payload["input"] = random.choice(texts)

    start_time = time.time()
    try:
        async with session.post(url, json=payload, headers=headers) as response:
            await response.json()
            return True, response.status, time.time() - start_time, None
    except Exception as e:
        return False, 0, time.time() - start_time, str(e)


async def worker(session, config, request_id, endpoint, results_queue):
    """工作线程，发送请求并收集结果"""
    base_url = config["base_url"]
    api_key = config["api_key"]
    stream = config["stream"]

    if endpoint == "chat":
        url = f"{base_url}/v1/chat/completions"
        success, status, duration, error = await send_chat_request(
            session, url, api_key, stream
        )
    elif endpoint == "embeddings":
        url = f"{base_url}/v1/embeddings"
        success, status, duration, error = await send_embedding_request(
            session, url, api_key
        )
    else:
        raise ValueError(f"不支持的端点: {endpoint}")

    results_queue.put(
        {
            "id": request_id,
            "endpoint": endpoint,
            "success": success,
            "status": status,
            "duration": duration,
            "error": error,
            "timestamp": time.time(),
        }
    )


async def run_test(config, results_queue):
    """运行测试"""
    base_url = config["base_url"]
    concurrency = config["concurrency"]
    total_requests = config["total_requests"]
    endpoints = config["endpoints"]

    print(f"开始压力测试 - 并发: {concurrency}, 总请求数: {total_requests}")
    print(f"目标服务器: {base_url}")
    print(f"测试端点: {', '.join(endpoints)}")
    print("-" * 50)

    # 创建TCP连接池
    connector = aiohttp.TCPConnector(limit=concurrency, limit_per_host=concurrency)

    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = []
        for i in range(total_requests):
            # 随机选择一个端点
            endpoint = random.choice(endpoints)
            task = asyncio.create_task(
                worker(session, config, i, endpoint, results_queue)
            )
            tasks.append(task)

        # 启动所有任务并等待完成
        await asyncio.gather(*tasks)

    print("测试完成，正在生成报告...")


def generate_report(results, test_duration):
    """生成测试报告"""
    if not results:
        print("没有收集到结果数据")
        return

    # 计算总体统计信息
    total_requests = len(results)
    successful_requests = sum(1 for r in results if r["success"])
    failed_requests = total_requests - successful_requests
    success_rate = (
        (successful_requests / total_requests) * 100 if total_requests > 0 else 0
    )

    # 按端点分组计算统计信息
    endpoints = {}
    for r in results:
        endpoint = r["endpoint"]
        if endpoint not in endpoints:
            endpoints[endpoint] = {"count": 0, "success": 0, "durations": []}

        endpoints[endpoint]["count"] += 1
        if r["success"]:
            endpoints[endpoint]["success"] += 1
            endpoints[endpoint]["durations"].append(r["duration"])

    # 计算响应时间统计信息
    all_durations = [r["duration"] for r in results if r["success"]]
    avg_duration = sum(all_durations) / len(all_durations) if all_durations else 0
    rps = total_requests / test_duration if test_duration > 0 else 0

    # 打印报告
    print("\n" + "=" * 60)
    print(f"压力测试报告 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    print(f"总请求数: {total_requests}")
    print(f"成功请求: {successful_requests} ({success_rate:.2f}%)")
    print(f"失败请求: {failed_requests} ({100 - success_rate:.2f}%)")
    print(f"平均响应时间: {avg_duration:.3f}秒")
    print(f"每秒请求数 (RPS): {rps:.2f}")
    print(f"测试持续时间: {test_duration:.2f}秒")
    print("-" * 60)

    # 输出按端点分组的统计信息
    print("按端点分组的统计信息:")
    for endpoint, stats in endpoints.items():
        success_rate = (
            (stats["success"] / stats["count"]) * 100 if stats["count"] > 0 else 0
        )
        avg_time = (
            sum(stats["durations"]) / len(stats["durations"])
            if stats["durations"]
            else 0
        )
        print(f"  {endpoint}:")
        print(f"    请求数: {stats['count']}")
        print(f"    成功率: {success_rate:.2f}%")
        print(f"    平均响应时间: {avg_time:.3f}秒")

    print("-" * 60)

    # 错误分析
    if failed_requests > 0:
        print("错误分析:")
        error_counts = {}
        for r in results:
            if not r["success"] and r["error"]:
                error_msg = r["error"][:100]  # 截断长错误消息
                if error_msg in error_counts:
                    error_counts[error_msg] += 1
                else:
                    error_counts[error_msg] = 1

        for error, count in sorted(
            error_counts.items(), key=lambda x: x[1], reverse=True
        ):
            print(f"  [{count}] {error}")

    print("=" * 60)


def process_worker(config):
    """进程池工作函数"""
    # 创建多进程安全的队列用于收集结果
    manager = mp.Manager()
    results_queue = manager.Queue()

    # 运行测试
    start_time = time.time()
    asyncio.run(run_test(config, results_queue))
    test_duration = time.time() - start_time

    # 收集结果
    results = []
    while not results_queue.empty():
        results.append(results_queue.get())

    # 生成报告
    generate_report(results, test_duration)


def main():
    parser = argparse.ArgumentParser(description="Silicon Pool API 压力测试工具")
    parser.add_argument(
        "--url", default=DEFAULT_CONFIG["base_url"], help="服务器基础URL"
    )
    parser.add_argument(
        "--key",
        default=DEFAULT_CONFIG["api_key"],
        help="API Key (如果启用了custom_api_key)",
    )
    parser.add_argument(
        "-c",
        "--concurrency",
        type=int,
        default=DEFAULT_CONFIG["concurrency"],
        help="并发连接数",
    )
    parser.add_argument(
        "-n",
        "--requests",
        type=int,
        default=DEFAULT_CONFIG["total_requests"],
        help="总请求数",
    )
    parser.add_argument(
        "--endpoints",
        default=",".join(DEFAULT_CONFIG["endpoints"]),
        help="要测试的端点,以逗号分隔 (可选: chat,embeddings)",
    )
    parser.add_argument("--no-stream", action="store_true", help="禁用流式响应")
    parser.add_argument(
        "--processes", type=int, default=1, help="使用的进程数 (默认: 1)"
    )

    args = parser.parse_args()

    # 准备配置
    config = {
        "base_url": args.url,
        "api_key": args.key,
        "concurrency": args.concurrency,
        "total_requests": args.requests,
        "endpoints": args.endpoints.split(","),
        "stream": not args.no_stream,
    }

    if args.processes > 1:
        print(f"使用 {args.processes} 个进程进行测试")
        # 根据进程数分配请求
        requests_per_process = args.requests // args.processes
        remaining = args.requests % args.processes

        with ProcessPoolExecutor(max_workers=args.processes) as executor:
            futures = []
            for i in range(args.processes):
                # 最后一个进程处理剩余的请求
                process_requests = requests_per_process + (
                    remaining if i == args.processes - 1 else 0
                )
                process_config = config.copy()
                process_config["total_requests"] = process_requests
                futures.append(executor.submit(process_worker, process_config))

            # 等待所有进程完成
            for future in futures:
                future.result()
    else:
        # 单进程执行
        process_worker(config)


if __name__ == "__main__":
    main()


# 使用示例
"""
# 简单测试，10个并发发送100个请求
python scripts/stress.py 

# 高并发测试，使用50个并发发送500个请求，使用2个进程
python scripts/stress.py -c 50 -n 500 --processes 2

# 仅测试chat接口，不使用流式响应
python scripts/stress.py --endpoints chat --no-stream

# 指定API Key (如果启用了custom_api_key验证)
python scripts/stress.py --key "your-api-key"
"""