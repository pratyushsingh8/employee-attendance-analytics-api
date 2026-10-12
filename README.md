# Employee Attendance & Analytics API

A REST API built with FastAPI and MongoDB for employee management, attendance tracking, attendance regularization, and department-level analytics.

## Requirements

- Python 3.11 or newer
- MongoDB 7.0 recommended (MongoDB 6.0+ required for the aggregation features used here)

## Local setup

Open PowerShell in the project directory and run:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Configure MongoDB using environment variables if you are not using the local default:

```powershell
$env:MONGO_URI = "mongodb://localhost:27017"
$env:MONGO_DB = "attendance_db"
```

The application defaults to `mongodb://localhost:27017` and the `attendance_db` database. It creates the required indexes at startup.

Start the API:

```powershell
uvicorn app.main:app --port 8000
```

Open `http://127.0.0.1:8000/docs` to explore the endpoints through Swagger UI. Check `GET /health` to verify API and MongoDB connectivity.

## API overview

- Employee creation and listing
- Attendance punch-in, punch-out, listing, and regularization
- Employee monthly attendance analytics
- Department summary and daily trend analytics
- Late-arrival leaderboard
- Query-plan inspection through the admin explain endpoint

The API uses epoch milliseconds for timestamps and `YYYY-MM-DD` dates for attendance records. See `openapi.yaml` and `DATA_MODEL.md` for the API contract and data model.


