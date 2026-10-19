"""Operating calendar. Administrative close timestamps never assign payments."""
from datetime import timedelta

def operational_day(day):
 while day.weekday()>4:day-=timedelta(days=1)
 return day

def previous_day(day):return operational_day(day-timedelta(days=1))

def is_operational(day):return day.weekday()<5
