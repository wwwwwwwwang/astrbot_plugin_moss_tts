"""tmp 缓存清理工具：按时间和数量双重限制回收合成出来的 wav。"""
from __future__ import annotations

import time
from pathlib import Path


def cleanup_tmp(tmp_dir: Path, keep_hours: int = 24, max_files: int = 60,
                patterns: tuple[str, ...] = ("*.wav",)) -> int:
    """删除超过 keep_hours 的文件，再只保留最新 max_files 个。返回删除数量。"""
    now = time.time()
    removed = 0
    keep: list[tuple[float, Path]] = []
    for pattern in patterns:
        for p in Path(tmp_dir).glob(pattern):
            try:
                st = p.stat()
            except OSError:
                continue
            if keep_hours > 0 and now - st.st_mtime > keep_hours * 3600:
                try:
                    p.unlink()
                    removed += 1
                except OSError:
                    pass
            else:
                keep.append((st.st_mtime, p))
    if max_files > 0 and len(keep) > max_files:
        keep.sort(key=lambda x: x[0])
        for _mtime, p in keep[:-max_files]:
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass
    return removed
