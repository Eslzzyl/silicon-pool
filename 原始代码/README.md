# 简介

<details>
<summary>展开查看截图</summary>

<img src="https://r2.eslzzyl.eu.org/silicon-pool/login.jpeg" alt="登录页" width="50%" />
<img src="https://r2.eslzzyl.eu.org/silicon-pool/index.jpeg" alt="主页" width="50%" />
<img src="https://r2.eslzzyl.eu.org/silicon-pool/models.jpeg" alt="模型页" width="50%" />
<img src="https://r2.eslzzyl.eu.org/silicon-pool/keys.jpeg" alt="Key 管理页" width="50%" />
<img src="https://r2.eslzzyl.eu.org/silicon-pool/logs.jpeg" alt="日志页" width="50%" />
<img src="https://r2.eslzzyl.eu.org/silicon-pool/stats.jpeg" alt="统计页" width="50%" />
<img src="https://r2.eslzzyl.eu.org/silicon-pool/settings.jpeg" alt="设置页" width="50%" />
</details>

***

一个用于管理硅基流动 API Key 的本地工具。支持以下功能：
- 登录验证
- API Key 的批量导入，自动过滤无效的 Key。余额用尽的 Key 也会接受，可用于和专门用于免费模型的 API token 配合，并发调用免费模型。Key 的导入可以正常处理带有括号余额后缀的 Key、用逗号分割的 Key 等，可无脑复制粘贴。
- API Key 的批量导出（导出为 txt），支持按余额或字典顺序排序，支持逗号分割。
- 对 `/chat/completions`、`/embeddings`、`/completions`（通常用于 FIM 任务，如代码自动补全）、`/images/generations`、`/rerank` 和 `/models` 接口的转发。其中 `/chat/completions` 和 `/completions` 支持流式响应和非流式响应
- 转发时有多个 Key 选择策略：随机、余额最多优先、余额最少优先、添加时间最旧优先、添加时间最新优先、使用次数最少优先、使用次数最多优先。
- 一个简单的 Web UI 用于集中管理 Key（见上方图）
- Key 的批量余额刷新，余额用尽的 Key 将被保留并用于免费模型的调用。
- 手动禁用或启用某些 Key
- 模型调用日志记录
- 利用 Chart.js 绘制的调用统计图表
- 自定义 API token 检查，仅当调用接口的客户端提供指定的 token 时才转发。

# 如何使用

## 运行编译好的程序

到 Release 中下载编译好的程序后解压运行 `main.exe` 即可。随后从下面的第 3 步开始。

## 直接运行源码

1. 安装 `uv`: https://docs.astral.sh/uv/getting-started/installation
2. 在项目根目录执行 `uv run main.py`
    > 我也写了一份 `requirements.txt`，因此也可以使用 `pip` 来安装依赖：`pip install -r requirements.txt`
3. 访问 http://127.0.0.1:7898 来查看 Web UI 并导入你的 Key。管理面板的默认用户名和密码都是 `admin`。
4. 在你的应用程序中设置 OpenAI `BASE_URL` 为 `http://127.0.0.1:7898/v1`，并设置 `API_KEY`：
    - 如果没有启用 API token（即留空），则 `API_KEY` 可以是任何值，留空也可以。此程序会直接转发请求，不会检查 `API_KEY`。
    - 如果启用了 API token，那么 `API_KEY` 必须是在 Web UI 中设置的 API token 值。
    - 此外，还有一个专门用于调用免费模型的 API token，设置后可用此 token 并发调用免费模型。
5. 正常使用即可。

# 注意事项

- 如果需要高并发，建议将 Key 选择策略设置为随机，这样并发的多个请求会被分配到多个随机的 Key。由于每次转发都需要读取和写入数据库，目前本工具的并发性能有限。未来我将着手处理此问题。
- 不要泄露生成的 `pool.db` 文件，因为其中包含你导入的所有 API Key。
- 也不建议泄露生成的 `config.json` 文件，因为其中可能包含你自定义的 API token。
- 当 Key 比较多时，短时间多次刷新余额可能导致 Key 的丢失。目前尚无解决方案。尽量避免频繁刷新余额。
