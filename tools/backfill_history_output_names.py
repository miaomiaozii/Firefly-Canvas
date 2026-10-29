"""#224 存量回填：把生成历史里丢失的可读文件名补回 outputs。

改动前写入的 history outputs 只有 {asset_id, kind}，名字其实一直存在项目媒体索引里
（media/index.json 的 name 就是当初上传/保存的文件名）。这个脚本一次性把它补回去。

    python tools/backfill_history_output_names.py                 # 预演，看清会改什么
    python tools/backfill_history_output_names.py --apply         # 真的写
    python tools/backfill_history_output_names.py --apply --project <项目ID>

写之前会把每个要改的 history.json 备份到 --backup-dir
（默认 <项目库>-backfill-backups/<UTC 时间戳>/，放在项目库外面，免得被当成项目目录）。
只补没有 name 的项，已有名字不动，所以重复跑第二次是空操作。

⚠️ 应用正在运行时跑 --apply 之后必须重启应用：进程内存里有一份历史缓存，
不重启的话后续生成任务可能用旧缓存把补好的名字覆盖掉。
"""
import argparse
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from firefly_projects import ProjectStore, default_projects_root  # noqa: E402


def _backup_history(store_root: Path, project_ids, backup_root: Path) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    target = backup_root / stamp
    for project_id in project_ids:
        source = store_root / project_id / "history.json"
        if not source.exists():
            continue
        destination = target / project_id
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination / "history.json")
    return target


def _print_summary(store_root: Path, summary: dict, *, applied: bool, backup_at: Path | None = None) -> None:
    print(f"项目库：{store_root}")
    print("扫描项目：%d  扫描记录：%d  输出项：%d" % (
        summary["projects_scanned"], summary["records_scanned"], summary["outputs_total"]))
    print("待补名字：%d  补不上（媒体索引里也没有可读名字）：%d" % (
        summary["outputs_named"], summary["outputs_unresolved"]))
    if applied:
        print("已写盘，改动 %d 个项目" % summary["projects_changed"])
        if backup_at is not None:
            print("原文件备份：%s" % backup_at)
    else:
        print("预演：未写盘。会改动 %d 个项目" % len(summary["changed_projects"]))


def main(argv=None) -> int:
    # Windows 控制台默认不是 UTF-8，项目名/文件名全是中文，不强制会喷乱码。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description="给生成历史补回可读文件名（#224 存量数据）")
    parser.add_argument("--apply", action="store_true", help="真的写盘（默认只预演）")
    parser.add_argument("--project", default=None, help="只处理指定项目 ID")
    parser.add_argument("--projects-root", default=None, help="项目库路径，默认取 FIREFLY_PROJECTS_ROOT")
    parser.add_argument("--backup-dir", default=None, help="备份根目录，默认 <项目库>-backfill-backups")
    args = parser.parse_args(argv)

    store_root = Path(args.projects_root) if args.projects_root else default_projects_root()
    if not store_root.exists():
        print("项目库不存在：%s" % store_root)
        return 1

    store = ProjectStore(store_root, snapshot_delay=0)
    try:
        plan = store.backfill_history_output_names(project_id=args.project, dry_run=True)
        _print_summary(store_root, plan, applied=False)
        if not args.apply:
            print("\n确认无误后加 --apply 真正写入；写入后请重启应用。")
            return 0
        if not plan["changed_projects"]:
            print("没有需要补的名字，无需写入。")
            return 0
        backup_root = Path(args.backup_dir) if args.backup_dir else Path(str(store_root) + "-backfill-backups")
        backup_at = _backup_history(store_root, plan["changed_projects"], backup_root)
        result = store.backfill_history_output_names(project_id=args.project, dry_run=False)
        _print_summary(store_root, result, applied=True, backup_at=backup_at)
        print("\n若应用正在运行，请重启应用，否则内存里的旧历史缓存可能覆盖刚补好的名字。")
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
