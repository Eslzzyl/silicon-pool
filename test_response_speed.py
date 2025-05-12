import asyncio
import aiohttp
import time
import random
import string
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
##功能：我们刚刚创建的脚本，测试AI模型响应速度和输出质量
##用途：生成随机英文文本并测量响应时间、token速率等
##独立性：独立测试工具，其输出仅保存到test_results.json
# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 测试配置
TEST_URL = "http://host.docker.internal:7898/v1/chat/completions"  # 使用docker内部地址
TOTAL_TESTS = 10  # 总测试次数
MODEL_NAME = "THUDM/GLM-4-9B-0414"  # 待测试的模型
API_KEY = "xinchenmianfei"  # API密钥
TARGET_CHARS = 1000  # 目标字符数量
SAVE_RESULTS = True  # 是否保存测试结果到文件
RESULTS_FILE = "test_results.json"  # 结果文件名

def generate_random_text(target_chars=TARGET_CHARS):
    """生成接近指定字符数的随机英文文本"""
    # 常用词汇列表，使生成的文本更接近自然语言
    common_words = [
        "the", "be", "to", "of", "and", "a", "in", "that", "have", "I", 
        "it", "for", "not", "on", "with", "he", "as", "you", "do", "at",
        "this", "but", "his", "by", "from", "they", "we", "say", "her", "she",
        "or", "an", "will", "my", "one", "all", "would", "there", "their", "what",
        "so", "up", "out", "if", "about", "who", "get", "which", "go", "me",
        "when", "make", "can", "like", "time", "no", "just", "him", "know", "take",
        "people", "into", "year", "your", "good", "some", "could", "them", "see", "other",
        "than", "then", "now", "look", "only", "come", "its", "over", "think", "also",
        "back", "after", "use", "two", "how", "our", "work", "first", "well", "way",
        "even", "new", "want", "because", "any", "these", "give", "day", "most", "us"
    ]
    
    # 生成文本直到达到目标字符数
    current_chars = 0
    sentences = []
    
    while current_chars < target_chars:
        # 每个句子长度在5-15个单词之间
        sentence_length = random.randint(5, 15)
        words = [random.choice(common_words) for _ in range(sentence_length)]
        # 确保句子首字母大写
        words[0] = words[0].capitalize()
        # 句子末尾添加标点符号
        punctuation = random.choice(['.', '.', '.', '?', '!'])  # 更常用句号
        sentence = ' '.join(words) + punctuation + ' '
        sentences.append(sentence)
        current_chars += len(sentence)
        
        # 防止过度生成
        if current_chars > target_chars * 1.1:
            break
    
    # 将句子组合成段落
    text = ''
    current_paragraph = []
    sentences_per_paragraph = random.randint(3, 8)
    
    for i, sentence in enumerate(sentences):
        current_paragraph.append(sentence)
        if (i + 1) % sentences_per_paragraph == 0 or i == len(sentences) - 1:
            paragraph = ''.join(current_paragraph)
            text += paragraph + '\n\n'
            current_paragraph = []
            sentences_per_paragraph = random.randint(3, 8)
    
    # 截断到目标字符数
    if len(text) > target_chars:
        text = text[:target_chars]
    
    return text.strip()

def count_tokens(text):
    """简单估算英文文本的token数量，这是一个粗略的估计"""
    # 分词规则：按空格分词，标点符号算作单独的token
    words = re.findall(r'\b\w+\b|[.,!?;:]', text)
    return len(words)

async def make_request(session, test_id, prompt_text):
    """发送单个请求并记录结果"""
    start_time = time.time()
    tokens_in_prompt = count_tokens(prompt_text)
    tokens_in_response = 0
    
    try:
        # 准备请求体
        payload = {
            "model": MODEL_NAME,
            "messages": [{"role": "user", "content": prompt_text}]
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}"
        }
        
        async with session.post(TEST_URL, json=payload, headers=headers) as response:
            status = response.status
            if status == 200:
                # 成功请求
                data = await response.json()
                response_text = data.get('choices', [{}])[0].get('message', {}).get('content', '')
                completion_time = time.time() - start_time
                tokens_in_response = count_tokens(response_text)
                
                # 计算token生成速度
                token_generation_rate = tokens_in_response / completion_time if completion_time > 0 else 0
                
                logger.info(f"测试 {test_id} 成功：响应时间 {completion_time:.2f}秒，输出 {tokens_in_response} tokens，速率 {token_generation_rate:.2f} tokens/秒")
                
                return {
                    "success": True,
                    "time": completion_time,
                    "tokens_in": tokens_in_prompt,
                    "tokens_out": tokens_in_response,
                    "token_rate": token_generation_rate,
                    "error": None
                }
            else:
                # 请求失败但有响应
                error_text = await response.text()
                completion_time = time.time() - start_time
                logger.error(f"测试 {test_id} 失败：状态码 {status}，错误：{error_text}")
                
                return {
                    "success": False,
                    "time": completion_time,
                    "tokens_in": tokens_in_prompt,
                    "tokens_out": 0,
                    "token_rate": 0,
                    "error": f"状态码: {status}, 错误: {error_text}"
                }
    except Exception as e:
        # 请求异常
        completion_time = time.time() - start_time
        error_msg = str(e)
        logger.error(f"测试 {test_id} 异常：{error_msg}")
        
        return {
            "success": False,
            "time": completion_time,
            "tokens_in": tokens_in_prompt,
            "tokens_out": 0,
            "token_rate": 0,
            "error": f"异常: {error_msg}"
        }

async def run_tests():
    """运行所有测试并汇总结果"""
    logger.info("开始测试AI模型响应性能")
    overall_start_time = time.time()
    
    # 使用优化的连接池配置
    connector = aiohttp.TCPConnector(
        limit=10,  # 限制连接数
        force_close=False,
        enable_cleanup_closed=True,
        keepalive_timeout=120,  # 增加保持连接时间
        ttl_dns_cache=600,     # 增加DNS缓存时间
        limit_per_host=50000,  # 提高每个主机的最大连接数
        ssl=False,            # 禁用SSL以提高性能
        use_dns_cache=True    # 启用DNS缓存
    )
    
    timeout = aiohttp.ClientTimeout(total=120, connect=30, sock_connect=30, sock_read=120)
    
    results = []
    
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        for i in range(TOTAL_TESTS):
            logger.info(f"开始测试 {i+1}/{TOTAL_TESTS}")
            # 生成1000字左右的随机英文文本
            random_text = generate_random_text()  # 生成1000字符的随机文本
            logger.info(f"生成的随机文本长度: {len(random_text)} 字符")
            
            # 发送请求
            result = await make_request(session, i+1, random_text)
            results.append(result)
            
            # 在测试之间稍作暂停，避免过于频繁的请求
            if i < TOTAL_TESTS - 1:
                await asyncio.sleep(2)
    
    # 汇总结果
    successful_tests = [r for r in results if r["success"]]
    total_success = len(successful_tests)
    success_rate = total_success / TOTAL_TESTS * 100 if TOTAL_TESTS > 0 else 0
    error_rate = 100 - success_rate
    
    # 只计算成功请求的平均响应时间和token速率
    avg_response_time = sum(r["time"] for r in successful_tests) / total_success if total_success > 0 else 0
    avg_token_rate = sum(r["token_rate"] for r in successful_tests) / total_success if total_success > 0 else 0
    avg_tokens_in = sum(r["tokens_in"] for r in successful_tests) / total_success if total_success > 0 else 0
    avg_tokens_out = sum(r["tokens_out"] for r in successful_tests) / total_success if total_success > 0 else 0
    
    total_test_time = time.time() - overall_start_time
    
    # 打印最终统计结果
    logger.info("===== 测试完成 =====")
    logger.info(f"测试模型: {MODEL_NAME}")
    logger.info(f"总测试数: {TOTAL_TESTS}")
    logger.info(f"成功次数: {total_success}")
    logger.info(f"成功率: {success_rate:.2f}%")
    logger.info(f"错误率: {error_rate:.2f}%")
    logger.info(f"平均响应时间: {avg_response_time:.2f}秒")
    logger.info(f"平均输入tokens: {avg_tokens_in:.2f}")
    logger.info(f"平均输出tokens: {avg_tokens_out:.2f}")
    logger.info(f"平均输出速度: {avg_token_rate:.2f} tokens/秒")
    logger.info(f"总测试时间: {total_test_time:.2f}秒")
    
    # 保存结果到文件
    if SAVE_RESULTS:
        result_data = {
            "model": MODEL_NAME,
            "total_tests": TOTAL_TESTS,
            "success_count": total_success,
            "success_rate": success_rate,
            "error_rate": error_rate,
            "avg_response_time": avg_response_time,
            "avg_token_rate": avg_token_rate,
            "avg_tokens_in": avg_tokens_in,
            "avg_tokens_out": avg_tokens_out,
            "total_test_time": total_test_time,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "detailed_results": [
                {
                    "test_id": i+1,
                    "success": r["success"],
                    "time": r["time"],
                    "tokens_in": r["tokens_in"],
                    "tokens_out": r["tokens_out"],
                    "token_rate": r["token_rate"],
                    "error": r["error"]
                } for i, r in enumerate(results)
            ]
        }
        
        try:
            with open(RESULTS_FILE, 'w', encoding='utf-8') as f:
                json.dump(result_data, f, ensure_ascii=False, indent=2)
            logger.info(f"测试结果已保存到 {RESULTS_FILE}")
        except Exception as e:
            logger.error(f"保存结果失败: {str(e)}")
    
    # 返回详细的测试结果供进一步分析
    return {
        "model": MODEL_NAME,
        "total_tests": TOTAL_TESTS,
        "success_count": total_success,
        "success_rate": success_rate,
        "error_rate": error_rate,
        "avg_response_time": avg_response_time,
        "avg_token_rate": avg_token_rate,
        "avg_tokens_in": avg_tokens_in,
        "avg_tokens_out": avg_tokens_out,
        "total_test_time": total_test_time,
        "detailed_results": results
    }

if __name__ == "__main__":
    import argparse
    
    # 解析命令行参数
    parser = argparse.ArgumentParser(description='测试AI模型响应性能')
    parser.add_argument('--url', type=str, default=TEST_URL, help='API端点URL')
    parser.add_argument('--model', type=str, default=MODEL_NAME, help='模型名称')
    parser.add_argument('--tests', type=int, default=TOTAL_TESTS, help='测试次数')
    parser.add_argument('--chars', type=int, default=TARGET_CHARS, help='每次测试的目标字符数')
    parser.add_argument('--save', action='store_true', default=SAVE_RESULTS, help='是否保存结果')
    parser.add_argument('--output', type=str, default=RESULTS_FILE, help='结果文件名')
    
    args = parser.parse_args()
    
    # 更新配置
    TEST_URL = args.url
    MODEL_NAME = args.model
    TOTAL_TESTS = args.tests
    TARGET_CHARS = args.chars
    SAVE_RESULTS = args.save
    RESULTS_FILE = args.output
    
    # 在Windows环境中运行异步代码需要使用特定的事件循环策略
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run_tests())