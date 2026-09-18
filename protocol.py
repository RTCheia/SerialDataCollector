"""V5 协议：保留原有 23 列格式，提供增量组帧与计数检查。"""
from pathlib import Path

DEFAULT_RULES = [
    (0, 2, 1.), (0, 1, 1.),
    (1, 4, 21474.83648), (1, 4, 21474.83648), (1, 4, 21474.83648),
    (1, 4, 26843.5456), (1, 4, 26843.5456), (1, 4, 26843.5456),
    (1, 2, 80.), (1, 2, 80.), (1, 2, 80.), (0, 4, 1000.),
    (0, 1, 100.), (0, 1, 100.), (0, 1, 100.),
    (1, 3, 1000.), (1, 3, 1000.), (1, 3, 1000.),
    (1, 3, 10000.), (1, 3, 10000.), (1, 3, 10000.),
    (0, 2, 1000.), (0, 1, 1.),
]
FRAME_SIZE = 61
PLOT_COLUMNS = (2, 3, 4, 5, 6, 7, 8, 9, 10, 15, 16, 17)


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


def format_row(values, rules):
    """与 V4 的 TXT 列宽、精度和顺序一致。"""
    return "".join(f"{int(v):17d}" if r[2] == 1. else f"{v:24.8f}" for v, r in zip(values, rules)) + "\n"


class FrameDecoder:
    """跨 read 边界保留半帧；同步丢弃与停止边界不足帧字节单独统计。"""
    def __init__(self, rules):
        self.rules = rules
        self.buffer = bytearray()
        self.discarded = 0
        self.previous = None
        self.anomalies = 0

    def feed(self, chunk):
        self.buffer.extend(chunk)
        rows = []
        offset = 0
        while len(self.buffer) - offset >= FRAME_SIZE:
            start = self.buffer.find(b"\xeb\x90", offset)
            if start < 0:
                keep = 1 if self.buffer[-1] == 0xEB else 0
                end = len(self.buffer) - keep
                self.discarded += end - offset
                offset = end
                break
            self.discarded += start - offset
            offset = start
            if len(self.buffer) - offset < FRAME_SIZE:
                break
            frame = self.buffer[offset:offset + FRAME_SIZE]
            counter = frame[2]
            if self.previous is not None and ((counter - self.previous) & 255) != 1:
                self.anomalies += 1
            self.previous = counter
            rows.append(parse_frame(frame, self.rules))
            offset += FRAME_SIZE
        del self.buffer[:offset]
        return rows
