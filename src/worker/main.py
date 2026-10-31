"""Фоновое пополнение дневника: синк недель и подготовка новых ДЗ.

Отдельный процесс, бот его не запускает и не ждёт: оба смотрят в одну БД.
Запуск: cd src; python -m worker.main
Разовый синк: python -m worker.main --once
"""

import argparse
import asyncio
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import text

from db.session import engine
from net.main import sync_weeks

SYNC_INTERVAL_MINUTES = 20
WORKING_START_HOUR = 7
WORKING_END_HOUR = 22

# Ключ advisory-локи в Postgres: воркер должен быть один. Token.refresh()
# перезаписывает .env, и два процесса затрут токены друг друга.
WORKER_LOCK_KEY = 55169001


async def acquire_worker_lock():
    """Берёт блокировку в БД. Возвращает соединение или None, если воркер уже идёт.

    Advisory-лок живёт на соединении: пока оно открыто, второй экземпляр
    получает отказ. При падении процесса Postgres снимает лок сам.
    """
    connection = await engine.connect()
    locked = (await connection.execute(
        text("select pg_try_advisory_lock(:key)"),
        {"key": WORKER_LOCK_KEY},
    )).scalar()
    # session-level лок переживает commit, а соединение остаётся за нами
    await connection.commit()
    if not locked:
        await connection.close()
        return None
    return connection


def build_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    now = datetime.now()
    job_kwargs = {}
    if WORKING_START_HOUR <= now.hour <= WORKING_END_HOUR:
        # После перезапуска не ждём целый интервал.
        job_kwargs["next_run_time"] = now
    scheduler.add_job(
        sync_weeks,
        CronTrigger(
            minute=f"*/{SYNC_INTERVAL_MINUTES}",
            hour=f"{WORKING_START_HOUR}-{WORKING_END_HOUR}",
        ),
        id="sync_weeks",
        # Пока идёт один прогон, второй не начинается: синк и подготовка
        # работают с одним Token и перезаписывают .env при refresh.
        max_instances=1,
        # Если компьютер спал и тик пропущен — не догонять все пропуски,
        # а сделать один прогон.
        coalesce=True,
        misfire_grace_time=300,
        **job_kwargs,
    )
    return scheduler


async def main(once: bool = False) -> None:
    lock = await acquire_worker_lock()
    if lock is None:
        return

    scheduler = None
    try:
        if once:
            await sync_weeks()
            return

        scheduler = build_scheduler()
        scheduler.start()
        print(f"воркер запущен: синк каждые {SYNC_INTERVAL_MINUTES} мин, "
              f"часы {WORKING_START_HOUR}-{WORKING_END_HOUR}", flush=True)
        # start() не блокирует: держим цикл живым, пока процесс не остановят.
        await asyncio.Event().wait()
    finally:
        if scheduler is not None:
            scheduler.shutdown()
            print("воркер остановлен", flush=True)
        await lock.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Фоновый синк дневника")
    parser.add_argument(
        "--once", action="store_true",
        help="сделать один синк и выйти (без расписания)")
    args = parser.parse_args()
    asyncio.run(main(once=args.once))
