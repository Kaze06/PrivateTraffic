# PrivateTraffic measurement baseline

姚子严负责的首日 C 语言测量模块。读取固定镜头的 8-bit Y4M 4:2:0 测试回放；第一帧建立背景，后续帧做亮度背景差分、连通区域检测、质心跟踪、水平线跨越计数。每条轨迹只计一次。以 60 秒窗口输出一行 Measurement JSON。录制长度不足 60 秒时，最后一行标为 `insufficient_window`，不能用于正式交通等级或告警。

## 编译与测试

```powershell
cmake -S E:\SW_Project -B E:\SW_Project\build -G "MinGW Makefiles"
cmake --build E:\SW_Project\build
ctest --test-dir E:\SW_Project\build --output-on-failure
```

## 运行

```powershell
E:\SW_Project\build\zian_trends_alerts\yzy_measurement\pt_demo.exe E:\SW_Project\yzy_measurement\sample\road_demo.y4m road-demo 40 1 3
```

参数为 Y4M 文件、地点 ID、计数线 Y 坐标、可选的道路校准阈值 T1/T2（辆/分钟）。未提供有效阈值时，等级为 `unable_to_determine`。视频宽高和 FPS 从 Y4M 头读取；时间戳单位为毫秒，来自帧序号和 FPS。若有 FFmpeg，可先转换：

```powershell
ffmpeg -i input.mp4 -pix_fmt yuv420p -f yuv4mpegpipe E:\SW_Project\yzy_measurement\sample\road.y4m
```

本机没有 FFmpeg，项目本身不依赖它。`sample/road_demo.y4m` 是合成回放，用于接口验证，不代表真实车辆识别准确率。`pt_demo` 只读取 Y4M；实际摄像头接入时，可由其他模块把帧亮度数据和采集时间传给 `pt_process_frame()`。

## 模块边界与后续工作

- `include/measurement.h` 是后端的共享 C 接口。`PTMeasurement` 字段对应开发计划中的 Measurement，加上窗口起始时间与秒数。
- `pt_process_frame()` 拒绝非递增时间戳和尺寸错误。完整窗口返回 1，未形成完整窗口返回 0，错误返回 -1。
- `pt_measurement_json()` 供后端接收和保存；`source_state` 固定为 `replay`，因本程序只读取回放。
- 当前背景差分识别的是移动物体，不能证明物体是车辆。它适合打通接口和计数链路。真实道路检测器、遮挡处理、镜头抖动与人工计数误差评估仍需替换和验证。
- 坐标为像素。`line_y` 必须落在画面内。默认 ROI 是全画面，检测阈值和最小区域可在 `src/main.c` 调整。
