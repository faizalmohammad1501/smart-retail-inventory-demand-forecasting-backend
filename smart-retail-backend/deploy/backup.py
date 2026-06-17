#!/usr/bin/env python3
"""
Smart Retail Platform — Database & Model Backup Script
Creates timestamped backups of:
  - SQLite database file (supply_chain.db)
  - ML saved models directory (ml/saved_models/)
  - Uploads directory

Usage:
    python deploy/backup.py                # backup to ./backups/
    python deploy/backup.py --dest /mnt/backup
    python deploy/backup.py --retention 7  # keep last N daily backups

Schedule with cron (Linux):
    0 2 * * * /usr/bin/python3 /app/deploy/backup.py --dest /mnt/backup >> /var/log/smart_retail_backup.log 2>&1

Schedule with Task Scheduler (Windows):
    schtasks /create /sc daily /tn "SmartRetailBackup" /tr "python C:\path\deploy\backup.py" /st 02:00
"""

import argparse
import logging
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("backup")

ROOT = Path(__file__).resolve().parent.parent


def backup_file(src: Path, dest_dir: Path, label: str) -> Path | None:
    """Copy a single file into dest_dir with timestamp suffix."""
    if not src.exists():
        log.warning(f"{label}: source not found — {src}")
        return None
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out = dest_dir / f"{src.stem}_{ts}{src.suffix}"
    shutil.copy2(src, out)
    size_mb = out.stat().st_size / (1024 * 1024)
    log.info(f"{label}: backed up → {out}  ({size_mb:.2f} MB)")
    return out


def backup_directory(src: Path, dest_dir: Path, label: str) -> Path | None:
    """Archive a directory into dest_dir as a .tar.gz with timestamp suffix."""
    if not src.exists():
        log.warning(f"{label}: source directory not found — {src}")
        return None
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    archive_base = dest_dir / f"{src.name}_{ts}"
    out = Path(shutil.make_archive(str(archive_base), "gztar", src.parent, src.name))
    size_mb = out.stat().st_size / (1024 * 1024)
    log.info(f"{label}: archived → {out}  ({size_mb:.2f} MB)")
    return out


def prune_old_backups(dest_dir: Path, retention_days: int) -> None:
    """Delete backup files older than retention_days."""
    cutoff = datetime.utcnow() - timedelta(days=retention_days)
    removed = 0
    for f in dest_dir.iterdir():
        if f.is_file() and datetime.utcfromtimestamp(f.stat().st_mtime) < cutoff:
            f.unlink()
            log.info(f"Pruned old backup: {f.name}")
            removed += 1
    if removed:
        log.info(f"Pruned {removed} backup(s) older than {retention_days} day(s).")
    else:
        log.info("No old backups to prune.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Smart Retail backup utility")
    parser.add_argument("--dest",      default=str(ROOT / "backups"),
                        help="Destination directory (default: ./backups)")
    parser.add_argument("--retention", default=7, type=int,
                        help="Days of backups to retain (default: 7)")
    parser.add_argument("--db-path",   default=None,
                        help="Path to SQLite DB file (auto-detected if omitted)")
    args = parser.parse_args()

    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)

    log.info("=" * 55)
    log.info("Smart Retail — Backup Started")
    log.info(f"Destination  : {dest}")
    log.info(f"Retention    : {args.retention} day(s)")
    log.info("=" * 55)

    errors = 0

    # ── 1. Database ──────────────────────────────────────────
    import os
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    db_url = os.environ.get("DATABASE_URL", "sqlite:///./supply_chain.db")
    if "sqlite" in db_url:
        # Extract file path from URL (sqlite:///./path or sqlite:////abs/path)
        db_path = Path(args.db_path) if args.db_path else Path(db_url.replace("sqlite:///", ""))
        if not db_path.is_absolute():
            db_path = ROOT / db_path
        if not backup_file(db_path, dest, "Database"):
            errors += 1
    else:
        log.info("Non-SQLite database detected — skipping file-based DB backup.")
        log.info("Use pg_dump or equivalent for PostgreSQL backups.")

    # ── 2. ML saved models ───────────────────────────────────
    if not backup_directory(ROOT / "ml" / "saved_models", dest, "ML Models"):
        errors += 1

    # ── 3. ML datasets ───────────────────────────────────────
    if not backup_directory(ROOT / "ml" / "datasets", dest, "ML Datasets"):
        errors += 1

    # ── 4. Uploads ───────────────────────────────────────────
    backup_directory(ROOT / "uploads", dest, "Uploads")  # non-critical

    # ── 5. Prune old backups ─────────────────────────────────
    prune_old_backups(dest, args.retention)

    log.info("=" * 55)
    if errors:
        log.error(f"Backup completed with {errors} error(s). Check logs above.")
        return 1
    log.info("Backup completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
