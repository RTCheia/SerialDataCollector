"""可在源码与冻结 EXE 运行的隔离自检，不连接物理串口。"""
from functools import partial
import json
import multiprocessing as mp
from pathlib import Path
import time
import traceback

import numpy as np

from acquisition import acquisition_main, make_shared, FRAMES, STATE, COMPLETE, FAILED
from protocol import DEFAULT_RULES, FrameDecoder
from session_data import (CSV_NAMES, apply_filter, discover_channels, export_processed_csv,
                          load_channel, moving_average)


def test_frame(index):
    """已知计数器及时间戳的 61 字节帧，包含正负 24 位数测试值。"""
    data = bytearray(b"\xeb\x90" + bytes([index % 256]))
    for column, (signed, length, _) in enumerate(DEFAULT_RULES[2:], 2):
        value = index * 5 if column == 11 else (-1000 if signed else 0)
        data.extend(value.to_bytes(length, "little", signed=bool(signed)))
    return bytes(data)


class ReplaySerial:
    """碎片化读入模拟跨帧串口块；可在数据读完后模拟拔线。"""
    def __init__(self, payload, disconnect=False):
        self.payload = payload
        self.offset = 0
        self.disconnect = disconnect
        self.calls = 0

    @property
    def in_waiting(self):
        return len(self.payload) - self.offset

    def read(self, size):
        self.calls += 1
        if self.offset == len(self.payload):
            if self.disconnect:
                raise OSError("测试：模拟串口拔线")
            time.sleep(0.002)
            return b""
        length = min(size, [1, 60, 127, 2048, 13, 4096][self.calls % 6])
        data = self.payload[self.offset:self.offset + length]
        self.offset += len(data)
        return data

    def close(self):
        pass


def run_selftest(output):
    """核对真实生成 TXT、三路隔离、预览锁竞争、异常计数和失败收尾。"""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    result = {"state": "failed", "checks": []}
    jobs = []
    locks = []
    try:
        payload = b"".join(test_frame(i) for i in range(3000))
        decoder = FrameDecoder(DEFAULT_RULES)
        decoded = []
        for offset in range(0, len(payload), 17):
            decoded.extend(decoder.feed(payload[offset:offset + 17]))
        assert len(decoded) == 3000 and decoder.anomalies == 0 and not decoder.buffer
        assert decoded[-1][11] == 14.995 and decoded[0][15] == -1.0
        gap = FrameDecoder(DEFAULT_RULES)
        gap.feed(test_frame(254) + test_frame(255) + test_frame(0) + test_frame(2) + test_frame(2))
        assert gap.anomalies == 2
        noisy = FrameDecoder(DEFAULT_RULES)
        assert len(noisy.feed(b"xyz" + test_frame(0) + test_frame(1)[:30])) == 1
        assert noisy.discarded == 3 and len(noisy.buffer) == 30
        result["checks"].append("协议跨 17 字节边界、255→0、跳帧、重复、噪声、半帧、负 24 位值通过")
        ctx = mp.get_context("spawn")
        for index in range(3):
            status, preview, event = make_shared(ctx)
            # 模拟 GUI 卡住并持有预览锁：记录端仍必须完整落盘。
            preview.get_lock().acquire()
            locks.append(preview.get_lock())
            config = dict(folder=str(output / f"ch{index + 1}"), port="TEST", baud=230440, rules=DEFAULT_RULES)
            process = ctx.Process(target=acquisition_main, args=(config, status, preview, event, partial(ReplaySerial, payload)))
            process.start()
            jobs.append((process, status, event))
        deadline = time.perf_counter() + 30
        while time.perf_counter() < deadline and any(s[FRAMES] < 3000 for _, s, _ in jobs):
            time.sleep(0.02)
        for _, status, event in jobs:
            assert status[FRAMES] == 3000, list(status)
            event.set()
        for index, (process, status, _) in enumerate(jobs):
            process.join(10)
            assert process.exitcode == 0 and status[STATE] == COMPLETE
            folder = output / f"ch{index + 1}"
            rows = [line.split() for line in (folder / "data.txt").read_text().splitlines()]
            assert len(rows) == 3000 and all(len(row) == 23 for row in rows)
            assert [int(row[1]) for row in rows] == [i % 256 for i in range(3000)]
            assert [float(row[11]) for row in rows] == [i * .005 for i in range(3000)] or all(abs(float(row[11]) - i * .005) < 1e-8 for i, row in enumerate(rows))
            assert not list(folder.glob("*.bin"))
            summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
            assert summary["received_bytes"] == summary["processed_bytes"] == len(payload)
            assert summary["flushed_frames"] == 3000
        result["checks"].append("三路各 3000 帧共 9000 帧逐行核验；UI 预览锁一直占用时仍完整写入 TXT，且无 BIN")
        # V7.1：看板从真实落盘 TXT 加载，滤波不改变输入，CSV 保留全部采样点。
        assert [item.name for item in discover_channels(output)] == ["ch1", "ch2", "ch3"]
        channel = load_channel(output, "ch1")
        original = channel.values.copy()
        filtered = moving_average(channel.values, 5)
        assert channel.frame_count == 3000 and channel.values.shape == (3000, 12)
        assert filtered.shape == original.shape and np.array_equal(channel.values, original)
        assert np.allclose(moving_average([[0.], [0.], [9.], [0.], [0.]], 3).ravel(), [0., 3., 3., 3., 0.])
        # 四阶低通保留低频/直流，一阶高通去直流，带通叠加两者。
        sample_rate = 1000.0
        times = np.arange(2000) / sample_rate
        synthetic = np.column_stack([2.0 + np.sin(2 * np.pi * 5 * times),
                                     -1.0 + np.sin(2 * np.pi * 20 * times),
                                     0.5 + np.sin(2 * np.pi * 50 * times)])
        lowpassed = apply_filter(synthetic, dict(method="lowpass", low_cut=160), sample_rate)
        highpassed = apply_filter(synthetic, dict(method="highpass", high_cut=1.0), sample_rate)
        bandpassed = apply_filter(synthetic, dict(method="bandpass", low_cut=160, high_cut=1.0), sample_rate)
        assert lowpassed.shape == synthetic.shape and not np.array_equal(lowpassed, synthetic)
        assert np.allclose(lowpassed[1000:].mean(axis=0), [2.0, -1.0, 0.5], atol=0.03)
        assert np.all(np.abs(highpassed[1000:].mean(axis=0)) < 0.03)
        assert np.all(np.abs(bandpassed[1000:].mean(axis=0)) < 0.03)
        try:
            apply_filter(synthetic, dict(method="lowpass", low_cut=600), sample_rate)
        except ValueError:
            pass
        else:
            raise AssertionError("超过奈奎斯特频率的低通截止值必须被拒绝")
        csv_path = export_processed_csv(channel, filtered, output / "analysis_test" / "processed.csv", 1000)
        with csv_path.open(encoding="utf-8-sig", newline="") as stream:
            lines = stream.read().splitlines()
        assert len(lines) == 3001 and tuple(lines[0].split(",")) == CSV_NAMES
        assert all(len(line.split(",")) == 16 for line in lines)
        assert lines[1].startswith("0,0,0.000000000,") and lines[-1].startswith("2999,183,2.999000000,")
        result["checks"].append("V7.1 看板数据：真实 TXT 加载、原始数组不变、移动平均、四阶低通/一阶高通/带通、奈奎斯特保护和 3000 帧完整 CSV 导出通过")
        status, preview, event = make_shared(ctx)
        config = dict(folder=str(output / "disconnect"), port="TEST", baud=230440, rules=DEFAULT_RULES)
        process = ctx.Process(target=acquisition_main, args=(config, status, preview, event, partial(ReplaySerial, payload[:6100], True)))
        process.start()
        jobs.append((process, status, event))
        process.join(10)
        assert process.exitcode == 0 and status[STATE] == FAILED and status[FRAMES] == 100
        result["checks"].append("模拟拔线：已接收 100 帧仍写完，最终明确标记失败")
        # 同名记录文件必须拒绝覆盖。
        existing = output / "existing"
        existing.mkdir()
        (existing / "data.txt").write_text("do not overwrite", encoding="utf-8")
        status, preview, event = make_shared(ctx)
        config["folder"] = str(existing)
        process = ctx.Process(target=acquisition_main, args=(config, status, preview, event, partial(ReplaySerial, b"")))
        process.start()
        jobs.append((process, status, event))
        process.join(10)
        assert process.exitcode == 0 and status[STATE] == FAILED
        assert (existing / "data.txt").read_text() == "do not overwrite"
        result["checks"].append("已有记录拒绝覆盖并标记失败")
        result["state"] = "passed"
    except Exception:
        result["error"] = traceback.format_exc()
    finally:
        for lock in locks:
            lock.release()
        for process, _, event in jobs:
            event.set()
            process.join(5)
            if process.is_alive():
                # 仅限测试创建的进程，避免失败自检残留后台进程。
                process.terminate()
                process.join(5)
        result["duration_seconds"] = round(time.perf_counter() - started, 3)
        (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result["state"] == "passed" else 1
