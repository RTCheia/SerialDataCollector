"""采集预览、完整波形导出和离线看板共用的字段与图表定义。"""

PLOT_COLORS = ("#dc2626", "#16a34a", "#2563eb")

# columns 使用 data.txt 的 0 基列号；布局仅供 Qt 实时预览和离线看板使用。
PLOT_GROUPS = (
    dict(key="gyro", title="IMU 角速度", unit="deg/s", names=("wx", "wy", "wz"),
         columns=(2, 3, 4), row=0, column=0, rowspan=1),
    dict(key="accel", title="IMU 线加速度", unit="m/s²", names=("ax", "ay", "az"),
         columns=(5, 6, 7), row=1, column=0, rowspan=1),
    dict(key="shock", title="大量程冲击加速度", unit="m/s²", names=("X", "Y", "Z"),
         columns=(8, 9, 10), row=0, column=1, rowspan=2),
    dict(key="uwb", title="UWB 空间位置", unit="m", names=("pos_x", "pos_y", "pos_z"),
         columns=(15, 16, 17), row=0, column=2, rowspan=2),
)

PLOT_COLUMNS = tuple(column for group in PLOT_GROUPS for column in group["columns"])
