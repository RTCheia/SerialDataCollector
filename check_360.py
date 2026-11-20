# -*- coding: utf-8 -*-
"""整圈校验：把录到的 data.txt 积一次角度，判定陀螺系数对不对。

用法：
    python check_360.py data.txt                    # 帧率 1000、除数取 fr.txt
    python check_360.py data.txt 1000               # 指定帧率
    python check_360.py data.txt 1000 4294.967296   # 指定录制时所用陀螺除数

判读（绕竖直轴转满一圈）：
    约 360°  → 系数正确
    约 250°  → 用的是商家 ±500° 口径，应改 ±720°（除数 2982.616178）
    约 180°  → 固件还在 2000 Hz，不是系数问题
    约   0°  → 没转起来，或转轴不是竖直
"""
import sys
from pathlib import Path

DATASHEET_RANGE = 720.0
VENDOR_DIV = 4294.967296   # 商家口径 ÷5（隐含 ±500° @1000Hz）
DS_DIV = 2982.616178       # 手册口径（隐含 ±720° @1000Hz）


def read_divisor(script_dir):
    """从 fr.txt 第 3 行取陀螺除数，失败则用手册值。"""
    p = Path(script_dir) / "fr.txt"
    try:
        lines = [x for x in p.read_text(encoding="utf-8-sig").splitlines() if x.strip()]
        return float(lines[2].split(",")[2])
    except Exception:
        return DS_DIV


if len(sys.argv) < 2:
    raise SystemExit(__doc__)

path = Path(sys.argv[1])
rate = float(sys.argv[2]) if len(sys.argv) > 2 else 1000.0
divisor = float(sys.argv[3]) if len(sys.argv) > 3 else read_divisor(Path(__file__).resolve().parent)
dt = 1.0 / rate

rows = []
with path.open(encoding="utf-8", errors="replace") as f:
    for line in f:
        p = line.split()
        if len(p) >= 23:
            rows.append([float(x) for x in p])

if not rows:
    raise SystemExit("没有解析到数据行")

print("文件    : %s" % path)
print("帧数    : %d   时长 %.2f s（按 %g Hz）" % (len(rows), len(rows) * dt, rate))
print("陀螺除数: %.6f（%s）" % (divisor, "fr.txt" if len(sys.argv) <= 3 else "命令行指定"))

fc = [int(r[1]) for r in rows]
gaps = [(fc[i] - fc[i - 1]) % 256 for i in range(1, len(fc))]
print("帧号    : 增量=1 共 %d 次，异常 %d 次" % (gaps.count(1), sum(1 for g in gaps if g != 1)))

names = ("X", "Y", "Z")
sums = [sum(r[2 + i] for r in rows) * dt for i in range(3)]
peaks = [max(abs(r[2 + i]) for r in rows) for i in range(3)]
axis = max(range(3), key=lambda i: abs(sums[i]))

print("\n各轴累计角度")
for i in range(3):
    print("  %s 轴: %9.2f 度   峰值 %7.1f 度/秒%s"
          % (names[i], sums[i], peaks[i], "   ← 主轴" if i == axis else ""))

ang = sums[axis]
raw = ang * divisor          # 还原成原始码值累加量
print("\n主轴 %s 轴：" % names[axis])
print("  按当前除数 %.6f          : %9.2f 度" % (divisor, ang))
print("  若按商家 ±500° 口径(4294.967296): %9.2f 度" % (raw / VENDOR_DIV))
print("  若按手册 ±720° 口径(2982.616178): %9.2f 度" % (raw / DS_DIV))
print()

if abs(abs(ang) - 360) < 12:
    print("  → 系数正确，一圈 = 360° ✅")
else:
    rec = divisor * abs(ang) / 360.0
    print("  → 与 360° 不符，按比例反推的除数应为 %.6f" % rec)
    if abs(rec - DS_DIV) < 60:
        print("    即手册 ±720° 口径（2982.616178），当前用的应是商家 ±500° 口径。")
    print("    也可能只是没转满一圈 / 转轴不竖直（Z 轴峰值 %.1f 度/秒）" % peaks[axis])
