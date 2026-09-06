# 语音约碰面地点

同一座城市内，根据两个人的口述地点推荐中间附近的店铺。当前进度：骨架、前端录音、后端上传、语音识别、地址提取。

真实 `POST /asr` 需要 `backend/.env` 中的 `BAILIAN_API_KEY`，并会产生百炼调用费用。真实 `POST /extract` 需要 `DEEPSEEK_API_KEY`，并会产生 DeepSeek 调用费用。Mock 测试不调用真实服务。

## 环境

- Python 3.13
- Node.js 22.12 及以上的 22.x
- 后端 `http://localhost:8003`
- 前端 `http://localhost:5175`

真实的 `BAILIAN_API_KEY`、`DEEPSEEK_API_KEY`、`AMAP_API_KEY` 填入 `backend/.env`。本轮健康检查不读取这些密钥，未填写也能通过。

## 启动后端

必须在 `backend` 目录用 Python 3.13 创建并激活虚拟环境，不要把依赖装进 conda `base`。

```bash
cd backend
python3.13 -m venv .venv
source .venv/bin/activate
python -c "import sys; print(sys.executable, sys.version)"
pip install -r requirements.txt
cp .env.example .env
uvicorn main:app --host 127.0.0.1 --port 8003 --reload
```

`python -c` 打印的路径应包含 `backend/.venv`，版本应是 3.13。若路径是 `miniconda3` 或版本不是 3.13，说明虚拟环境未激活，不要继续。

若 `pip install` 出现 `CERTIFICATE_VERIFY_FAILED`，说明 python.org 的证书还不可用。不要换目录，在已激活的 `.venv` 里执行：

```bash
pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org -r requirements.txt
python -m pytest
```

## 启动前端

另开一个终端：

```bash
cd frontend
npm install
npm run dev
```

浏览器打开 `http://localhost:5175`。

## 本轮验证

- 浏览器打开 `http://localhost:8003/health`，应返回 HTTP 200，JSON 含 `request_id` 与 `data.status` 为 `"ok"`。
- 浏览器打开 `http://localhost:8003/docs`，应看到 FastAPI 文档，可调试 `GET /health`。
- 浏览器打开 `http://localhost:5175`，应看到标题为「语音约碰面地点」的基础页面。

可选本地接口测试。请先确认 `backend/.venv` 已存在；不要用系统或 conda 里的 `pytest`。

```bash
cd backend
source .venv/bin/activate
python -m pytest
```

模拟测试通过不能证明真实 ASR、DeepSeek、高德、TTS 已跑通。那些验收在接入对应接口并由你确认真实调用后再做。

真实 `POST /extract` 在 `http://localhost:8003/docs` 调试。请求体为 `text` 与 `city`；成功时 `data` 只含五个业务字段。需要 `DEEPSEEK_API_KEY`，会产生 DeepSeek 调用费用。

真实 `POST /upload` 需要本机 `ffprobe`（FFmpeg），只做探测、不转码。macOS 安装：

```bash
brew install ffmpeg
ffprobe -version
```

## 尚未实现

搜店、推荐语、语音播报，以及前端接入上传/识别/提取接口均未实现。
