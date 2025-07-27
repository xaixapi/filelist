# Filelist - 企业文件网盘服务

[English](#filelist---enterprise-file-sharing-service) | [中文](#filelist---企业文件网盘服务-1)

A lightweight, self-hostable file sharing and management platform. The first registered user becomes the administrator with full privileges.

### ✨ Features

*   **Admin Control**: First user gets full control over public and private files.
*   **File Management**: Upload, download, delete, preview, and share.
*   **Secure Sharing**: Create expiring short links and direct links.
*   **Easy Deployment**: Containerized for Docker and Kubernetes.

---

## 🚀 Quick Start (Docker Compose)

The simplest way to get started.

```bash
# 1. Clone the repository
git clone <your-repository-url>
cd filelist

# 2. Run the service
docker-compose up -d
```

## 📦 Kubernetes Deployment

For scalable, production environments.

1.  **Build & Push Image**

    First, update the image name in `deployment.yml` to your own registry.
    ```bash
    # Example: image: your-registry/filelist:latest
    
    docker build -t your-registry/filelist:latest .
    docker push your-registry/filelist:latest
    ```

2.  **Deploy to Cluster**
    ```bash
    kubectl apply -f deployment.yml
    ```

---
---

# Filelist - 企业文件网盘服务

一个轻量级的、可自托管的文件分享和管理平台。首位注册用户即为管理员，拥有最高权限。

### ✨ 核心功能

*   **管理员权限**: 首位注册用户拥有对公共和私有文件的完整控制权。
*   **文件管理**: 支持上传、下载、删除、预览和分享。
*   **安全分享**: 可创建带有效期的短链接和文件直链。
*   **便捷部署**: 已容器化，支持 Docker 和 Kubernetes。

---

## 🚀 快速开始 (Docker Compose)

最简单的启动方式。

```bash
# 1. 克隆仓库
git clone <your-repository-url>
cd filelist

# 2. 启动服务
docker-compose up -d
```

## 📦 Kubernetes 部署

适用于可扩展的生产环境。

1.  **构建并推送镜像**

    首先，在 `deployment.yml` 文件中修改镜像地址为你的镜像仓库。
    ```bash
    # 示例: image: your-registry/filelist:latest
    
    docker build -t your-registry/filelist:latest .
    docker push your-registry/filelist:latest
    ```

2.  **部署到集群**
    ```bash
    kubectl apply -f deployment.yml
    ```
