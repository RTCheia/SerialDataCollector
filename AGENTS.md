# 项目临时工具清单

## `_temp/tools`

- `build_v5.ps1`：使用 Conda base 和 PyInstaller 生成 V5 独立 EXE；运行 `powershell -ExecutionPolicy Bypass -File _temp/tools/build_v5.ps1`，构建日志位于 `log/build_v5.log`。
- `verify_window_close_v5.py`：验证三路仿真正在记录时直接关闭窗口的完整收尾；使用 Conda base Python 运行，结果位于 `log/verify_window_close_v5.log`。
- `SerialDataCollector_V5.spec`：维护的 PyInstaller 构建配置，明确排除与 Qt 系统 ICU 冲突的第三方 DLL；由 `build_v5.ps1` 调用，不应重新自动生成覆盖。

## V5 自检

- `app_v5.py --self-test _temp/data/唯一测试目录`：三路记录、计数异常、预览阻塞隔离、异常收尾和防覆盖测试。相同参数也适用于 EXE。自检目录必须尚不存在。
- `app_v5.py --ui-smoke-test`：三路仿真启动、绘图、停止和窗口关闭自检，截图及记录位于 `_temp/data/ui_smoke/`，不连接真实串口。
