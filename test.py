from db.session import SessionLocal
import asyncio
from db.models import homework_files, homework_links, homework_tasks, homeworks, subjects, schedule
from sqlalchemy import select
from sqlalchemy.orm import selectinload


async def main():
    async with SessionLocal() as session:
        query = select(homeworks.Homework).options(selectinload(homeworks.Homework.homework_files), selectinload(
            homeworks.Homework.homework_links)).where(homeworks.Homework.id == 36)
        result = await session.execute(query)
        a = result.scalars().one()

        print(a.id, a.text, a.homework_files[0].url)


if __name__ == "__main__":
    asyncio.run(main())
