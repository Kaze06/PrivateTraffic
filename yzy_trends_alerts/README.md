# PrivateTraffic 历史趋势、告警与集成

宋子安负责模块。C11 后端复用姚子严的 `PTMeasurement` 和陈嘉俊的 `PTStore`。浏览器页面用原生 HTML、CSS、JavaScript 绘图和显示新告警，无外部前端依赖。

## 构建与检测

```powershell
C:\mingw64\bin\cmake.exe -S E:\SW_Project -B E:\SW_Project\build -G "MinGW Makefiles"
C:\mingw64\bin\cmake.exe --build E:\SW_Project\build
C:\mingw64\bin\ctest.exe --test-dir E:\SW_Project\build --output-on-failure
```

入口文件：`include/trend_alert.h`、`src/trend_alert.c`、`src/main.c`、`src/pipeline.c`、`web/dashboard.html`。

## 运行顺序

先用 `pt_locations.exe` 登记一个团队已确认的 Y4M 回放路径，许可状态填 `approved`，并关注该地点。真实来源路径和告警阈值由团队提供，不在代码中预设。下面的占位符要替换成实际值：

```powershell
E:\SW_Project\build\yzy_trends_alerts\jiajun_backend\pt_locations.exe E:\SW_Project\local_data add "LOCATION_ID" "LOCATION_NAME" "SOURCE_PATH.y4m" replay approved
E:\SW_Project\build\yzy_trends_alerts\jiajun_backend\pt_locations.exe E:\SW_Project\local_data follow "LOCATION_ID"
E:\SW_Project\build\yzy_trends_alerts\pt_zian.exe E:\SW_Project\local_data rule put "RULE_ID" "LOCATION_ID" "THRESHOLD_VPM" 08:00 20:00 1
E:\SW_Project\build\yzy_trends_alerts\pt_pipeline.exe E:\SW_Project\local_data "LOCATION_ID" "LINE_Y" "T1_VPM" "T2_VPM"
E:\SW_Project\build\yzy_trends_alerts\pt_zian.exe E:\SW_Project\local_data export "LOCATION_ID"
```

最后打开 `E:\SW_Project\build\yzy_trends_alerts\web\dashboard.html`。`pt_zian ... watch "LOCATION_ID"` 可每 5 秒检测新测量并刷新页面数据；保持该进程运行，浏览器会每 5 秒读取一次。新告警在已打开的页面顶部显示。页面第一次打开时不弹出历史告警。

`pt_zian rule put` 同时用于新建、编辑、启用和停用规则，最后一位 `1` 为启用，`0` 为停用。`rule delete "RULE_ID"` 删除规则。`rule list` 查看规则。`compare "LOCATION_ID" <起点毫秒> <终点毫秒> <起点毫秒> <终点毫秒>` 输出两段统计。浏览器页面可直接修改两个时间段并比较曲线与数值。

## 规则语义

- 流量 `>=` 用户给定阈值才触发。只使用 `validity=valid` 的测量；不足窗口或来源不可用不触发。
- 时间窗按运行机器的本地时间，起点包含、终点不包含；支持跨午夜。起止相同表示全天。
- 规则必须绑定已关注地点。取消关注后不再产生新告警。
- 每个规则和测量 ID 最多产生一条事件；事件保存在 `alert_events.psv`，规则变更保存在 `alert_rules.psv`。
- 趋势统计只累计有效记录；不可用记录单独计数。空时间段显示缺失值，不把缺失流量当作 0。

回放的测量时间以运行开始时刻为基准，加视频内相对时间；它不是摄像头原始拍摄时间。浏览器页面由本地文件供数，不包含远程推送或多用户管理。真实道路识别准确率仍取决于测量模块的后续验证。
