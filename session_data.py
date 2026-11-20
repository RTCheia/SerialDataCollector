"""离线会话发现、分析列加载、滤波和处理后 CSV 导出；不依赖 Qt。"""
from dataclasses import dataclass
import json
from pathlib import Path

import numpy as np

from plot_metadata import PLOT_COLUMNS, PLOT_GROUPS


DATA_COLUMNS = (1, 11) + PLOT_COLUMNS
CSV_NAMES = ("frame_index", "device_counter", "nominal_time_s", "device_time_s") + tuple(
    name for group in PLOT_GROUPS for name in group["names"]
)


class SessionDataError(ValueError):
    """会话目录或记录文件不能用于离线分析。"""


@dataclass(frozen=True)
class ChannelSource:
    name: str
    path: Path
    summary: dict


@dataclass(frozen=True)
class ChannelData:
    name: str
    path: Path
    summary: dict
    counter: np.ndarray
    device_time: np.ndarray
    values: np.ndarray
    rate_hz: float

    @property
    def frame_count(self):
        return int(self.values.shape[0])

    @property
    def available_groups(self):
        header = self.summary.get("frame_header")
        if header == "EB 91":
            return (True, True, True, False)
        if header == "EB 92":
            return (False, False, False, True)
        return (True, True, True, True)

    @property
    def has_device_time(self):
        return (self.device_time.size > 1 and np.all(np.isfinite(self.device_time))
                and bool(np.any(np.diff(self.device_time) != 0)))


def _summary(channel_folder):
    path = channel_folder / "summary.json"
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {"summary_warning": "摘要不是 JSON 对象"}
    except Exception as exc:
        return {"summary_warning": f"摘要读取失败：{exc}"}


def _channel_key(path):
    suffix = path.parent.name[2:]
    return (int(suffix) if suffix.isdigit() else 10**9, path.parent.name)


def discover_channels(session):
    """枚举会话内已有 TXT；空文件留给加载阶段给出明确错误。"""
    folder = Path(session)
    if not folder.is_dir():
        raise SessionDataError(f"会话目录不存在：{folder}")
    channels = [ChannelSource(path.parent.name, path, _summary(path.parent))
                for path in sorted(folder.glob("ch*/data.txt"), key=_channel_key) if path.is_file()]
    if not channels:
        raise SessionDataError(f"会话中没有可分析的 chN/data.txt：{folder}")
    return channels


def load_channel(session, channel_name):
    """只加载分析所需 14 列，完整原始 TXT 保持不变。"""
    source = next((item for item in discover_channels(session) if item.name == channel_name), None)
    if source is None:
        raise SessionDataError(f"找不到通道 {channel_name}")
    try:
        data = np.loadtxt(source.path, usecols=DATA_COLUMNS, ndmin=2)
    except Exception as exc:
        raise SessionDataError(f"无法读取 {source.path}：{exc}") from exc
    if data.shape[0] == 0:
        raise SessionDataError(f"{source.path} 是空记录")
    rate = source.summary.get("rate_hz") or 1000
    try:
        rate = float(rate)
    except (TypeError, ValueError):
        rate = 1000.0
    if not np.isfinite(rate) or rate <= 0:
        rate = 1000.0
    return ChannelData(
        name=source.name,
        path=source.path,
        summary=source.summary,
        counter=data[:, 0].astype(np.int64),
        device_time=data[:, 1].copy(),
        values=data[:, 2:].copy(),
        rate_hz=rate,
    )


def moving_average(values, window):
    """居中移动平均；边缘使用实际存在的样本，返回形状与输入相同。"""
    values = np.asarray(values, dtype=np.float64)
    window = int(window)
    if values.ndim != 2:
        raise ValueError("滤波输入必须是二维数组")
    if window < 1:
        raise ValueError("滤波窗口必须至少为 1 个采样点")
    count = values.shape[0]
    if not count or window == 1:
        return values.copy()
    window = min(window, max(1, count * 2 - 1))
    indices = np.arange(count)
    left, right = (window - 1) // 2, window // 2
    starts = np.maximum(0, indices - left)
    ends = np.minimum(count, indices + right + 1)
    cumulative = np.vstack((np.zeros((1, values.shape[1])), np.cumsum(values, axis=0)))
    return (cumulative[ends] - cumulative[starts]) / (ends - starts)[:, None]


def _check_cutoff(cutoff, rate_hz, name):
    cutoff = float(cutoff)
    if not np.isfinite(cutoff) or cutoff <= 0:
        raise ValueError(f"{name}截止频率必须为有限正数")
    if cutoff >= rate_hz / 2:
        raise ValueError(f"{name}截止频率 {cutoff:g} Hz 必须低于奈奎斯特频率 {rate_hz / 2:g} Hz")
    return cutoff


def _biquad_lowpass(values, cutoff, rate_hz, q):
    """RBJ 双二次低通；两级级联构成四阶 Butterworth 低通。"""
    omega = 2 * np.pi * cutoff / rate_hz
    cosine, sine = np.cos(omega), np.sin(omega)
    alpha = sine / (2 * q)
    a0 = 1 + alpha
    b0 = (1 - cosine) / 2 / a0
    b1 = (1 - cosine) / a0
    b2 = b0
    a1 = -2 * cosine / a0
    a2 = (1 - alpha) / a0
    output = np.empty_like(values, dtype=np.float64)
    state1 = np.zeros(values.shape[1], dtype=np.float64)
    state2 = np.zeros(values.shape[1], dtype=np.float64)
    for index in range(values.shape[0]):
        source = values[index]
        filtered = b0 * source + state1
        state1 = b1 * source - a1 * filtered + state2
        state2 = b2 * source - a2 * filtered
        output[index] = filtered
    return output


def adxl373_lowpass(values, cutoff, rate_hz):
    """四阶 Butterworth 低通滤波，截止频率为 -3 dB 频率。"""
    cutoff = _check_cutoff(cutoff, rate_hz, "低通")
    output = _biquad_lowpass(values, cutoff, rate_hz, 0.541196100146197)
    return _biquad_lowpass(output, cutoff, rate_hz, 1.306562964876377)


def adxl373_highpass(values, cutoff, rate_hz):
    """一阶 IIR 高通滤波，用于去除直流分量和缓慢温漂。"""
    cutoff = _check_cutoff(cutoff, rate_hz, "高通")
    k = np.tan(np.pi * cutoff / rate_hz)
    scale = 1.0 / (1.0 + k)
    feedback = (k - 1.0) / (k + 1.0)
    output = np.empty_like(values, dtype=np.float64)
    previous_x = values[0].copy()
    previous_y = np.zeros(values.shape[1], dtype=np.float64)
    for index in range(values.shape[0]):
        source = values[index]
        filtered = scale * (source - previous_x) - feedback * previous_y
        output[index] = filtered
        previous_x = source
        previous_y = filtered
    return output


def apply_filter(values, settings, rate_hz):
    """按界面设置处理一组三轴数据；返回新数组，不修改原始记录。"""
    values = np.asarray(values, dtype=np.float64)
    if values.ndim != 2:
        raise ValueError("滤波输入必须是二维数组")
    method = settings.get("method", "none")
    if method == "none":
        return values.copy()
    if method == "moving_average":
        return moving_average(values, int(settings.get("window", 5)))
    if method == "lowpass":
        return adxl373_lowpass(values, settings["low_cut"], rate_hz)
    if method == "highpass":
        return adxl373_highpass(values, settings["high_cut"], rate_hz)
    if method == "bandpass":
        highpassed = adxl373_highpass(values, settings["high_cut"], rate_hz)
        return adxl373_lowpass(highpassed, settings["low_cut"], rate_hz)
    raise ValueError(f"未知滤波方式：{method}")


def nominal_times(count, rate_hz):
    if not np.isfinite(rate_hz) or rate_hz <= 0:
        raise ValueError("采样率必须为有限正数")
    return np.arange(count, dtype=np.float64) / float(rate_hz)


def export_processed_csv(channel, values, output, rate_hz):
    """分块写完整处理结果，避免为整个 CSV 再创建一份巨型字符串。"""
    values = np.asarray(values, dtype=np.float64)
    if values.shape != channel.values.shape:
        raise ValueError("导出数组与通道记录形状不一致")
    times = nominal_times(channel.frame_count, rate_hz)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    formats = ["%d", "%d", "%.9f", "%.9f"] + ["%.9f"] * len(PLOT_COLUMNS)
    with output.open("w", encoding="utf-8-sig", newline="") as stream:
        stream.write(",".join(CSV_NAMES) + "\n")
        for start in range(0, channel.frame_count, 10000):
            end = min(channel.frame_count, start + 10000)
            block = np.column_stack((np.arange(start, end), channel.counter[start:end],
                                     times[start:end], channel.device_time[start:end], values[start:end]))
            np.savetxt(stream, block, delimiter=",", fmt=formats)
    return output
