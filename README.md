# PrivateTraffic 地点与记录模块

陈嘉俊负责的 C 模块。只管理已登记地点、单一用户的本地关注状态、Measurement 持久化和时间范围查询。数据存储为 `locations.psv`、`follows.psv`、`measurements.psv`，目录由调用方提供；不要把真实视频或身份数据存入这些文件。

## 构建与测试

```powershell
cmake -S E:\SW_Project\jiajun_backend -B E:\SW_Project\jiajun_backend\build -G "MinGW Makefiles"
cmake --build E:\SW_Project\jiajun_backend\build
ctest --test-dir E:\SW_Project\jiajun_backend\build --output-on-failure
```

命令行示例（先用团队已确认的真实地点 ID、来源和许可状态替换占位值）：

```powershell
E:\SW_Project\jiajun_backend\build\pt_locations.exe E:\SW_Project\local_data add "LOCATION_ID" "LOCATION_NAME" "SOURCE_PATH" replay pending
E:\SW_Project\jiajun_backend\build\pt_locations.exe E:\SW_Project\local_data list
E:\SW_Project\jiajun_backend\build\pt_locations.exe E:\SW_Project\local_data follow "LOCATION_ID"
E:\SW_Project\jiajun_backend\build\pt_locations.exe E:\SW_Project\local_data current "LOCATION_ID"
```

`current`、`history` 在姚子严模块的 `PTMeasurement` 经 `pt_save_measurement()` 写入后才有记录。命令行不生成虚构测量数据。`approved` 仅表示团队登记的许可状态，不代表程序已检测视频源是否在线。

## 使用接口

包含 `include/location_store.h`，并链接 `pt_location_store`。先创建一个可写数据目录，再调用 `pt_store_open()`。之后可调用：

- `pt_add_location()`、`pt_get_location()`、`pt_list_locations()`：登记和浏览地点。地点必须显式标记 `approved` 或 `pending`；模块不会自行发现或授权摄像头。
- `pt_follow_location()`、`pt_unfollow_location()`、`pt_is_followed()`：关注状态持久化；重复操作返回 0。
- `pt_save_measurement()`：直接接收姚子严模块的 `PTMeasurement`。地点不存在或许可仍为 `pending` 时返回 0，不写入。
- `pt_get_current_status()`：返回时间最新的一条记录，包括无效记录，前端应检查 `validity`。
- `pt_query_measurements()`：按地点和毫秒时间区间查询，回调逐条接收记录。

结果为 1 表示完成操作或找到记录，0 表示无变化或未找到，-1 表示参数或 I/O 错误。列表和查询返回数量或 -1。当前数据文件只适合单进程演示，之后若需要多用户或并发写入，应换成数据库，并将关注关系加入用户 ID。
