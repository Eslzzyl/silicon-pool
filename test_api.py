import asyncio
import aiohttp
import sys
import logging
import traceback
##验证API密钥和获取余额
##用途：简单的密钥验证测试工具
##独立性：独立运行，不影响其他组件
# 配置日志输出到控制台
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

async def validate_key(api_key):
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with aiohttp.ClientSession() as session:
            print(f"开始请求API验证密钥...")
            logger.info(f"开始请求API验证密钥...")
            async with session.get(
                "https://api.siliconflow.cn/v1/user/info", headers=headers, timeout=30
            ) as r:
                print(f"API响应状态码: {r.status}")
                logger.info(f"API响应状态码: {r.status}")
                if r.status == 200:
                    try:
                        data = await r.json()
                        print(f"API返回数据: {data}")
                        logger.info(f"API返回数据: {data}")
                        if "data" in data and "totalBalance" in data.get("data", {}):
                            balance = data["data"]["totalBalance"]
                            print(f"获取到余额: {balance}, 类型: {type(balance)}")
                            logger.info(f"获取到余额: {balance}, 类型: {type(balance)}")
                            # 确保余额是数字
                            if isinstance(balance, (int, float)):
                                return True, balance
                            elif isinstance(balance, str) and balance.replace('.', '', 1).isdigit():
                                return True, float(balance)
                            else:
                                print(f"余额格式异常: {balance}, 设置为0")
                                logger.warning(f"余额格式异常: {balance}, 设置为0")
                                return True, 0
                        else:
                            print("API响应成功但没有余额信息")
                            logger.warning("API响应成功但没有余额信息")
                            return True, 0
                    except Exception as e:
                        print(f"解析API响应失败: {str(e)}")
                        logger.error(f"解析API响应失败: {str(e)}")
                        print(traceback.format_exc())
                        return True, 0
                else:
                    try:
                        data = await r.json()
                        print(f"API错误返回: {data}")
                        logger.warning(f"API错误返回: {data}")
                        return False, data.get("message", "验证失败")
                    except Exception as e:
                        print(f"解析错误响应失败: {str(e)}")
                        logger.error(f"解析错误响应失败: {str(e)}")
                        return False, f"HTTP错误 {r.status}"
    except asyncio.TimeoutError:
        print("请求超时")
        logger.error("请求超时")
        return False, "请求超时"
    except Exception as e:
        print(f"请求异常: {str(e)}")
        logger.error(f"请求异常: {str(e)}")
        print(traceback.format_exc())
        return False, f"请求失败: {str(e)}"

async def main():
    try:
        # 从命令行获取密钥，如果没有提供则使用默认值
        key = sys.argv[1] if len(sys.argv) > 1 else 'sk-luthegyrkenqqorbiiewzavuuztxgbebtmpphxyajbbpytfb'
        logger.info(f"测试密钥: {key[:8]}***")
        result = await validate_key(key)
        logger.info(f'验证结果: {result}')
        
        # 详细分析结果
        valid, balance = result
        logger.info(f'密钥有效: {valid}')
        logger.info(f'余额信息: {balance}, 类型: {type(balance)}')
        
        if valid and isinstance(balance, (int, float)) or (isinstance(balance, str) and balance.replace('.', '', 1).isdigit()):
            balance_float = float(balance) if isinstance(balance, str) else balance
            logger.info(f'转换后余额: {balance_float}, 类型: {type(balance_float)}')
    except Exception as e:
        logger.error(f"执行过程中发生错误: {str(e)}")
        logger.error(traceback.format_exc())
        sys.exit(1)

if __name__ == "__main__":
    try:
        print("开始执行测试脚本...")
        asyncio.run(main())
        print("测试脚本执行完成")
    except Exception as e:
        print(f"主程序异常: {e}")
        print(traceback.format_exc())
        sys.exit(1)