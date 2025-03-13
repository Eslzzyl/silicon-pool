/**
 * 初始化导航栏
 * 在所有带有 id="navbar" 的元素中创建导航栏
 */
document.addEventListener('DOMContentLoaded', function () {
    initNavbar();
    // 添加身份验证检查（非登录页面）
    if (!window.location.pathname.includes('login.html')) {
        checkAuthentication();
    }
});

/**
 * 创建导航栏并插入到页面中
 */
function initNavbar() {
    const navbars = document.querySelectorAll('#navbar');
    if (navbars.length === 0) return;

    // 获取当前页面路径，用于判断当前活动页
    const currentPath = window.location.pathname;

    // 导航项定义
    const navItems = [
        { name: '主页', path: '/', icon: '🏠' },
        { name: '密钥管理', path: '/static/keys.html', icon: '🔑' },
        { name: '调用日志', path: '/static/logs.html', icon: '📝' },
        { name: '统计', path: '/static/stats.html', icon: '📊' },
        { name: '设置', path: '/static/settings.html', icon: '⚙️' },
    ];

    // 创建导航栏HTML
    const navHtml = `
        <div class="navbar">
            <div class="navbar-container">
                <div class="navbar-logo">
                    <a href="/">硅基 Key 池</a>
                </div>
                <div class="navbar-links">
                    ${navItems.map(item => {
                        const isActive = currentPath === item.path ||
                            (item.path !== '/' && currentPath.startsWith(item.path));
                        return `
                            <a href="${item.path}" class="${isActive ? 'active' : ''}">
                                <span class="nav-icon">${item.icon}</span>
                                <span class="nav-text">${item.name}</span>
                            </a>
                        `;
                    }).join('')}
                    <a href="javascript:void(0)" onclick="logout()" class="logout-link">
                        <span class="nav-icon">🚪</span>
                        <span class="nav-text">退出</span>
                    </a>
                </div>
            </div>
        </div>
    `;

    // 插入到页面中
    navbars.forEach(navbar => {
        navbar.innerHTML = navHtml;
    });
}
