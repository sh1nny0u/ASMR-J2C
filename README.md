# ASMR-J2C

将日语（或其他语言）原音频中中文 LRC 字幕标记的说话片段替换为中文 TTS 语音的本地工具。

## 功能

- 上传原音频、中文 LRC 字幕和目标音色参考音频。
- 按 LRC 时间逐句调用本地 IndexTTS2 生成中文语音。
- 支持IndexTTS2情绪控制、参数调整。
- 替换字幕有效时间段内的原声，字幕外的环境声、停顿和背景音保持原样。
- 对生成语音做响度平衡和时长适配，输出完整 WAV/MP3 音频。
- 支持“双语音声”，完整原音轨与中文 TTS 在字幕时段同步混音，原声与中文语音均可由用户自定义 `0%` 到 `200%` 音量。不自动压低任一音轨；只有峰值超过可编码范围时才启用保持相对比例的主限幅保护，并给出提示。

## 截图
![ASMR-J2C 运行界面截图](./img/img.png)

## 依赖

- **Python 3.11+**
- **ffmpeg**（需加入系统 PATH）
- **IndexTTS2** 服务（本项目需要配合 [IndexTTS2](https://github.com/index-tts/index-tts) 服务使用。）

## 快速开始

1. 克隆或下载本仓库。
2. 双击 `setup-index.bat` 安装 IndexTTS2 服务（基于官方[IndexTTS2](https://github.com/index-tts/index-tts),首次运行会自动创建虚拟环境安装依赖，并下载所需模型）。
3. 双击 `setup-j2c.bat` 安装本项目。
4. 双击 `start.bat` 启动本项目，自动跳出IndexTTS2以及J2C前端页面，后续启动只需点击 `start.bat` 。

> 为确保端口释放，建议点击 `stop.bat` 结束运行本项目。


## 项目结构

```
ASMR-J2C/
│
├── app/                            # FastAPI 后端主程序目录
│   ├── audio.py                    # 音频处理
│   ├── config.py                   # 配置管理
│   ├── jobs.py                     # 任务队列管理与执行
│   ├── lrc.py                      # LRC 字幕解析和验证
│   ├── main.py                     # FastAPI 应用入口
│   ├── routes.py                   # API 路由
│   ├── tts.py                      # IndexTTS2 客户端封装
│   └── __init__.py                 # 包标识
│
├── indexTTS2/                      # IndexTTS2 语音合成引擎
│
├── static/                         # 前端静态资源
│   ├── app.js                      # 前端业务逻辑（任务提交、状态轮询）
│   ├── index.html                  # 主界面 HTML
│   └── styles.css                  # 样式表
│
├── setup-index.bat                 # 一键安装 IndexTTS2 环境的批处理
├── setup-index.ps1                 # IndexTTS2 环境安装脚本
├── setup-j2c.bat                   # 一键安装 ASMR-J2C 主项目依赖的批处理
├── setup-j2c.ps1                   # 主项目环境安装脚本
├── start.bat                       # 启动服务的批处理
├── start-app.ps1                   # 启动主逻辑
└── stop.bat                        # 强制停止服务的批处理
```

## 注意事项

- 生成任务会占用较多内存和 CPU，建议在有独立显卡的机器上运行。
- 首次启动时会自动下载 Python 依赖、以及IndexTTS2所需模型，请耐心等待。

## 开源协议

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
