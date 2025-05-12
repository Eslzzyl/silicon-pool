import asyncio
import aiohttp
import json
import sys
import traceback
import logging

# 设置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def test_key(key=None):
    """测试API密钥的有效性和余额
    
    返回:
    - 有效密钥且余额 > 0: 应该启用
    - 有效密钥但余额 ≤ 0: 应该启用
    - 无效密钥: 应该禁用
    - 网络错误: 应该保持原状态
    """
    # 如果没有提供密钥，使用默认测试密钥
    if key is None:
        key = 'sk-luthegyrkenqqorbiiewzavuuztxgbebtmpphxyajbbpytfb'
    
    print(f"\n测试密钥: {key[:8]}***")
    headers = {"Authorization": f"Bearer {key}"}
    
    try:
        print("开始请求API验证密钥...")
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.siliconflow.cn/v1/user/info", headers=headers, timeout=30) as r:
                print(f"状态码: {r.status}")
                
                if r.status == 200:
                    # 密钥有效
                    try:
                        data = await r.json()
                        print("API返回数据:")
                        print(json.dumps(data, ensure_ascii=False, indent=2))
                        
                        if 'data' in data and 'totalBalance' in data['data']:
                            balance = data['data']['totalBalance']
                            print(f"余额: {balance}, 类型: {type(balance).__name__}")
                            
                            # 处理不同类型的余额
                            if isinstance(balance, (int, float)):
                                balance_float = float(balance)
                                if balance_float > 0:
                                    print(f"✅ 有效密钥，有余额: {balance_float} - 应该启用")
                                else:
                                    print(f"✅ 有效密钥，余额用尽: {balance_float} - 应该启用")
                            elif isinstance(balance, str):
                                try:
                                    balance_float = float(balance)
                                    if balance_float > 0:
                                        print(f"✅ 有效密钥，有余额: {balance_float} - 应该启用")
                                    else:
                                        print(f"✅ 有效密钥，余额用尽: {balance_float} - 应该启用")
                                except ValueError:
                                    print(f"✅ 有效密钥，余额无法解析: {balance} - 应该启用并设置余额为0")
                            else:
                                print(f"✅ 有效密钥，余额类型未知: {type(balance).__name__} - 应该启用并设置余额为0")
                        else:
                            print("✅ 有效密钥，但无余额信息 - 应该启用并设置余额为0")
                    except Exception as e:
                        print(f"解析API响应失败: {str(e)}")
                        print("✅ 有效密钥，但解析响应失败 - 应该启用并设置余额为0")
                elif r.status in [401, 403]:
                    # 密钥无效
                    try:
                        error_data = await r.json()
                        error_message = error_data.get("message", "")
                        print(f"❌ 无效密钥 - 应该禁用: {error_message}")
                    except Exception as e:
                        print(f"❌ 无效密钥 - 应该禁用: HTTP {r.status}")
                else:
                    # 网络错误或其他HTTP错误
                    try:
                        error_data = await r.json()
                        error_message = error_data.get("message", "")
                        print(f"⚠️ 网络错误 - 应该保持原状态: {error_message} (HTTP {r.status})")
                    except Exception as e:
                        print(f"⚠️ 网络错误 - 应该保持原状态: HTTP {r.status}")
    except asyncio.TimeoutError:
        print("⚠️ 网络错误(超时) - 应该保持原状态")
    except Exception as e:
        print(f"⚠️ 网络错误 - 应该保持原状态: {str(e)}")

async def main():
    # 测试有效密钥（假设有余额）
    print("\n=== 测试1: 有效密钥（有余额）===")
    await test_key()
    
    # 测试无效密钥
    print("\n=== 测试2: 无效密钥 ===")
    await test_key('sk-invalid12345')

if __name__ == '__main__':
    try:
        print("开始执行测试脚本...")
        asyncio.run(main())
        print("\n测试脚本执行完成")
        print("\n总结: 密钥处理规则")
        print("1. 有效密钥且余额 > 0: 启用 (有余额的key)")
        print("2. 有效密钥但余额 ≤ 0: 启用 (余额用尽的key)")
        print("3. 无效密钥(无法连通): 禁用")
        print("4. 网络错误: 保持原状态")
    except Exception as e:
        print(f"主程序异常: {str(e)}")
        print(traceback.format_exc())
        sys.exit(1)