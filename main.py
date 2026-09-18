"""V5 启动入口：冻结程序先处理子进程参数，再导入 Qt。"""
import multiprocessing as mp
from pathlib import Path
import sys


def application_root():
    """单文件 EXE 的记录目录位于 EXE 旁，不使用临时解包路径。"""
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent


if __name__ == "__main__":
    mp.freeze_support()
    try:
        if "--self-test" in sys.argv:
            from selftest import run_selftest
            target = Path(sys.argv[sys.argv.index("--self-test") + 1])
            sys.exit(run_selftest(target))
        else:
            from ui import run_gui
            sys.exit(run_gui(application_root(), smoke_test="--ui-smoke-test" in sys.argv))
    except Exception:
        # 界面依赖导入阶段也要留下诊断日志，避免仅出现无控制台的错误弹窗。
        import traceback
        import datetime
        log_dir = application_root() / "log"
        log_dir.mkdir(exist_ok=True)
        with (log_dir / "app_v5.log").open("a", encoding="utf-8") as stream:
            stream.write(f"\n{datetime.datetime.now().isoformat()} 启动/运行失败\n{traceback.format_exc()}\n")
        raise
