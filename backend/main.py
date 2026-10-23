
from pathlib import Path
import sqlite3

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "hostel.db"
SCHEMA_PATH = BASE_DIR.parent / "database" / "schema.sql"

app = FastAPI(title="Walchand Hostel Management")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Local college project only
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def initialize_db():
    with get_db() as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


@app.on_event("startup")
def startup():
    initialize_db()


class StudentInput(BaseModel):
    full_name: str
    roll_number: str
    email: str
    phone: str = ""


class BookingInput(BaseModel):
    student_id: int
    room_id: int


class ComplaintInput(BaseModel):
    student_id: int
    category: str
    description: str


class StatusInput(BaseModel):
    status: str


@app.get("/")
def home():
    return {"message": "Walchand Hostel API is running"}


@app.get("/rooms")
def list_rooms():
    with get_db() as conn:
        rows = conn.execute("""
            SELECT r.room_id, h.hostel_name, r.room_number,
                   r.capacity, r.monthly_fee,
                   r.capacity - (
                       SELECT COUNT(*) FROM bookings b
                       WHERE b.room_id = r.room_id
                         AND b.status = 'approved'
                   ) AS available
            FROM rooms r
            JOIN hostels h ON h.hostel_id = r.hostel_id
            ORDER BY r.room_id
        """).fetchall()
        return [dict(row) for row in rows]


@app.post("/students")
def create_student(student: StudentInput):
    try:
        with get_db() as conn:
            cur = conn.execute("""
                INSERT INTO students
                    (full_name, roll_number, email, phone)
                VALUES (?, ?, ?, ?)
            """, (
                student.full_name, student.roll_number,
                student.email, student.phone
            ))
            return {"student_id": cur.lastrowid,
                    "message": "Student registered"}
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=400,
            detail="Duplicate roll number/email or invalid student data"
        ) from exc


@app.post("/bookings")
def create_booking(booking: BookingInput):
    with get_db() as conn:
        student = conn.execute(
            "SELECT student_id FROM students WHERE student_id = ?",
            (booking.student_id,)
        ).fetchone()

        room = conn.execute(
            "SELECT capacity FROM rooms WHERE room_id = ?",
            (booking.room_id,)
        ).fetchone()

        if not student or not room:
            raise HTTPException(
                status_code=404, detail="Student or room not found"
            )

        existing = conn.execute("""
            SELECT 1 FROM bookings
            WHERE student_id = ? AND status IN ('pending', 'approved')
        """, (booking.student_id,)).fetchone()

        if existing:
            raise HTTPException(
                status_code=400,
                detail="Student already has an active booking request"
            )

        occupied = conn.execute("""
            SELECT COUNT(*) AS n FROM bookings
            WHERE room_id = ? AND status = 'approved'
        """, (booking.room_id,)).fetchone()["n"]

        pending = conn.execute("""
            SELECT COUNT(*) AS n FROM bookings
            WHERE room_id = ? AND status = 'pending'
        """, (booking.room_id,)).fetchone()["n"]

        if occupied + pending >= room["capacity"]:
            raise HTTPException(
                status_code=400,
                detail="Room is full or has pending requests"
            )

        cur = conn.execute("""
            INSERT INTO bookings (student_id, room_id)
            VALUES (?, ?)
        """, (booking.student_id, booking.room_id))

        return {"booking_id": cur.lastrowid,
                "status": "pending",
                "message": "Booking request submitted"}


@app.get("/bookings")
def list_bookings():
    with get_db() as conn:
        rows = conn.execute("""
            SELECT b.booking_id, s.full_name, s.roll_number,
                   r.room_number, b.booking_date, b.status
            FROM bookings b
            JOIN students s ON s.student_id = b.student_id
            JOIN rooms r ON r.room_id = b.room_id
            ORDER BY b.booking_id DESC
        """).fetchall()
        return [dict(row) for row in rows]


@app.patch("/bookings/{booking_id}")
def update_booking(booking_id: int, data: StatusInput):
    if data.status not in ("approved", "cancelled"):
        raise HTTPException(
            status_code=400,
            detail="Status must be approved or cancelled"
        )

    with get_db() as conn:
        booking = conn.execute("""
            SELECT b.*, r.capacity
            FROM bookings b JOIN rooms r ON r.room_id = b.room_id
            WHERE b.booking_id = ?
        """, (booking_id,)).fetchone()

        if not booking:
            raise HTTPException(status_code=404, detail="Booking not found")

        if booking["status"] != "pending":
            raise HTTPException(
                status_code=400, detail="Only pending bookings can be updated"
            )

        if data.status == "approved":
            count = conn.execute("""
                SELECT COUNT(*) AS n FROM bookings
                WHERE room_id = ? AND status = 'approved'
            """, (booking["room_id"],)).fetchone()["n"]

            if count >= booking["capacity"]:
                raise HTTPException(
                    status_code=400, detail="Room has no available capacity"
                )

        conn.execute(
            "UPDATE bookings SET status = ? WHERE booking_id = ?",
            (data.status, booking_id)
        )
        return {"message": "Booking updated", "status": data.status}


@app.post("/complaints")
def create_complaint(complaint: ComplaintInput):
    with get_db() as conn:
        student = conn.execute(
            "SELECT student_id FROM students WHERE student_id = ?",
            (complaint.student_id,)
        ).fetchone()

        if not student:
            raise HTTPException(status_code=404, detail="Student not found")

        cur = conn.execute("""
            INSERT INTO complaints (student_id, category, description)
            VALUES (?, ?, ?)
        """, (
            complaint.student_id, complaint.category,
            complaint.description
        ))

        return {"complaint_id": cur.lastrowid,
                "status": "open",
                "message": "Complaint submitted"}


@app.get("/complaints")
def list_complaints():
    with get_db() as conn:
        rows = conn.execute("""
            SELECT c.complaint_id, s.full_name, c.category,
                   c.description, c.status, c.created_at
            FROM complaints c
            JOIN students s ON s.student_id = c.student_id
            ORDER BY c.complaint_id DESC
        """).fetchall()
        return [dict(row) for row in rows]


@app.patch("/complaints/{complaint_id}")
def update_complaint(complaint_id: int, data: StatusInput):
    if data.status not in ("open", "in_progress", "resolved"):
        raise HTTPException(status_code=400, detail="Invalid complaint status")

    with get_db() as conn:
        cur = conn.execute("""
            UPDATE complaints SET status = ?
            WHERE complaint_id = ?
        """, (data.status, complaint_id))

        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Complaint not found")

        return {"message": "Complaint status updated",
                "status": data.status}
