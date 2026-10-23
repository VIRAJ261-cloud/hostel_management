
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS hostels (
    hostel_id INTEGER PRIMARY KEY AUTOINCREMENT,
    hostel_name TEXT NOT NULL,
    gender TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rooms (
    room_id INTEGER PRIMARY KEY AUTOINCREMENT,
    hostel_id INTEGER NOT NULL,
    room_number TEXT NOT NULL,
    capacity INTEGER NOT NULL CHECK (capacity > 0),
    monthly_fee REAL NOT NULL CHECK (monthly_fee >= 0),
    FOREIGN KEY (hostel_id) REFERENCES hostels(hostel_id),
    UNIQUE(hostel_id, room_number)
);

CREATE TABLE IF NOT EXISTS students (
    student_id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    roll_number TEXT NOT NULL UNIQUE,
    email TEXT NOT NULL UNIQUE,
    phone TEXT
);

CREATE TABLE IF NOT EXISTS bookings (
    booking_id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL,
    room_id INTEGER NOT NULL,
    booking_date TEXT DEFAULT CURRENT_TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'approved', 'cancelled')),
    FOREIGN KEY (student_id) REFERENCES students(student_id),
    FOREIGN KEY (room_id) REFERENCES rooms(room_id)
);

CREATE TABLE IF NOT EXISTS complaints (
    complaint_id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL,
    category TEXT NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'
        CHECK(status IN ('open', 'in_progress', 'resolved')),
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (student_id) REFERENCES students(student_id)
);

CREATE INDEX IF NOT EXISTS idx_bookings_room
ON bookings(room_id, status);

CREATE INDEX IF NOT EXISTS idx_complaints_status
ON complaints(status);

INSERT OR IGNORE INTO hostels
(hostel_id, hostel_name, gender)
VALUES (1, 'Walchand Hostel A', 'boys');

INSERT OR IGNORE INTO rooms
(room_id, hostel_id, room_number, capacity, monthly_fee)
VALUES
(1, 1, '101', 3, 2500),
(2, 1, '102', 2, 3000),
(3, 1, '103', 4, 2000);
