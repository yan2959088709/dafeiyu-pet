# 大肥鱼桌宠 🐋

DeepSeek V4 Pro 二创形象「鲸鱼娘·大肥鱼」的透明桌面宠物。

基于三视图素材（正面 / 侧面 / 背面），用 Python + PySide6 实现，无边框透明置顶窗口。

## 功能

- **三视图行走**：左右走用侧面（自动镜像）、向上走用背面、向下走用正面

- **三种模式**：自由散步 / 跟随鼠标 / 原地待着（右键菜单切换）

- **互动**：
  - 左键按住：拖拽（会侧身朝向拖动方向，松手会说话）
  - 单击：蹦跳 + 回嘴（互动台词）+ 弹出 🗨️ 聊天面板
  - 双击：喂食面板（小鱼干 / 蛋糕 / 棒棒糖 / 团子 / 钻石）
  - 右键：完整菜单（模式 / 大小 / 喂食 / 说句话 / 显示/隐藏 / 鼠标穿透 / 置顶 / 开机自启 / 退出；托盘右键是同款菜单，穿透后可从托盘解除）

- **台词系统**：日常随机台词 + 互动回嘴 + 思维链心声（灰色斜体括号气泡，小概率冒出），全部取材自社区 DS 梗

- **细节**：呼吸 / 摇摆 / 蹦跳 / 进食动画、转向交叉淡化、加减速惯性、散步自动休息、说话冷却

- 托盘图标、窗口置顶、鼠标穿透、开机自启、配置记忆（config.json）；穿透 / 置顶状态重启后自动恢复

- ##### AI 对话（新增）

  - 左键单击弹出 🗨️ 图标，点击后弹出聊天输入框
  - 调用 DeepSeek API（`deepseek-chat` 模型），每句话不超过 25 字，风格贱兮兮但可爱
  - 对话历史保留最近 40 条（自动记忆上下文）
  - API Key 通过右键菜单「设置 Key」输入，保存到 `config.json`
  - 聊天期间鱼会暂停移动，但呼吸/摇摆/小动作照常

  ##### 天气查询（新增）

  - 右键菜单「查看天气」→ 调用 `wttr.in` 获取当前城市天气
  - 城市默认从 `config.json` 读取，可手动修改配置文件中的 `"city"` 字段
  - 鱼会气泡播报：「汕头今天 26°，天气多云」

  ##### 系统状态监控（新增）

  - **CPU**：超过 90% 时冒泡提醒
  - **内存**：超过 95% 时冒泡提醒
  - **显卡（NVIDIA）**：温度超过 80°C 时冒泡提醒（`pynvml` 读取）
  - 检测间隔 10 秒，不频繁打扰

## 运行

需要 **Python 3.11+**

```bash
pip install -r requirements.txt
# 或
pip install PySide6
```

然后双击 `启动桌宠.bat`，或：

```bash
python 桌宠.py
```

## 打包成独立 exe（可分享给朋友）

```bash
pip install pyinstaller
pyinstaller --noconfirm --onefile --windowed --name 大肥鱼桌宠 --add-data "sprites;sprites" --icon icon.ico 桌宠.py
```

产物在 `dist/大肥鱼桌宠.exe`，对方双击即用，无需安装 Python。
（杀毒软件可能对 PyInstaller 产物误报，加信任即可。）

## 更换形象

把新的三视图（白底）放到程序目录：

1. 正面.png / 侧面.png / 背面.png（原图）
2. 运行 `python tools/legacy/preprocess.py` —— 白底抠图 + 统一高度（脚本内 SRC/OUT 写死旧开发机路径，先改成你的路径）
3. 运行 `python tools/legacy/preprocess2.py` —— 边缘去污 + 预乘 alpha 缩放出各尺寸精灵（同上）

## 文件说明

| 文件 | 说明 |
|------|------|
| 桌宠.py | 主程序：窗口装配、Qt 事件胶水、跨域调度（tick 编排） |
| pet/ | 功能模块（下表）；每模块一个 Mixin 或一组函数，主程序不感知其内部实现 |
| tools/smoke_check.py | 冒烟检查与行为指纹（22 项，双平台可跑） |
| tools/smoke_baseline.json | 动画/渲染行为指纹基线（重构前后逐帧对比用） |
| tools/build_exe.py | 打包 exe 与便携 zip |
| tools/import_dsh_skin.py | dsh-pet 素材导入（webm → 动画 WebP + manifest） |
| tools/legacy/ | 已归档的一次性脚本（仍依赖旧开发机路径，仅作参考，不可直接运行） |
| docs/ | 开发资料（移植功能对照清单） |
| sprites/ | 精灵图（三视图 各尺寸 + 图标 + dsh-pet 动作素材与预览图） |
| 启动桌宠.bat | 启动脚本（自动选择 venv 或系统 Python） |
| requirements.txt | 依赖 |
| 桌宠.spec | PyInstaller 打包配置（含新依赖收集） |

### pet/ 模块职责

| 模块 | 职责 |
|------|------|
| storage.py | 运行期路径表 `P` + JSON 落盘（损坏文件先备份再回落） |
| credentials.py | API Key / Platform Token 的 DPAPI 加密存储（绑定当前 Windows 用户） |
| pricing.py | 峰谷定价表、时段判定、单轮成本换算 |
| stickers.py | 表情标签映射、首次解压、标记解析 |
| ledger.py | 用量台账、跨天归档、Token 统计窗 |
| memory.py | 事实抽取、去重、同主题极性冲突消解 |
| sessions.py | 多会话数据层与旧版历史迁移 |
| chat.py | 系统提示词、SSE 流式调用、回复落库 |
| motion.py | 逐帧调度、移动/动画状态机、气泡排版、paintEvent |
| gadget.py | DSH 挂件气泡绘制与贴壁 |
| balance.py | 余额查询、天气、系统监控、本地技能分流 |
| menus.py | 右键菜单构建、勾选同步、窗口属性操作 |
| facts_ui.py | 记忆管理对话框 |
| sound.py | Q弹音效合成与播放 |
| inputs.py | 通用输入弹窗（消除默认按钮误触） |

### 修改约定

- 新功能优先放进 `pet/` 下对应模块，并实现为 Mixin 或纯函数；主程序只负责装配。
- `pet/` 内禁止 `from 桌宠 import ...`（会循环导入）；共享常量在子模块定义，主程序反向导入。
- 改动涉及 `tick` / `paintEvent` / 动画后，务必先跑 `tools/smoke_check.py`；若为有意的行为变更，再加 `--update-baseline` 重生指纹。

## 贡献与致谢

- **AI 对话 / 天气查询 / 系统监控 / PyInstaller 打包配置**：由 [Cpanoe](https://github.com/Cpanoe) 通过 [PR#3](https://github.com/1190fasheqi/dafeiyu-pet/pull/3) 贡献（DeepSeek API 聊天、wttr.in 天气、CPU/内存/GPU 监控、桌宠.spec）。
- 合并时维护方修复：
  - 线程安全：DeepSeek 回复由后台线程直接调用 Qt 界面改为经队列转发主线程（`_say_queue`）
  - 配置保护：`config.json` 保持不入仓库（本地配置含 API Key，防止泄露）
- 桌面宠物朝向修复由 [B-A-A-GE](https://github.com/B-A-A-GE) 通过 [PR#1](https://github.com/1190fasheqi/dafeiyu-pet/pull/1) 提交（未合并，当前为维护方修复版）。

## 台词梗来源

台词均取自 DeepSeek / 鲸鱼娘 / 大肥鱼社区梗（D指导去吃饭、吃白饭、梁文锋会议三连、"才不是大肥鱼"、思维链心声等），感谢社区整活。

## 协议

MIT
