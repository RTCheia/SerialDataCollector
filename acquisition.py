"""每路独立进程：接收线程只读串口，写入线程解析并保存 TXT。"""
import json
import logging
import math
import os
from pathlib import Path
import queue
import threading
import time
from collections import deque

from protocol import DEFAULT_RULES, PLOT_COLUMNS, FrameDecoder, format_row

# 状态由采集端单向发布，界面只读；错误信息另存 JSON 和阶段日志。
STATE, RECEIVED, PROCESSED, FRAMES, ANOMALIES, BACKLOG, HIGH_WATER, DISCARDED, TAIL, LAST_COUNTER, FLUSHED = range(11)
STARTING, RUNNING, STOPPING, COMPLETE, FAILED = 0, 1, 2, 3, -1
PREVIEW_POINTS = 2000
PREVIEW_WIDTH = 13
QUEUE_CHUNKS = 1024  # 最多 1024 块，每块至多 4096 字节；容量时间取决于驱动实际返回的块大小。


def make_shared(ctx):
    """固定大小共享内存，不使用会无限积压的逐帧 UI 消息队列。"""
    return ctx.Array("d", 11, lock=False), ctx.Array("d", 2 + PREVIEW_POINTS * PREVIEW_WIDTH), ctx.Event()


def publish_preview(preview, history, total):
    """界面持锁时直接跳过本次快照，绝不为了预览等待。"""
    lock = preview.get_lock()
    if not lock.acquire(False):
        return
    try:
        flat = [value for row in history for value in row]
        preview[2:2 + len(flat)] = flat
        preview[1] = len(history)
        preview[0] = total
    finally:
        lock.release()


class SimulatedSerial:
    """内置确定性 200 Hz 数据源，仅用于自检，不占用真实串口。"""
    def __init__(self, rate=200):
        self.rate = rate
        self.start = time.perf_counter()
        self.sent = 0

    @property
    def in_waiting(self):
        return max(0, int((time.perf_counter() - self.start) * self.rate) - self.sent) * 61

    def read(self, size):
        time.sleep(0.005)
        count = min(max(0, int((time.perf_counter() - self.start) * self.rate) - self.sent), max(1, size // 61))
        chunks = []
        for _ in range(count):
            index = self.sent
            frame = bytearray(b"\xeb\x90" + bytes([index % 256]))
            for column, (signed, length, scale) in enumerate(DEFAULT_RULES[2:], 2):
                value = int(1000 * index / self.rate) if column == 11 else (int(math.sin(index / 25 + column) * min(scale, 10000)) if signed else 0)
                frame.extend(value.to_bytes(length, "little", signed=bool(signed)))
            chunks.append(frame)
            self.sent += 1
        return b"".join(chunks)

    def close(self):
        pass


class DTextReplaySerial:
    """加载 d.txt 真实硬件数据，按 200 Hz (61字节/帧，12.2KB/s) 循环回放。"""
    def __init__(self, file_path=r"C:\Users\RTC\Desktop\222\d.txt", rate=200):
        self.rate = rate
        self.bytes_per_sec = rate * 61
        self.start_time = None
        self.sent_bytes = 0
        raw = b""
        alt_paths = [file_path, r"C:\Users\RTC\Desktop\222\demo\d.txt", "d.txt"]
        for p in alt_paths:
            if os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8", errors="ignore") as f:
                        clean = "".join(c for c in f.read() if c in "0123456789abcdefABCDEF")
                    if len(clean) % 2 != 0:
                        clean = clean[:-1]
                    raw = bytes.fromhex(clean)
                    break
                except Exception:
                    continue
        self.raw = raw

    @property
    def in_waiting(self):
        if not self.raw:
            return 0
        if self.start_time is None:
            self.start_time = time.perf_counter()
        elapsed = time.perf_counter() - self.start_time
        target_bytes = int(elapsed * self.bytes_per_sec)
        return max(0, target_bytes - self.sent_bytes)

    def read(self, size):
        time.sleep(0.002)
        if not self.raw:
            return b""
        avail = self.in_waiting
        if avail <= 0:
            return b""
        to_read = min(size, avail)
        start_pos = self.sent_bytes % len(self.raw)
        self.sent_bytes += to_read
        if start_pos + to_read <= len(self.raw):
            return self.raw[start_pos : start_pos + to_read]
        else:
            first = self.raw[start_pos:]
            rest = to_read - len(first)
            return first + self.raw[:rest]

    def close(self):
        pass


def acquisition_main(config, status, preview, stop_event, serial_factory=None):
    """进程入口；正常结束须接收线程退出、记录队列排空且文件同步成功。"""
    folder = Path(config["folder"])
    folder.mkdir(parents=True, exist_ok=True)
    log_folder = folder / "log"
    log_folder.mkdir(exist_ok=True)
    logger = logging.getLogger(f"acquisition.{os.getpid()}")
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(log_folder / "acquisition.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    started = time.time()
    errors = []
    chunks = queue.Queue(maxsize=QUEUE_CHUNKS)
    reader_done = threading.Event()
    writer_failed = threading.Event()
    decoder = FrameDecoder(config["rules"])
    history = deque(maxlen=PREVIEW_POINTS)
    ser = None
    reader = None

    def receive():
        """读取路径没有 GUI、解码、文本格式化或磁盘操作。"""
        try:
            while not stop_event.is_set() and not writer_failed.is_set():
                data = ser.read(min(4096, max(1, ser.in_waiting)))
                if data:
                    status[RECEIVED] += len(data)
                    try:
                        chunks.put_nowait(data)
                    except queue.Full:
                        # 不静默丢弃：等待写线程接走已收到的块，然后按失败状态结束。
                        errors.append("记录缓冲已满，采集停止；设备后续数据可能丢失，请检查磁盘")
                        stop_event.set()
                        while not writer_failed.is_set():
                            try:
                                chunks.put(data, timeout=0.1)
                                break
                            except queue.Full:
                                continue
                    status[BACKLOG] = chunks.qsize()
                    status[HIGH_WATER] = max(status[HIGH_WATER], chunks.qsize())
            status[STATE] = STOPPING
            # 停止边界：只排空此刻驱动中已有字节，不追逐设备无限发送的新数据。
            pending = ser.in_waiting if not writer_failed.is_set() else 0
            while pending > 0 and not writer_failed.is_set():
                data = ser.read(min(4096, pending))
                if not data:
                    break
                pending -= len(data)
                status[RECEIVED] += len(data)
                while not writer_failed.is_set():
                    try:
                        chunks.put(data, timeout=0.1)
                        break
                    except queue.Full:
                        continue
        except Exception as exc:
            errors.append(f"串口读取失败: {exc}")
            logger.exception("串口读取失败")
        finally:
            reader_done.set()

    try:
        logger.info("运行开始 port=%s baud=%s", config["port"], config["baud"])
        # 独占创建防止覆盖旧记录，串口打开前先确认输出目录可写。
        # [替换为] 用户仅需短时 TXT 记录，不生成原始二进制文件。
        with open(folder / "data.txt", "x", encoding="utf-8", buffering=262144) as txt:
            if serial_factory:
                ser = serial_factory()
            elif config["port"] == "SIM_D":
                ser = DTextReplaySerial(rate=config.get("rate", 200))
            elif config["port"] == "SIM":
                ser = SimulatedSerial(config.get("rate", 200))
            else:
                import serial
                ser = serial.Serial(config["port"], config["baud"], bytesize=8, parity="N", stopbits=1, timeout=0.05)
                try:
                    ser.set_buffer_size(rx_size=262144)
                except (AttributeError, OSError, ValueError) as exc:
                    logger.warning("驱动接收缓冲扩容未生效，使用驱动默认值: %s", exc)
                try:
                    ser.reset_input_buffer()
                except Exception:
                    pass
            status[STATE] = RUNNING
            reader = threading.Thread(target=receive, name="serial-reader", daemon=False)
            reader.start()
            last_flush = last_preview = time.perf_counter()
            while not reader_done.is_set() or not chunks.empty():
                try:
                    data = chunks.get(timeout=0.05)
                except queue.Empty:
                    data = b""
                if data:
                    rows = decoder.feed(data)
                    txt.writelines(format_row(row, config["rules"]) for row in rows)
                    status[PROCESSED] += len(data)
                    for row in rows:
                        history.append([status[FRAMES]] + [row[c] for c in PLOT_COLUMNS])
                        status[FRAMES] += 1
                    status[ANOMALIES] = decoder.anomalies
                    status[DISCARDED] = decoder.discarded
                    status[LAST_COUNTER] = decoder.previous if decoder.previous is not None else -1
                    status[BACKLOG] = chunks.qsize()
                now = time.perf_counter()
                if now - last_flush >= 0.2:
                    txt.flush()
                    status[FLUSHED] = status[FRAMES]
                    last_flush = now
                if now - last_preview >= 0.1:
                    publish_preview(preview, history, status[FRAMES])
                    last_preview = now
            reader.join()
            status[TAIL] = len(decoder.buffer)
            publish_preview(preview, history, status[FRAMES])
            txt.flush()
            # 只在收尾同步磁盘，接收阶段不做高频强制刷盘。
            os.fsync(txt.fileno())
            status[FLUSHED] = status[FRAMES]
        if status[RECEIVED] != status[PROCESSED]:
            errors.append("收到字节数与解析处理字节数不一致")
    except Exception as exc:
        errors.append(f"采集/记录失败: {exc}")
        logger.exception("采集/记录失败")
    finally:
        writer_failed.set()
        stop_event.set()
        if reader:
            reader.join()
        if ser:
            try:
                ser.close()
            except Exception as exc:
                errors.append(f"串口关闭失败: {exc}")
        status[STATE] = FAILED if errors else COMPLETE
        status[TAIL] = len(decoder.buffer)
        summary = {
            "state": "failed" if errors else "complete", "port": config["port"], "baud": config["baud"],
            "started": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started)),
            "duration_seconds": round(time.time() - started, 3),
            "received_bytes": int(status[RECEIVED]), "processed_bytes": int(status[PROCESSED]),
            "written_frames": int(status[FRAMES]), "flushed_frames": int(status[FLUSHED]),
            "counter_anomalies": decoder.anomalies, "resync_discarded_bytes": decoder.discarded,
            "trailing_bytes": len(decoder.buffer), "queue_high_water_chunks": int(status[HIGH_WATER]),
            "checksum_verified": False, "errors": errors, "rules": config["rules"],
        }
        try:
            (folder / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            status[STATE] = FAILED
            logger.exception("无法写入摘要")
        logger.info("阶段结果 %s", json.dumps(summary, ensure_ascii=False))
        handler.close()
        logger.removeHandler(handler)
