from datetime import date
from pydantic import BaseModel, Field

class ReportIn(BaseModel):
    report_date: date
    title: str = Field(min_length=1, max_length=200)
    content_markdown: str = Field(min_length=1, max_length=200000)
