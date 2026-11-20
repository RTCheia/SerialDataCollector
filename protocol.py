"""V5 协议：保留原有 23 列格式，提供增量组帧与计数检查。"""
from pathlib import Path

from plot_metadata import PLOT_COLUMNS

DEFAULT_RULES = [
    (0, 2, 1.), (0, 1, 1.),
    (1, 4, 2982.616178), (1, 4, 2982.616178), (1, 4, 2982.616178),
    (1, 4, 5368.70912), (1, 4, 5368.70912), (1, 4, 5368.70912),
    (1, 2, 8.1577297), (1, 2, 8.1577297), (1, 2, 8.1577297), (0, 4, 1000.),
    (0, 1, 100.), (0, 1, 100.), (0, 1, 100.),
    (1, 3, 1000.), (1, 3, 1000.), (1, 3, 1000.),
    (1, 3, 10000.), (1, 3, 10000.), (1, 3, 10000.),
    (0, 2, 1000.), (0, 1, 1.),
]
FRAME_SIZE = 61
# 帧头第二字节 → (串口帧长, 该数据源采样率 Hz)。
#   0x90 / 61 字节：老格式全量帧（IMU24 + ADXL6 + UWB27 都在），采样率由界面手动指定
#   0x91 / 34 字节：IMU 板瘦身帧，只含 IMU24 + ADXL6，UWB 的 27 个 0 字节不上串口
#   0x92 / 31 字节：UWB 板瘦身帧，只含 UWB27，IMU/ADXL 的 30 个 0 字节不上串口
# 两种瘦身帧补零后都归一化为 61 字节老格式，23 列解析规则与 TXT 列格式完全不变。
FRAME_TYPES = {0x90: (61, 0), 0x91: (34, 1000), 0x92: (31, 50)}


def load_rules(path):
    """允许校准比例覆盖，但拒绝会破坏 V5 帧布局的外部规则。"""
    if not Path(path).exists():
        return list(DEFAULT_RULES)
    rules = []
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            signed, length, scale = line.strip().split(",")
            rules.append((int(signed), int(length), float(scale)))
    if len(rules) != len(DEFAULT_RULES):
        raise ValueError("fr.txt 必须包含 23 项规则")
    for rule, default in zip(rules, DEFAULT_RULES):
        if rule[:2] != default[:2] or not (0 < rule[2] < float("inf")):
            raise ValueError("fr.txt 的字段类型/长度必须符合 61 字节协议，比例须为有限正数")
    if rules[0][2] != 1 or rules[1][2] != 1:
        raise ValueError("帧头和计数器比例必须为 1")
    return rules


def parse_frame(frame, rules):
    """按小端有符号规则解析完整帧，兼容 3 字节位置/速度字段。"""
    values, offset = [], 0
    for signed, length, scale in rules:
        values.append(int.from_bytes(frame[offset:offset + length], "little", signed=bool(signed)) / scale)
        offset += length
    return values


def expand_frame(frame):
    """把瘦身帧补零归一化为 61 字节老格式；帧头统一回 0xEB 0x90，保证 TXT 与历史文件同构。"""
    kind = frame[1]
    if kind == 0x90:
        return frame
    if kind == 0x91:
        # EB 91 计数 | IMU24+ADXL6(3..32) | 校验(33) → UWB 区(33..59)补 27 个 0
        return frame[:1] + b"\x90" + frame[2:33] + bytes(27) + frame[33:34]
    if kind == 0x92:
        # EB 92 计数 | UWB27(3..29) | 校验(30) → IMU/ADXL 区(3..32)补 30 个 0，负载移到 33..59
        return b"\xeb\x90" + frame[2:3] + bytes(30) + frame[3:30] + frame[30:31]
    raise ValueError(f"未知帧类型 0x{kind:02X}")


def format_row(values, rules):
    """与 V4 的 TXT 列宽、精度和顺序一致。"""
    return "".join(f"{int(v):17d}" if r[2] == 1. else f"{v:24.8f}" for v, r in zip(values, rules)) + "\n"


class FrameDecoder:
    """跨 read 边界保留半帧；按帧头第二字节自动识别 61/34/31 三种帧长，无需人工选择协议。"""
    def __init__(self, rules):
        self.rules = rules
        self.buffer = bytearray()
        self.discarded = 0
        self.previous = None
        self.anomalies = 0
        self.kind = 0   # 最近一帧的帧头第二字节；0 = 尚未识别
        self.rate = 0   # 由帧类型推出的采样率 Hz；0 = 老格式，需用界面手动帧率

    def feed(self, chunk):
        self.buffer.extend(chunk)
        buf = self.buffer
        rows = []
        offset = 0
        size = len(buf)
        while offset < size:
            start = buf.find(b"\xeb", offset)
            while start >= 0 and start + 1 < size and buf[start + 1] not in FRAME_TYPES:
                start = buf.find(b"\xeb", start + 1)
            if start < 0:
                self.discarded += size - offset
                offset = size
                break
            if start + 1 >= size:
                # 末尾只有半个帧头 0xEB，留给下一包；它前面的内容按噪声统计。
                self.discarded += start - offset
                offset = start
                break
            self.discarded += start - offset
            frame_size, rate = FRAME_TYPES[buf[start + 1]]
            if size - start < frame_size:
                offset = start   # 半帧，等下一包
                break
            frame = bytes(buf[start:start + frame_size])
            counter = frame[2]
            if self.previous is not None and ((counter - self.previous) & 255) != 1:
                self.anomalies += 1
            self.previous = counter
            self.kind = frame[1]
            self.rate = rate
            rows.append(parse_frame(expand_frame(frame), self.rules))
            offset = start + frame_size
        del self.buffer[:offset]
        return rows
