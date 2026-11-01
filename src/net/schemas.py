from pydantic import BaseModel
import datetime


class HomeworkLink(BaseModel):
    url: str
    title: str


class Homework(BaseModel):
    id: int
    lesson_date: datetime.date
    check_at: datetime.date
    text: str
    files: list[HomeworkLink]
    links: list[HomeworkLink]


class Lesson(BaseModel):
    lesson_event_id: int
    subject: str
    lesson_date: datetime.date
    lesson_number: int
    created_homework_id: int | None


class ScheduleResponse(BaseModel):
    schedule: list[Lesson]
    homeworks: list[Homework]
