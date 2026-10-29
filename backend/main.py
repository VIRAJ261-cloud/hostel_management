import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from contextlib import contextmanager


import jwt
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash
from pydantic import BaseModel

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

ADMIN_CODE = os.getenv("WALCHAND_ADMIN_CODE")
JWT_SECRET = os.getenv("JWT_SECRET")

if not ADMIN_CODE or not JWT_SECRET:
    raise RuntimeError("Set WALCHAND_ADMIN_CODE and JWT_SECRET in .env")

password_hash = PasswordHash.recommended()
bearer = HTTPBearer()

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


@contextmanager           
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize_db():
    with get_db() as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))

def initialize_auth():
    with get_db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('admin','student')),
            student_id INTEGER UNIQUE,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(student_id) REFERENCES students(student_id)
        );
        """)

@app.on_event("startup")
def startup():
    initialize_db()
    initialize_auth()

class StudentInput(BaseModel):
    full_name: str
    roll_number: str
    email: str
    phone: str = ""


class BookingInput(BaseModel):
    room_id: int


class RoomInput(BaseModel):
    hostel_id: int
    room_number: str
    capacity: int
    monthly_fee: float


class ComplaintInput(BaseModel):
    student_id: int
    category: str
    description: str


class StatusInput(BaseModel):
    status: str

class RegisterInput(BaseModel):
    name: str
    email: str
    password: str
    role: str
    admin_code: str | None = None
    roll_number: str | None = None
    phone: str | None = None

class LoginInput(BaseModel):
    email: str
    password: str

def create_token(user):
    now = datetime.now(timezone.utc)
    return jwt.encode({
        "sub": str(user["user_id"]),
        "role": user["role"],
        "iat": now,
        "exp": now + timedelta(hours=8)
    }, JWT_SECRET, algorithm="HS256")

def current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer)
):
    try:
        payload = jwt.decode(
            credentials.credentials,
            JWT_SECRET,
            algorithms=["HS256"]
        )
        with get_db() as conn:
            user = conn.execute(
                "SELECT user_id, name, email, role, student_id "
                "FROM users WHERE user_id = ?",
                (int(payload["sub"]),)
            ).fetchone()

        if not user or user["role"] != payload.get("role"):
            raise HTTPException(status_code=401, detail="Invalid session")
        return dict(user)
    except (jwt.InvalidTokenError, ValueError, KeyError):
        raise HTTPException(status_code=401, detail="Invalid or expired token")

def require_admin(user=Depends(current_user)):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user

def require_student(user=Depends(current_user)):
    if user["role"] != "student":
        raise HTTPException(status_code=403, detail="Student access required")
    return user


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
def create_booking(booking: BookingInput,user=Depends(require_student)):
    with get_db() as conn:
        student = conn.execute(
        "SELECT student_id FROM users WHERE user_id = ?",
        (user["user_id"],)
        ).fetchone()

        if not student or student["student_id"] is None:
            raise HTTPException(
                status_code=403,
                detail="Your account is not linked to a student record"
            )

        student_id = student["student_id"]
        student = conn.execute(
            "SELECT student_id FROM students WHERE student_id = ?",
            (student_id,)
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
        """, (student_id,)).fetchone()

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
        """, (student_id, booking.room_id))

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

@app.get("/my/bookings")
def my_bookings(user=Depends(require_student)):
    with get_db() as conn:
        student = conn.execute(
            "SELECT student_id FROM users WHERE user_id = ?",
            (user["user_id"],)
        ).fetchone()

        if not student or student["student_id"] is None:
            raise HTTPException(
                status_code=403,
                detail="Your account is not linked to a student record."
            )

        rows = conn.execute("""
            SELECT b.booking_id,
                   h.hostel_name,
                   r.room_number,
                   b.booking_date,
                   b.status
            FROM bookings b
            JOIN rooms r ON r.room_id = b.room_id
            JOIN hostels h ON h.hostel_id = r.hostel_id
            WHERE b.student_id = ?
            ORDER BY b.booking_id DESC
        """, (student["student_id"],)).fetchall()

        return [dict(row) for row in rows]


@app.get("/students")
def list_students(user=Depends(require_admin)):
    with get_db() as conn:
        rows = conn.execute("""
            SELECT student_id, full_name, roll_number, email, phone
            FROM students
            ORDER BY student_id DESC
        """).fetchall()

        return [dict(row) for row in rows]


@app.post("/rooms")
def create_room(room: RoomInput, user=Depends(require_admin)):
    room_number = room.room_number.strip()

    if not room_number:
        raise HTTPException(
            status_code=400,
            detail="Room number is required"
        )

    if room.capacity < 1:
        raise HTTPException(
            status_code=400,
            detail="Capacity must be at least 1"
        )

    if room.monthly_fee < 0:
        raise HTTPException(
            status_code=400,
            detail="Monthly fee cannot be negative"
        )

    with get_db() as conn:
        hostel = conn.execute(
            "SELECT hostel_id FROM hostels WHERE hostel_id = ?",
            (room.hostel_id,)
        ).fetchone()

        if not hostel:
            raise HTTPException(
                status_code=404,
                detail="Hostel not found"
            )

        try:
            cursor = conn.execute("""
                INSERT INTO rooms
                    (hostel_id, room_number, capacity, monthly_fee)
                VALUES (?, ?, ?, ?)
            """, (
                room.hostel_id,
                room_number,
                room.capacity,
                room.monthly_fee
            ))
        except sqlite3.IntegrityError as exc:
            raise HTTPException(
                status_code=400,
                detail="Could not add room. Check the room number and hostel."
            ) from exc

        return {
            "message": "Room added successfully",
            "room_id": cursor.lastrowid
        }


@app.get("/hostels")
def list_hostels(user=Depends(require_admin)):
    with get_db() as conn:
        rows = conn.execute("""
            SELECT hostel_id, hostel_name
            FROM hostels
            ORDER BY hostel_name
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

@app.post("/auth/register")
def register(data: RegisterInput):
    name = data.name.strip()
    email = data.email.strip().lower()

    if not name or len(name) > 100:
        raise HTTPException(status_code=400, detail="Enter a valid name")
    if len(data.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    if data.role not in ("admin", "student"):
        raise HTTPException(status_code=400, detail="Invalid role")

    # Admin code is checked on the server, never in the frontend.
    if data.role == "admin":
        if not data.admin_code or not __import__("hmac").compare_digest(
            data.admin_code, ADMIN_CODE
        ):
            raise HTTPException(status_code=403, detail="Invalid admin code")
        student_id = None
    else:
        if not data.roll_number or not data.roll_number.strip():
            raise HTTPException(status_code=400, detail="Roll number required")
        student_id = None

    try:
        with get_db() as conn:
            if data.role == "student":
                cur = conn.execute("""
                    INSERT INTO students (full_name, roll_number, email, phone)
                    VALUES (?, ?, ?, ?)
                """, (
                    name, data.roll_number.strip(), email, data.phone or ""
                ))
                student_id = cur.lastrowid

            cur = conn.execute("""
                INSERT INTO users (name, email, password_hash, role, student_id)
                VALUES (?, ?, ?, ?, ?)
            """, (
                name, email, password_hash.hash(data.password),
                data.role, student_id
            ))
            user_id = cur.lastrowid

        return {"message": "Registration successful", "user_id": user_id}
    except Exception as exc:
        import sqlite3
        if isinstance(exc, sqlite3.IntegrityError):
            raise HTTPException(
                status_code=400,
                detail="Email or roll number already registered"
            ) from exc
        raise

@app.post("/auth/login")
def login(data: LoginInput):
    with get_db() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE email = ?",
            (data.email.strip().lower(),)
        ).fetchone()

    if not user or not password_hash.verify(
        data.password, user["password_hash"]
    ):
        raise HTTPException(status_code=401, detail="Incorrect email or password")

    return {
        "access_token": create_token(user),
        "token_type": "bearer",
        "role": user["role"],
        "name": user["name"]
    }

@app.get("/auth/me")
def me(user=Depends(current_user)):
    return user
