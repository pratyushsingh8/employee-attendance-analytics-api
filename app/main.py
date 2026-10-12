"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from decimal import Decimal, ROUND_HALF_UP

from bson import json_util
from typing import Optional, Literal
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Path
from pydantic import BaseModel, ConfigDict, StrictInt, field_validator, Field
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError, PyMongoError

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


@app.on_event("startup")
def create_indexes():
    db.employees.create_index(
        [("emp_code", ASCENDING)],
        unique=True,
        name="uq_employees_emp_code",
    )

    db.attendance_logs.create_index(
        [("emp_code", ASCENDING), ("date", ASCENDING)],
        unique=True,
        name="uq_attendance_emp_code_date",
    )

    db.employees.create_index(
        [("department", ASCENDING), ("emp_code", ASCENDING)],
        name="ix_employees_department_emp_code",
    )


    db.employees.create_index(
        [("joined_on", ASCENDING)],
        name="ix_employees_joined_on",
    )


    db.attendance_logs.create_index(
        [("date", DESCENDING), ("emp_code", ASCENDING)],
        name="ix_attendance_date_emp_code",
    )

    db.attendance_logs.create_index(
        [("status", ASCENDING), ("date", DESCENDING)],
        name="ix_attendance_status_date",
    )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def compute_late_minutes(
    punch_in: datetime,
    shift_start: str,
    shift_end: str,
) -> int:
    start_hour, start_minute = map(int, shift_start.split(":"))
    end_hour, end_minute = map(int, shift_end.split(":"))

    shift_start_dt = punch_in.replace(
        hour=start_hour,
        minute=start_minute,
        second=0,
        microsecond=0,
    )

    # For an overnight shift, an early-morning punch-in
    # belongs to the shift that started the previous day.
    if (
        (end_hour, end_minute) <= (start_hour, start_minute)
        and (punch_in.hour, punch_in.minute) < (end_hour, end_minute)
    ):
        shift_start_dt -= timedelta(days=1)

    elapsed_seconds = (punch_in - shift_start_dt).total_seconds()

    # Ten minutes of grace; exactly ten minutes is not late.
    if elapsed_seconds <= 10 * 60:
        return 0

    return int(elapsed_seconds // 60)



def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    seconds = Decimal(str((punch_out - punch_in).total_seconds()))
    hours = seconds / Decimal("3600")

    rounded_hours = hours.quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )

    return float(rounded_hours)




def compute_overtime(
    punch_out: datetime,
    shift_end: str,
    date_str: str,
    shift_start: str = "09:30",
) -> int:
    end_hour, end_minute = map(int, shift_end.split(":"))
    start_hour, start_minute = map(int, shift_start.split(":"))

    shift_start_dt = datetime.fromisoformat(date_str).replace(
        hour=start_hour,
        minute=start_minute,
        second=0,
        microsecond=0,
        tzinfo=IST,
    )

    shift_end_dt = datetime.fromisoformat(date_str).replace(
        hour=end_hour,
        minute=end_minute,
        second=0,
        microsecond=0,
        tzinfo=IST,
    )

    if shift_end_dt <= shift_start_dt:
        shift_end_dt += timedelta(days=1)

    if punch_out.tzinfo is None:
        punch_out = punch_out.replace(tzinfo=IST)

    elapsed_seconds = (punch_out - shift_end_dt).total_seconds()
    whole_minutes = int(elapsed_seconds // 60)

    if whole_minutes < 30:
        return 0

    return whole_minutes

def to_epoch_ms(value):
    if value is None:
        return None

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    return int(value.timestamp() * 1000)


def serialize_attendance(doc):
    doc.pop("_id", None)
    doc.pop("id", None)


    for field in ("punch_in", "punch_out"):
        doc[field] = to_epoch_ms(doc.get(field))

    return doc

def serialize_employee(doc):
    doc.pop("_id", None)
    doc.pop("id", None)

    if doc.get("created_at") is not None:
        doc["created_at"] = to_epoch_ms(doc["created_at"])

    return doc

# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str

    @field_validator("joined_on")
    @classmethod
    def validate_joined_on(cls, value):
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            raise ValueError("joined_on must use YYYY-MM-DD format")
        return value

    @field_validator("shift_start", "shift_end")
    @classmethod
    def validate_shift_time(cls, value):
        try:
            parsed = datetime.strptime(value, "%H:%M")
        except ValueError:
            raise ValueError("Shift times must use HH:MM format")

        if parsed.strftime("%H:%M") != value:
            raise ValueError("Shift times must use HH:MM format")

        return value


class PunchInIn(BaseModel):
    model_config = ConfigDict(strict=True)

    emp_code: str
    punched_at: Optional[StrictInt] = None
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"

class PunchOutIn(BaseModel):
    model_config = ConfigDict(strict=True)

    emp_code: str
    punched_at: Optional[StrictInt] = None



class RegularizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Optional[str] = None
    punch_in: Optional[StrictInt] = None
    punch_out: Optional[StrictInt] = None
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)



# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except PyMongoError:
        raise HTTPException(status_code=503, detail="MongoDB unavailable")



@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc)

    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="emp_code already exists",
        )

    return serialize_employee(doc)



@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    if page < 1:
        raise HTTPException(status_code=422, detail="page must be at least 1")

    if page_size < 1 or page_size > 100:
        raise HTTPException(
            status_code=422,
            detail="page_size must be between 1 and 100",
        )

    q = {}
    if department:
        q["department"] = department

    skip = (page - 1) * page_size
    total = db.employees.count_documents(q)

    items = list(
        db.employees.find(q, {"_id": 0})
        .sort("emp_code", 1)
        .skip(skip)
        .limit(page_size)
    )
    items = [serialize_employee(doc) for doc in items]

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})

    if emp is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    if body.punched_at is not None:
        ts = datetime.fromtimestamp(
            body.punched_at / 1000,
            tz=timezone.utc,
        ).astimezone(IST)
    else:
        ts = datetime.now(IST)

    ts = ts.replace(microsecond=0)

    shift_start = emp["shift_start"]
    shift_end = emp["shift_end"]

    start_hour, start_minute = map(int, shift_start.split(":"))
    end_hour, end_minute = map(int, shift_end.split(":"))

    d = ts.date().isoformat()

    # An early-morning punch-in for an overnight shift
    # belongs to the previous shift's start date.
    if (
        (end_hour, end_minute) <= (start_hour, start_minute)
        and (ts.hour, ts.minute) < (end_hour, end_minute)
    ):
        d = (ts.date() - timedelta(days=1)).isoformat()
    if db.attendance_logs.find_one({"emp_code": body.emp_code, "date": d}):
        raise HTTPException(409, "already punched in for this date")
    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(
            ts,
            shift_start,
            shift_end,
        ),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="already punched in for this date",
        )

    return serialize_attendance(doc)



@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    if page < 1:
        raise HTTPException(
            status_code=422,
            detail="page must be at least 1",
        )

    if page_size < 1 or page_size > 100:
        raise HTTPException(
            status_code=422,
            detail="page_size must be between 1 and 100",
        )

    q = {}

    if emp_code:
        q["emp_code"] = emp_code

    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to

    if status:
        q["status"] = status

    total = db.attendance_logs.count_documents(q)
    skip = (page - 1) * page_size

    page_docs = list(
        db.attendance_logs.find(q, {"_id": 0})
        .sort([("date", -1), ("emp_code", 1)])
        .skip(skip)
        .limit(page_size)
    )
    page_docs = [serialize_attendance(doc) for doc in page_docs]

    return {
        "items": page_docs,
        "total": total,
        "page": page,
        "page_size": page_size,
    }



@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})

    if emp is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    if body.punched_at is not None:
        ts = datetime.fromtimestamp(
            body.punched_at / 1000,
            tz=timezone.utc,
        ).astimezone(IST)
    else:
        ts = datetime.now(IST)

    ts = ts.replace(microsecond=0)

    # Find the most recent eligible punch-in.
    record = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_in": {
                "$ne": None,
                "$lte": ts.astimezone(timezone.utc).replace(tzinfo=None),
            },
        },
        sort=[("punch_in", -1)],
    )

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="No eligible punch-in record found",
        )

    # PyMongo may return naive datetimes; stored instants are UTC.
    punch_in_ts = record["punch_in"]
    if punch_in_ts.tzinfo is None:
        punch_in_ts = punch_in_ts.replace(tzinfo=timezone.utc)

    punch_in_ts = punch_in_ts.astimezone(IST).replace(microsecond=0)

    elapsed_seconds = (ts - punch_in_ts).total_seconds()

    if elapsed_seconds <= 0 or elapsed_seconds > 24 * 60 * 60:
        raise HTTPException(
            status_code=422,
            detail="punched_at must be after punch_in and no more than 24 hours later",
        )

    work_hours = compute_work_hours(punch_in_ts, ts)

    shift_start = emp["shift_start"]
    shift_end = emp["shift_end"]

    # For an overnight shift, an early-morning punch-in belongs
    # to the previous day's shift.
    shift_date = record["date"]
    start_hour, start_minute = map(int, shift_start.split(":"))
    end_hour, end_minute = map(int, shift_end.split(":"))

    if (
        (end_hour, end_minute) <= (start_hour, start_minute)
        and (punch_in_ts.hour, punch_in_ts.minute) < (end_hour, end_minute)
    ):
        shift_date = (
            datetime.fromisoformat(shift_date) - timedelta(days=1)
        ).date().isoformat()

    overtime_minutes = compute_overtime(
        ts,
        shift_end,
        shift_date,
        shift_start,
    )

    half_day = work_hours < 4.50

    # Atomic conditional update: only one simultaneous request can win.
    result = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            "punch_out": None,
        },
        {
            "$set": {
                "punch_out": ts,
                "work_hours": work_hours,
                "overtime_minutes": overtime_minutes,
                "half_day": half_day,
            }
        },
    )

    if result.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail="Attendance record is already punched out",
        )

    updated = db.attendance_logs.find_one({"_id": record["_id"]})
    return serialize_attendance(updated)


@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(
    emp_code: str,
    date: str,
    request: RegularizeRequest,
):
    # Validate the date format.
    try:
        parsed_date = datetime.strptime(date, "%Y-%m-%d").date()
        if parsed_date.isoformat() != date:
            raise ValueError
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="date must be in YYYY-MM-DD format",
        )

    # Find the employee.
    employee = db.employees.find_one({"emp_code": emp_code})

    if employee is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    # Find that employee's attendance record for the requested date.
    record = db.attendance_logs.find_one(
        {"emp_code": emp_code, "date": date}
    )

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="Attendance record not found",
        )


    allowed_statuses = {
        "PRESENT",
        "WFH",
        "ON_DUTY",
        "ABSENT",
        "LEAVE",
    }

    # Start with the existing values.
    old_status = record.get("status", "PRESENT")
    old_punch_in = record.get("punch_in")
    old_punch_out = record.get("punch_out")

    # Apply only fields included in the request.
    new_status = (
        request.status
        if request.status is not None
        else old_status
    )

    if new_status not in allowed_statuses:
        raise HTTPException(
            status_code=422,
            detail="Invalid attendance status",
        )

    def parse_epoch_ms(value):
        if value is None:
            return None

        return (
            datetime.fromtimestamp(value / 1000, tz=timezone.utc)
            .astimezone(IST)
            .replace(microsecond=0)
        )

    new_punch_in = (
        parse_epoch_ms(request.punch_in)
        if "punch_in" in request.model_fields_set
        else old_punch_in
    )

    new_punch_out = (
        parse_epoch_ms(request.punch_out)
        if "punch_out" in request.model_fields_set
        else old_punch_out
    )

    # ABSENT and LEAVE records cannot have punch times.
    if new_status in {"ABSENT", "LEAVE"}:
        if (
            "punch_in" in request.model_fields_set
            or "punch_out" in request.model_fields_set
        ):

            raise HTTPException(
                status_code=422,
                detail="ABSENT and LEAVE cannot include punch times",
            )

        new_punch_in = None
        new_punch_out = None

    else:
        # A presence status requires a punch-in.
        if new_punch_in is None:
            raise HTTPException(
                status_code=422,
                detail="A presence status requires punch_in",
            )



        # Punch-out must follow punch-in and be within 24 hours.
        if new_punch_out is not None:
            elapsed = (new_punch_out - new_punch_in).total_seconds()

            if elapsed <= 0 or elapsed > 24 * 60 * 60:
                raise HTTPException(
                    status_code=422,
                    detail="punch_out must follow punch_in within 24 hours",
                )

    # Convert stored MongoDB datetimes to IST.
    # MongoDB may return naive datetimes representing UTC.
    def as_ist(value):
        if value is None:
            return None

        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)

        return value.astimezone(IST).replace(microsecond=0)

    old_punch_in = as_ist(old_punch_in)
    old_punch_out = as_ist(old_punch_out)

    new_punch_in = as_ist(new_punch_in)
    new_punch_out = as_ist(new_punch_out)

    # Determine the shift's attendance date.
    shift_start = employee.get("shift_start", "09:30")
    shift_end = employee.get("shift_end", "18:30")

    start_hour, start_minute = map(int, shift_start.split(":"))
    end_hour, end_minute = map(int, shift_end.split(":"))

    is_overnight = (end_hour, end_minute) <= (
        start_hour,
        start_minute,
    )

    if new_punch_in is not None:
        expected_date = new_punch_in.date()

        if (
            is_overnight
            and (new_punch_in.hour, new_punch_in.minute)
            < (end_hour, end_minute)
        ):
            expected_date -= timedelta(days=1)

        if expected_date.isoformat() != date:
            raise HTTPException(
                status_code=422,
                detail="punch_in does not belong to this attendance date",
            )

    # Calculate the final derived values.
    if new_punch_in is None:
        new_late_minutes = 0
    else:

        new_late_minutes = compute_late_minutes(
            new_punch_in,
            shift_start,
            shift_end,
        )

    if new_punch_in is None or new_punch_out is None:
        new_work_hours = None
    else:
        new_work_hours = compute_work_hours(
            new_punch_in,
            new_punch_out,
        )

    if new_punch_out is None:
        new_overtime_minutes = 0
    else:
        new_overtime_minutes = compute_overtime(
            new_punch_out,
            shift_end,
            date,
            shift_start,
        )

    new_half_day = (
        new_work_hours is not None
        and new_work_hours < 4.50
    )

    # Compare the original and final values.
    changes = {}

    def epoch_ms(value):
        if value is None:
            return None

        return int(value.timestamp() * 1000)

    old_values = {
        "status": old_status,
        "punch_in": epoch_ms(old_punch_in),
        "punch_out": epoch_ms(old_punch_out),
        "work_hours": record.get("work_hours"),
        "late_minutes": record.get("late_minutes", 0),
        "overtime_minutes": record.get("overtime_minutes", 0),
        "half_day": record.get("half_day", False),
    }

    new_values = {
        "status": new_status,
        "punch_in": epoch_ms(new_punch_in),
        "punch_out": epoch_ms(new_punch_out),
        "work_hours": new_work_hours,
        "late_minutes": new_late_minutes,
        "overtime_minutes": new_overtime_minutes,
        "half_day": new_half_day,
    }

    for field, new_value in new_values.items():
        old_value = old_values[field]

        if old_value != new_value:
            changes[field] = {
                "from": old_value,
                "to": new_value,
            }

    if not changes:
        raise HTTPException(
            status_code=422,
            detail="The request does not change the attendance record",
        )


    # Build the audit-history entry.
    now_utc = datetime.now(timezone.utc).replace(microsecond=0)

    history_entry = {
        "at": int(now_utc.timestamp() * 1000),
        "by": request.regularized_by,
        "reason": request.reason,
        "changes": changes,
    }

    # Convert corrected timestamps to UTC for MongoDB storage.
    punch_in_utc = (
        new_punch_in.astimezone(timezone.utc)
        if new_punch_in is not None
        else None
    )

    punch_out_utc = (
        new_punch_out.astimezone(timezone.utc)
        if new_punch_out is not None
        else None
    )

    # Match the original record to prevent lost concurrent corrections.
    update_filter = {
        "_id": record["_id"],
        "status": record.get("status", "PRESENT"),
        "punch_in": record.get("punch_in"),
        "punch_out": record.get("punch_out"),
        "work_hours": record.get("work_hours"),
        "late_minutes": record.get("late_minutes", 0),
        "overtime_minutes": record.get("overtime_minutes", 0),
        "half_day": record.get("half_day", False),
    }

    # Preserve the exact history state observed when we read the record.
    if "history" in record:
        update_filter["history"] = record["history"]
    else:
        update_filter["history"] = {"$exists": False}

    update_result = db.attendance_logs.update_one(
        update_filter,
        {
            "$set": {
                "status": new_status,
                "punch_in": punch_in_utc,
                "punch_out": punch_out_utc,
                "work_hours": new_work_hours,
                "late_minutes": new_late_minutes,
                "overtime_minutes": new_overtime_minutes,
                "half_day": new_half_day,
            },
            "$push": {
                "history": history_entry,
            },
        },
    )

    if update_result.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail="Attendance record changed concurrently; retry the correction",
        )

    # Return the saved record without MongoDB's internal _id.
    updated_record = db.attendance_logs.find_one(
        {"_id": record["_id"]}
    )

    updated_record.pop("_id", None)

    # Return timestamps as epoch milliseconds, as required by the API.
    for field in ("punch_in", "punch_out"):
        value = updated_record.get(field)

        if value is not None:
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)

            updated_record[field] = int(
                value.timestamp() * 1000
            )

    updated_record["history"] = [
        {
            "at": entry["at"],
            "by": entry["by"],
            "reason": entry["reason"],
            "changes": entry["changes"],
        }
        for entry in updated_record.get("history", [])
    ]

    return updated_record


def build_employee_monthly_pipeline(
    emp_code: str,
    month_start,
    next_month_start,
    joined_on,
):
    first_date = max(month_start, joined_on)

    return [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {
                    "$gte": first_date.isoformat(),
                    "$lt": next_month_start.isoformat(),
                },
            }
        },
        {
            "$group": {
                "_id": None,
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$in": [
                                            "$status",
                                            ["PRESENT", "WFH", "ON_DUTY"],
                                        ]
                                    },
                                    {
                                        "$gte": [
                                            {
                                                "$dayOfWeek": {
                                                    "$dateFromString": {
                                                        "dateString": "$date",
                                                        "format": "%Y-%m-%d",
                                                    }
                                                }
                                            },
                                            2,
                                        ]
                                    },
                                    {
                                        "$lte": [
                                            {
                                                "$dayOfWeek": {
                                                    "$dateFromString": {
                                                        "dateString": "$date",
                                                        "format": "%Y-%m-%d",
                                                    }
                                                }
                                            },
                                            6,
                                        ]
                                    },
                                ]
                            },
                            {
                                "$cond": [
                                    {
                                        "$eq": [
                                            {"$ifNull": ["$half_day", False]},
                                            True,
                                        ]
                                    },
                                    0.5,
                                    1,
                                ]
                            },
                            0,
                        ]
                    }
                },
                "leave_days": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$status", "LEAVE"]},
                            1,
                            0,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": ["$late_minutes", 0]},
                            1,
                            0,
                        ]
                    }
                },
                "total_late_minutes": {
                    "$sum": {"$ifNull": ["$late_minutes", 0]}
                },
                "total_overtime_minutes": {
                    "$sum": {"$ifNull": ["$overtime_minutes", 0]}
                },
            }
        },
    ]



@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str):
    # Validate month format: YYYY-MM
    try:
        parsed_month = datetime.strptime(month, "%Y-%m")
        if parsed_month.strftime("%Y-%m") != month:
            raise ValueError
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="month must use YYYY-MM format",
        )

    # Check employee existence first.
    employee = db.employees.find_one(
        {"emp_code": emp_code},
        {"_id": 0, "emp_code": 1, "joined_on": 1},
    )

    if employee is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    year = parsed_month.year
    month_number = parsed_month.month

    month_start = datetime(year, month_number, 1).date()

    if month_number == 12:
        next_month_start = datetime(year + 1, 1, 1).date()
    else:
        next_month_start = datetime(
            year, month_number + 1, 1
        ).date()

    month_end = next_month_start - timedelta(days=1)

    joined_on = datetime.strptime(
        employee["joined_on"], "%Y-%m-%d"
    ).date()

    # Count Monday-Friday working days from joined_on onward.
    first_working_date = max(month_start, joined_on)

    working_days = 0
    current_date = first_working_date

    while current_date <= month_end:
        if current_date.weekday() < 5:
            working_days += 1
        current_date += timedelta(days=1)

    # Aggregate attendance metrics in MongoDB.
    # Build the shared MongoDB aggregation pipeline.
    pipeline = build_employee_monthly_pipeline(
        emp_code,
        month_start,
        next_month_start,
        joined_on,
    )

    results = list(db.attendance_logs.aggregate(pipeline))
    metrics = results[0] if results else {}

    present_days = metrics.get("present_days", 0)

    attendance_pct = (
        float(
            (
                Decimal(str(present_days))
                / Decimal(working_days)
                * Decimal("100")
            ).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            )
        )
        if working_days > 0
        else None
    )


    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": metrics.get("leave_days", 0),
        "late_count": metrics.get("late_count", 0),
        "total_late_minutes": metrics.get("total_late_minutes", 0),
        "total_overtime_minutes": metrics.get(
            "total_overtime_minutes", 0
        ),
        "attendance_pct": attendance_pct,
    }


def build_department_summary_pipeline(
    month_start_str: str,
    next_month_start_str: str,
    month_end_str: str,
    department: Optional[str] = None,
):
    employee_match = {
        "joined_on": {"$lte": month_end_str}
    }

    if department is not None:
        employee_match["department"] = department

    weekday_expression = {
        "$dayOfWeek": {
            "$dateFromString": {
                "dateString": "$attendance.date",
                "format": "%Y-%m-%d",
            }
        }
    }

    present_status_expression = {
        "$in": [
            "$attendance.status",
            ["PRESENT", "WFH", "ON_DUTY"],
        ]
    }

    attendance_exists_expression = {
        "$ne": [
            {"$ifNull": ["$attendance.date", None]},
            None,
        ]
    }

    present_weekday_expression = {
        "$and": [
            attendance_exists_expression,
            present_status_expression,
            {"$gte": [weekday_expression, 2]},
            {"$lte": [weekday_expression, 6]},
        ]
    }

    valid_work_hours_expression = {
        "$and": [
            attendance_exists_expression,
            present_status_expression,
            {
                "$ne": [
                    {"$ifNull": ["$attendance.work_hours", None]},
                    None,
                ]
            },
        ]
    }

    pipeline = [
        {"$match": employee_match},
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {
                    "employee_code": "$emp_code",
                    "employee_joined_on": "$joined_on",
                },
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$emp_code", "$$employee_code"]},
                                    {"$gte": ["$date", month_start_str]},
                                    {"$lt": ["$date", next_month_start_str]},
                                    {"$gte": ["$date", "$$employee_joined_on"]},
                                ]
                            }
                        }
                    }
                ],
                "as": "attendance",
            }
        },
        {
            "$unwind": {
                "path": "$attendance",
                "preserveNullAndEmptyArrays": True,
            }
        },
        {
            "$group": {
                "_id": {
                    "department": "$department",
                    "emp_code": "$emp_code",
                },
                "headcount": {"$first": 1},
                "present_days": {
                    "$sum": {
                        "$cond": [
                            present_weekday_expression,
                            {
                                "$cond": [
                                    {
                                        "$eq": [
                                            {
                                                "$ifNull": [
                                                    "$attendance.half_day",
                                                    False,
                                                ]
                                            },
                                            True,
                                        ]
                                    },
                                    0.5,
                                    1,
                                ]
                            },
                            0,
                        ]
                    }
                },
                "work_hours_sum": {
                    "$sum": {
                        "$cond": [
                            valid_work_hours_expression,
                            "$attendance.work_hours",
                            0,
                        ]
                    }
                },
                "work_hours_count": {
                    "$sum": {
                        "$cond": [
                            valid_work_hours_expression,
                            1,
                            0,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    attendance_exists_expression,
                                    {"$gt": ["$attendance.late_minutes", 0]},
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
                "total_late_minutes": {
                    "$sum": {
                        "$cond": [
                            attendance_exists_expression,
                            {"$ifNull": ["$attendance.late_minutes", 0]},
                            0,
                        ]
                    }
                },
                "leave_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    attendance_exists_expression,
                                    {"$eq": ["$attendance.status", "LEAVE"]},
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
                "on_duty_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    attendance_exists_expression,
                                    {"$eq": ["$attendance.status", "ON_DUTY"]},
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
            }
        },
        {
            "$group": {
                "_id": "$_id.department",
                "headcount": {"$sum": "$headcount"},
                "present_days": {"$sum": "$present_days"},
                "work_hours_sum": {"$sum": "$work_hours_sum"},
                "work_hours_count": {"$sum": "$work_hours_count"},
                "late_count": {"$sum": "$late_count"},
                "total_late_minutes": {"$sum": "$total_late_minutes"},
                "leave_count": {"$sum": "$leave_count"},
                "on_duty_count": {"$sum": "$on_duty_count"},
            }
        },
        {"$sort": {"_id": 1}},
    ]

    return pipeline


@app.get("/analytics/departments/summary")
def department_summary(
    month: str,
    department: Optional[str] = None,
):
    # Validate the YYYY-MM month parameter.
    try:
        parsed_month = datetime.strptime(month, "%Y-%m")
        if parsed_month.strftime("%Y-%m") != month:
            raise ValueError
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="month must use YYYY-MM format",
        )

    year = parsed_month.year
    month_number = parsed_month.month

    month_start = datetime(year, month_number, 1).date()

    if month_number == 12:
        next_month_start = datetime(year + 1, 1, 1).date()
    else:
        next_month_start = datetime(
            year, month_number + 1, 1
        ).date()

    month_start_str = month_start.isoformat()
    next_month_start_str = next_month_start.isoformat()


    month_end_str = (next_month_start - timedelta(days=1)).isoformat()

    pipeline = build_department_summary_pipeline(
        month_start_str=month_start_str,
        next_month_start_str=next_month_start_str,
        month_end_str=month_end_str,
        department=department,
    )


    results = list(db.employees.aggregate(pipeline))

    items = []

    for result in results:
        # R8: report values use round-half-up to two decimal places.
        work_hours_count = result["work_hours_count"]

        if work_hours_count:
            avg_work_hours = float(
                (
                    Decimal(str(result["work_hours_sum"]))
                    / Decimal(work_hours_count)
                ).quantize(
                    Decimal("0.01"),
                    rounding=ROUND_HALF_UP,
                )
            )
        else:
            avg_work_hours = None

        present_days = float(result["present_days"])
        if present_days.is_integer():
            present_days = int(present_days)

        items.append(
            {
                "department": result["_id"],
                "headcount": result["headcount"],
                "present_days": present_days,
                "avg_work_hours": avg_work_hours,
                "late_count": result["late_count"],
                "total_late_minutes": result["total_late_minutes"],
                "leave_count": result["leave_count"],
                "on_duty_count": result["on_duty_count"],
            }
        )

    return {
        "month": month,
        "items": items,
    }


def build_late_leaderboard_pipeline(
    month_start_str: str,
    next_month_start_str: str,
):
    return [
        {
            "$match": {
                "date": {
                    "$gte": month_start_str,
                    "$lt": next_month_start_str,
                },
                "late_minutes": {"$gt": 0},
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
            }
        },
        {
            "$match": {
                "total_late_minutes": {"$gt": 0},
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "employee",
            }
        },
        {"$unwind": "$employee"},
    ]


@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str,
    limit: int = 10,
    department: Optional[str] = None,
):
    # Validate month format.
    try:
        parsed_month = datetime.strptime(month, "%Y-%m")
        if parsed_month.strftime("%Y-%m") != month:
            raise ValueError
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="month must use YYYY-MM format",
        )

    # Validate limit.
    if limit < 1 or limit > 50:
        raise HTTPException(
            status_code=422,
            detail="limit must be between 1 and 50",
        )

    year = parsed_month.year
    month_number = parsed_month.month

    month_start = datetime(year, month_number, 1).date()

    if month_number == 12:
        next_month_start = datetime(year + 1, 1, 1).date()
    else:
        next_month_start = datetime(
            year, month_number + 1, 1
        ).date()

    month_start_str = month_start.isoformat()
    next_month_start_str = next_month_start.isoformat()

    # Aggregate attendance by employee, then join employee details.

    pipeline = build_late_leaderboard_pipeline(
        month_start_str=month_start_str,
        next_month_start_str=next_month_start_str,
    )

    # If department is supplied, apply it before ranking.
    if department is not None:
        pipeline.append(
            {
                "$match": {
                    "employee.department": department,
                }
            }
        )

    pipeline.extend(
        [
            {
                "$project": {
                    "_id": 0,
                    "emp_code": "$_id",
                    "name": "$employee.name",
                    "department": "$employee.department",
                    "total_late_minutes": 1,
                }
            },
            {
                "$sort": {
                    "total_late_minutes": -1,
                    "emp_code": 1,
                }
            },
            {
                "$setWindowFields": {
                    "sortBy": {
                        "total_late_minutes": -1,
                    },
                    "output": {
                        "rank": {"$rank": {}},
                    },
                }
            },
            {"$match": {"rank": {"$lte": limit}}},
            {
                "$sort": {
                    "total_late_minutes": -1,
                    "emp_code": 1,
                }
            },
        ]
    )

    items = list(db.attendance_logs.aggregate(pipeline))

    return {
        "month": month,
        "items": items,
    }


def build_department_trend_pipeline(
    department: str,
    start_datetime: datetime,
    end_datetime: datetime,
):
    return [
        {"$documents": [{"date": start_datetime}]},
        {
            "$densify": {
                "field": "date",
                "range": {
                    "step": 1,
                    "unit": "day",
                    "bounds": [start_datetime, end_datetime],
                },
            }
        },
        {
            "$addFields": {
                "date_string": {
                    "$dateToString": {
                        "date": "$date",
                        "format": "%Y-%m-%d",
                        "timezone": "UTC",
                    }
                },
                "day_of_week": {"$dayOfWeek": "$date"},
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "let": {"current_date": "$date_string"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$department", department]},
                                    {"$lte": ["$joined_on", "$$current_date"]},
                                ]
                            }
                        }
                    },
                    {"$project": {"_id": 0, "emp_code": 1}},
                ],
                "as": "eligible_employees",
            }
        },
        {
            "$addFields": {
                "headcount": {"$size": "$eligible_employees"},
                "eligible_codes": {
                    "$map": {
                        "input": "$eligible_employees",
                        "as": "employee",
                        "in": "$$employee.emp_code",
                    }
                },
                "is_working_day": {
                    "$and": [
                        {"$gte": ["$day_of_week", 2]},
                        {"$lte": ["$day_of_week", 6]},
                    ]
                },
            }
        },
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {
                    "current_date": "$date_string",
                    "codes": "$eligible_codes",
                },
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$date", "$$current_date"]},
                                    {"$in": ["$emp_code", "$$codes"]},
                                ]
                            }
                        }
                    },
                    {
                        "$project": {
                            "_id": 0,
                            "status": 1,
                            "half_day": 1,
                            "late_minutes": 1,
                        }
                    },
                ],
                "as": "daily_logs",
            }
        },
        {
            "$addFields": {
                "present_count": {
                    "$reduce": {
                        "input": "$daily_logs",
                        "initialValue": 0,
                        "in": {
                            "$add": [
                                "$$value",
                                {
                                    "$cond": [
                                        {
                                            "$in": [
                                                "$$this.status",
                                                ["PRESENT", "WFH", "ON_DUTY"],
                                            ]
                                        },
                                        {
                                            "$cond": [
                                                {
                                                    "$eq": [
                                                        {
                                                            "$ifNull": [
                                                                "$$this.half_day",
                                                                False,
                                                            ]
                                                        },
                                                        True,
                                                    ]
                                                },
                                                0.5,
                                                1,
                                            ]
                                        },
                                        0,
                                    ]
                                },
                            ]
                        },
                    }
                },
                "late_count": {
                    "$size": {
                        "$filter": {
                            "input": "$daily_logs",
                            "as": "log",
                            "cond": {
                                "$gt": [
                                    {"$ifNull": ["$$log.late_minutes", 0]},
                                    0,
                                ]
                            },
                        }
                    }
                },
            }
        },
        {
            "$addFields": {
                "raw_rate": {
                    "$cond": [
                        {
                            "$and": [
                                "$is_working_day",
                                {"$gt": ["$headcount", 0]},
                            ]
                        },
                        {"$divide": ["$present_count", "$headcount"]},
                        None,
                    ]
                }
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"date": 1},
                "output": {
                    "raw_moving_avg": {
                        "$avg": "$raw_rate",
                        "window": {"documents": [-6, 0]},
                    }
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": "$date_string",
                "is_working_day": 1,
                "headcount": 1,
                "present_count": 1,
                "late_count": 1,
                "raw_rate": 1,
                "raw_moving_avg": 1,
            }
        },
        {"$sort": {"date": 1}},
    ]



@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_date: str = Query(..., alias="from"),
    to_date: str = Query(..., alias="to"),
):
    # Validate the two ISO calendar dates.
    try:
        start_date = datetime.strptime(from_date, "%Y-%m-%d").date()
        end_date = datetime.strptime(to_date, "%Y-%m-%d").date()

        if start_date.isoformat() != from_date:
            raise ValueError
        if end_date.isoformat() != to_date:
            raise ValueError
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="from and to must use YYYY-MM-DD format",
        )

    if end_date < start_date:
        raise HTTPException(
            status_code=422,
            detail="to must be on or after from",
        )

    if (end_date - start_date).days + 1 > 92:
        raise HTTPException(
            status_code=422,
            detail="date range must not exceed 92 days",
        )

    # An unknown department must return 404.
    if db.employees.count_documents({"department": department}) == 0:
        raise HTTPException(
            status_code=404,
            detail="Department not found",
        )

    start_datetime = datetime.combine(
        start_date, datetime.min.time(), tzinfo=timezone.utc
    )
    end_datetime = datetime.combine(
        end_date + timedelta(days=1),
        datetime.min.time(),
        tzinfo=timezone.utc,
    )


    pipeline = build_department_trend_pipeline(
        department=department,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
    )


    results = list(db.aggregate(pipeline))

    # Apply required half-up rounding to four decimal places.
    def round_four(value):
        if value is None:
            return None
        return float(
            Decimal(str(value)).quantize(
                Decimal("0.0001"),
                rounding=ROUND_HALF_UP,
            )
        )

    items = []
    for result in results:
        items.append(
            {
                "date": result["date"],
                "is_working_day": result["is_working_day"],
                "headcount": result["headcount"],
                "present_count": result["present_count"],
                "late_count": result["late_count"],
                "attendance_rate": round_four(result["raw_rate"]),
                "moving_avg_7d": round_four(result["raw_moving_avg"]),
            }
        )

    return {
        "department": department,
        "items": items,
    }



@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: Literal[
        "attendance_list",
        "employee_monthly",
        "department_summary",
        "late_leaderboard",
        "department_trend",
    ] = Path(...),
    emp_code: Optional[str] = None,
    month: Optional[str] = None,
    department: Optional[str] = None,
    limit: int = Query(10, ge=1, le=50),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """
    Return MongoDB executionStats for an endpoint's main query.
    """


    if endpoint == "employee_monthly":
        # These parameters are required for monthly analytics.
        if not emp_code or not month:
            raise HTTPException(
                status_code=422,
                detail="emp_code and month are required for employee_monthly",
            )

        # Validate month format.
        try:
            parsed_month = datetime.strptime(month, "%Y-%m")
            if parsed_month.strftime("%Y-%m") != month:
                raise ValueError
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="month must use YYYY-MM format",
            )

        # Use the same employee lookup as the real monthly endpoint.
        employee = db.employees.find_one(
            {"emp_code": emp_code},
            {"_id": 0, "emp_code": 1, "joined_on": 1},
        )

        if employee is None:
            raise HTTPException(
                status_code=404,
                detail="Employee not found",
            )

        # Calculate the same month boundaries.
        year = parsed_month.year
        month_number = parsed_month.month

        month_start = datetime(year, month_number, 1).date()

        if month_number == 12:
            next_month_start = datetime(year + 1, 1, 1).date()
        else:
            next_month_start = datetime(
                year, month_number + 1, 1
            ).date()

        joined_on = datetime.strptime(
            employee["joined_on"], "%Y-%m-%d"
        ).date()

        # Reuse the exact pipeline from the real endpoint.
        pipeline = build_employee_monthly_pipeline(
            emp_code,
            month_start,
            next_month_start,
            joined_on,
        )

        explain_result = db.command(
            "explain",
            {
                "aggregate": "attendance_logs",
                "pipeline": pipeline,
                "cursor": {},
            },
            verbosity="executionStats",
        )

        explain_json = json_util.loads(
            json_util.dumps(explain_result)
        )

        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": explain_json,
        }


    if endpoint == "department_summary":
        if month is None:
            raise HTTPException(
                status_code=422,
                detail="month is required for department_summary",
            )

        try:
            parsed_month = datetime.strptime(month, "%Y-%m")
            if parsed_month.strftime("%Y-%m") != month:
                raise ValueError
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="month must use YYYY-MM format",
            )

        year = parsed_month.year
        month_number = parsed_month.month
        month_start = datetime(year, month_number, 1).date()

        if month_number == 12:
            next_month_start = datetime(year + 1, 1, 1).date()
        else:
            next_month_start = datetime(
                year, month_number + 1, 1
            ).date()

        month_start_str = month_start.isoformat()
        next_month_start_str = next_month_start.isoformat()
        month_end_str = (
            next_month_start - timedelta(days=1)
        ).isoformat()

        pipeline = build_department_summary_pipeline(
            month_start_str=month_start_str,
            next_month_start_str=next_month_start_str,
            month_end_str=month_end_str,
            department=department,
        )

        explain_result = db.command(
            "explain",
            {
                "aggregate": "employees",
                "pipeline": pipeline,
                "cursor": {},
            },
            verbosity="executionStats",
        )

        explain_json = json_util.loads(json_util.dumps(explain_result))

        return {
            "endpoint": endpoint,
            "collection": "employees",
            "explain": explain_json,
        }


    if endpoint == "late_leaderboard":
        if month is None:
            raise HTTPException(
                status_code=422,
                detail="month is required for late_leaderboard",
            )

        try:
            parsed_month = datetime.strptime(month, "%Y-%m")
            if parsed_month.strftime("%Y-%m") != month:
                raise ValueError
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="month must use YYYY-MM format",
            )

        year = parsed_month.year
        month_number = parsed_month.month
        month_start = datetime(year, month_number, 1).date()

        if month_number == 12:
            next_month_start = datetime(year + 1, 1, 1).date()
        else:
            next_month_start = datetime(
                year, month_number + 1, 1
            ).date()

        pipeline = build_late_leaderboard_pipeline(
            month_start_str=month_start.isoformat(),
            next_month_start_str=next_month_start.isoformat(),
        )

        explain_result = db.command(
            "explain",
            {
                "aggregate": "attendance_logs",
                "pipeline": pipeline,
                "cursor": {},
            },
            verbosity="executionStats",
        )

        explain_json = json_util.loads(json_util.dumps(explain_result))

        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": explain_json,
        }



    if endpoint == "department_trend":
        if not from_date or not to_date:
            raise HTTPException(
                status_code=422,
                detail="from and to dates are required",
            )

        try:
            start_date = datetime.strptime(
                from_date, "%Y-%m-%d"
            ).date()
            end_date = datetime.strptime(
                to_date, "%Y-%m-%d"
            ).date()
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="Dates must use YYYY-MM-DD format",
            )

        if start_date.isoformat() != from_date:
            raise HTTPException(
                status_code=422,
                detail="from must use YYYY-MM-DD format",
            )

        if end_date.isoformat() != to_date:
            raise HTTPException(
                status_code=422,
                detail="to must use YYYY-MM-DD format",
            )

        if start_date > end_date:
            raise HTTPException(
                status_code=422,
                detail="from date cannot be after to date",
            )

        if (end_date - start_date).days > 92:
            raise HTTPException(
                status_code=422,
                detail="Date range cannot exceed 92 days",
            )

        if not db.employees.find_one(
            {"department": department},
            {"_id": 1},
        ):
            raise HTTPException(
                status_code=404,
                detail="Department not found",
            )

        start_datetime = datetime.combine(
            start_date, datetime.min.time()
        ).replace(tzinfo=timezone.utc)

        end_datetime = datetime.combine(
            end_date + timedelta(days=1),
            datetime.min.time(),
        ).replace(tzinfo=timezone.utc)

        pipeline = build_department_trend_pipeline(
            department=department,
            start_datetime=start_datetime,
            end_datetime=end_datetime,
        )

        explain_result = db.command(
            "explain",
            {
                "aggregate": 1,
                "pipeline": pipeline,
                "cursor": {},
            },
            verbosity="executionStats",
        )

        explain_json = json_util.loads(
            json_util.dumps(explain_result)
        )

        return {
            "endpoint": endpoint,
            "collection": "employees",
            "explain": explain_json,
        }

    if endpoint != "attendance_list":
        raise HTTPException(
            status_code=501,
            detail="This explain branch is not implemented yet",
        )



    query = {}

    if emp_code:
        query["emp_code"] = emp_code

    if date_from or date_to:
        date_filter = {}

        if date_from:
            try:
                parsed_from = datetime.strptime(
                    date_from, "%Y-%m-%d"
                ).date()
                if parsed_from.isoformat() != date_from:
                    raise ValueError
            except ValueError:
                raise HTTPException(
                    status_code=422,
                    detail="date_from must use YYYY-MM-DD format",
                )

            date_filter["$gte"] = date_from

        if date_to:
            try:
                parsed_to = datetime.strptime(
                    date_to, "%Y-%m-%d"
                ).date()
                if parsed_to.isoformat() != date_to:
                    raise ValueError
            except ValueError:
                raise HTTPException(
                    status_code=422,
                    detail="date_to must use YYYY-MM-DD format",
                )

            date_filter["$lte"] = date_to

        if (
            date_from
            and date_to
            and date_from > date_to
        ):
            raise HTTPException(
                status_code=422,
                detail="date_from must be on or before date_to",
            )

        query["date"] = date_filter

    if status:
        allowed_statuses = {
            "PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"
        }
        if status not in allowed_statuses:
            raise HTTPException(
                status_code=422,
                detail="Invalid attendance status",
            )
        query["status"] = status

    skip = (page - 1) * page_size

    explain_result = db.command(
        "explain",
        {
            "find": "attendance_logs",
            "filter": query,
            "sort": {"date": -1, "emp_code": 1},
            "skip": skip,
            "limit": page_size,
            "projection": {"_id": 0},
        },
        verbosity="executionStats",
    )

    # Convert BSON-specific values to ordinary JSON-compatible values.
    explain_json = json_util.loads(json_util.dumps(explain_result))

    return {
        "endpoint": endpoint,
        "collection": "attendance_logs",
        "explain": explain_json,
    }




# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# TODO - remaining contract work (see openapi.yaml):
#   GET /admin/explain/{endpoint}
#   Final contract review and concurrency testing
# --------------------------------------------------------------------------- #
