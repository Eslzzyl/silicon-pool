/**
 * 显示操作消息
 * @param {string} text 消息文本
 * @param {string} type 消息类型 ('success' 或 'error')
 */
async function showMessage(text, type = 'success') {
    const messageEl = document.getElementById('message');
    messageEl.textContent = text;
    messageEl.className = type;
    messageEl.style.display = 'block';
    setTimeout(() => messageEl.style.display = 'none', 3000);
}

/**
 * 获取系统统计数据
 */
async function fetchStats() {
    const response = await fetch("/stats");
    const data = await response.json();
    document.getElementById("keyCount").textContent = data.key_count;
    document.getElementById("totalBalance").textContent = data.total_balance;
}

/**
 * 刷新所有密钥
 */
async function refreshKeys() {
    showMessage("正在刷新，请稍候...", "success");
    const response = await fetch("/refresh", { method: "POST" });
    const data = await response.json();
    showMessage(data.message, "success");
    fetchStats();
}

/**
 * 遮蔽API密钥中部分字符
 * @param {string} key API密钥
 * @returns {string} 遮蔽后的密钥
 */
function maskKey(key) {
    // 显示密钥的前8个字符和后4个字符，中间用***替代
    if (key.length > 12) {
        return `${key.substring(0, 8)}***${key.substring(key.length - 4)}`;
    }
    return key;
}

/**
 * 创建改进的分页UI
 * @param {number} currentPage 当前页码
 * @param {number} totalPages 总页数
 * @param {function} callback 页码点击回调函数
 */
function renderPagination(currentPage, totalPages, callback) {
    const paginationDiv = document.getElementById("pagination");
    paginationDiv.innerHTML = "";

    if (totalPages <= 1) return;

    // 确保当前页码合法
    currentPage = Math.max(1, Math.min(currentPage, totalPages));

    // 添加第一页按钮
    addPageButton(paginationDiv, 1, currentPage, callback);

    // 显示省略号和中间的页码
    if (totalPages > 7) {
        let startPage = Math.max(2, currentPage - 2);
        let endPage = Math.min(totalPages - 1, currentPage + 2);

        if (currentPage - 2 > 2) {
            paginationDiv.appendChild(createEllipsis());
        }

        for (let i = startPage; i <= endPage; i++) {
            addPageButton(paginationDiv, i, currentPage, callback);
        }

        if (currentPage + 2 < totalPages - 1) {
            paginationDiv.appendChild(createEllipsis());
        }
    } else {
        // 如果页数较少，则全部显示
        for (let i = 2; i < totalPages; i++) {
            addPageButton(paginationDiv, i, currentPage, callback);
        }
    }

    // 添加最后一页按钮(如果总页数大于1)
    if (totalPages > 1) {
        addPageButton(paginationDiv, totalPages, currentPage, callback);
    }

    // 添加页面跳转输入框
    const jumpDiv = document.createElement('div');
    jumpDiv.className = 'page-jump';
    jumpDiv.innerHTML = `
        <span>跳至</span>
        <input type="number" min="1" max="${totalPages}" value="${currentPage}" id="pageJumpInput">
        <span>页</span>
        <button class="secondary">确定</button>
    `;
    paginationDiv.appendChild(jumpDiv);

    // 为跳转按钮添加事件监听
    jumpDiv.querySelector('button').addEventListener('click', function () {
        const pageInput = document.getElementById('pageJumpInput');
        let targetPage = parseInt(pageInput.value);
        if (isNaN(targetPage)) targetPage = currentPage;
        targetPage = Math.max(1, Math.min(targetPage, totalPages));
        if (targetPage !== currentPage) {
            callback(targetPage);
        }
    });

    // 为输入框添加回车事件
    jumpDiv.querySelector('input').addEventListener('keypress', function (e) {
        if (e.key === 'Enter') {
            jumpDiv.querySelector('button').click();
        }
    });
}

/**
 * 创建页码按钮
 * @param {HTMLElement} container 容器元素
 * @param {number} page 页码
 * @param {number} currentPage 当前页码
 * @param {function} callback 点击回调函数
 */
function addPageButton(container, page, currentPage, callback) {
    const btn = document.createElement("button");
    btn.textContent = page;
    btn.className = currentPage === page ? "current" : "secondary";
    btn.disabled = currentPage === page;
    if (currentPage !== page) {
        btn.onclick = () => callback(page);
    }
    container.appendChild(btn);
}

/**
 * 创建省略号元素
 * @returns {HTMLSpanElement} 省略号元素
 */
function createEllipsis() {
    const span = document.createElement('span');
    span.className = 'ellipsis';
    span.textContent = '...';
    return span;
}

/**
 * 复制文本到剪贴板
 * @param {string} text 要复制的文本
 * @param {HTMLElement} buttonElement 触发复制的按钮元素
 */
async function copyToClipboard(text, buttonElement) {
    try {
        await navigator.clipboard.writeText(text);

        // 更改按钮样式以显示成功
        const originalText = buttonElement.textContent;
        const originalClass = buttonElement.className;

        buttonElement.textContent = '✓';
        buttonElement.classList.add('copy-success');

        // 1.5秒后恢复按钮样式
        setTimeout(() => {
            buttonElement.textContent = originalText;
            buttonElement.className = originalClass;
        }, 1500);
    } catch (err) {
        showMessage('复制失败，请手动复制', 'error');
    }
}
