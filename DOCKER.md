# Silicon Pool Docker 部署指南

## 使用 Docker 构建和运行

### 方法一：使用 Docker 命令

1. 构建 Docker 镜像
   ```bash
   docker build -t silicon-pool .
   ```

2. 运行 Docker 容器
   ```bash
   docker run -d -p 7898:7898 silicon-pool
   ```

### 方法二：使用 Docker Compose

1. 使用以下命令构建并启动服务
   ```bash
   docker-compose up -d
   ```

2. 停止服务
   ```bash
   docker-compose down
   ```

## 访问应用

构建并运行容器后，可以通过以下地址访问应用：

- Web 界面：http://localhost:7898
- API 接口：http://localhost:7898/v1

## 注意事项

- 默认的用户名和密码都是 `admin`
- 容器中的应用数据存储在容器内部，如需持久化存储，可以修改 docker-compose.yml 添加数据卷映射
- 如果需要修改端口，请同时更新 docker-compose.yml 中的端口映射和 Dockerfile 中的 EXPOSE 指令