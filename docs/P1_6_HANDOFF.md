# P1-6 回归与交接记录

日期：2026-11-16。负责人：姚子严。分支：`feature/python-measurement-y4m`。

本轮完成 Measurement 软件回归和交接资料整理。新增 6 项接口验收用例，运行实际 measurement、camera 和 alert 路由，使用独立内存数据库和合成输入。团队现场彩排、真实直播时延和 main 合并尚未完成。

## 已验证内容

| 检查 | 结果与入口 |
|---|---|
| 当前 C8351 配置 | T1=33、T2=142；仍为临时配置，畅通标签待团队复核 |
| 分类边界、保存、读取 | 32→clear，33→normal，141→normal，142→congested；POST、数据库、状态及历史接口一致 |
| 回放时间 | 返回原录制时间和窗口来源时间；保持 replay_not_live，不更新实时 last_valid_time |
| 回放告警 | 建立有效页面会话和规则后，141→142→143 仍无告警；重复报告返回 409，未重复入库 |
| 无效窗口 | 当前车辆数为空，状态等级 unable_to_determine，原因 inference_failed；有效历史保留 |
| 既有实时行为 | 原有集成测试覆盖首次基线、跨阈值、持续高位、重新武装、异常恢复和会话关闭 |

新用例：[test_p1_6_handoff.py](../tests/test_p1_6_handoff.py)。既有联调：[test_data_alert_integration.py](../tests/test_data_alert_integration.py)、[test_alert_sessions.py](../tests/test_alert_sessions.py)、[test_data_validity.py](../tests/test_data_validity.py)。本次未改动业务接口或前端。

## 复现命令

在本分支仓库根目录运行，使用已安装仓库及 `python_code/requirements.txt` 依赖的 Python 环境：

```powershell
$env:PYTHONPATH='python_code'
python -B -m unittest discover -s python_code/tests -v
python -B -m pytest -q
python -B -m pytest -q tests python_code/tests
python -B -m pytest -v tests/test_p1_6_handoff.py tests/test_data_alert_integration.py tests/test_alert_sessions.py
```

本轮结果：unittest 29 项通过；默认 pytest 107 项通过；显式覆盖两个测试目录共 147 项通过，无失败、无跳过，13 条依赖弃用警告。默认 pytest 受 `pytest.ini` 的 `testpaths=tests` 限制，不能代替双目录命令；上述测试数量有重叠，不能相加。测试环境为 Windows，解释器 `D:\python_code\.venv\Scripts\python.exe`。

## 交接与演示顺序

1. 陈嘉俊检查本分支提交及 `pilot_cameras.json`。用新用例演示测量提交、数据库保存、状态与历史查询。新数据库初始化读取临时阈值；已有数据库按 [更新脚本](../python_code/apply_camera_calibration.py) 的 `--database`、`--pilot-config`、`--camera-code` 参数显式更新。先备份已有数据库。
2. 宋子安核对回放标志、来源时间、无效状态，以及页面会话告警。上述接口用例可先运行；网页现场演示使用已有 `python tools/demo_server.py`，访问 `http://127.0.0.1:8765/demo`，打开控制台链接的主页并选择 DEMO 摄像头、启用阈值 25。依次发送 low、high、high、low、high，预期两次告警；再发送 replay、stale、offline，不新增实时告警。该工具使用合成输入及独立 `demo-session.db`，不要用它启动正式直播。本轮没有重新执行网页演示。
3. 团队在真实录像上复核计数口径与畅通候选标签，再记录实际彩排日期、参与人、页面结果和问题。姚子严负责窗口、计数和时间说明；陈嘉俊确认保存与查询；宋子安确认页面与告警。此处为待执行安排。
4. 协调人合并候选分支后重新运行双目录完整回归，独立审查合并差异。当前功能分支通过不代表其他分支合并后也通过。

## 姚子严的代码提交

| 阶段 | 提交与作用 |
|---|---|
| P0-1 | [fb09864](https://github.com/Kaze06/PrivateTraffic/commit/fb09864)、[3e8af34](https://github.com/Kaze06/PrivateTraffic/commit/3e8af34)：Python Y4M 计数与回放桥接 |
| P0-2 | [af11601](https://github.com/Kaze06/PrivateTraffic/commit/af11601)、[14dbc24](https://github.com/Kaze06/PrivateTraffic/commit/14dbc24)：计数证据整理与 RF-DETR 自动计数 |
| P0-3 | [23745e0](https://github.com/Kaze06/PrivateTraffic/commit/23745e0)：小框先过滤，修复轨迹匹配问题 |
| P0-4 | [0f21f59](https://github.com/Kaze06/PrivateTraffic/commit/0f21f59)：异常来源时间生成无效报告 |
| P1-5 | [c8eb6fa](https://github.com/Kaze06/PrivateTraffic/commit/c8eb6fa)、[b5e52df](https://github.com/Kaze06/PrivateTraffic/commit/b5e52df)：标定核查与 C8351 临时阈值 |
| P1-6 | 本文及接口交接用例；提交号见本文件 Git 历史 |

## 本地证据与待完成项

测试日志、JUnit XML、标定复核结果、人工计数交接表和开发进程报告位于 `D:\软件工程\PrivateTraffic_P1-6_姚子严_验证`。原始计数及模型报告保留在 P0-3、P1-5 验证目录；本轮未重新运行视频推理。新增 Python 文件副本位于 `D:\python_code\p1_6`。录像、帧、数据库及逐窗人工计数表不在本次提交中。

- 已取得用户提供的来源用途及保留说明、两段录像和指定窗口人工数；对应真实回放计数核验已有本地记录。
- 仍需来源方直播观察时间依据，之后才能测量真实来源至显示时延和告警时延。
- C8351 阈值保持临时性质，需复核畅通标签并补不同时间样本。
- 团队彩排、手机/电脑三人使用验证、协调人整合和独立审查待完成。
- 课程评估结束后按已确认约定清理本地评估记录与临时片段；具体清理日期由课程验收安排确定。
