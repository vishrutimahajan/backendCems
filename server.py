import os
import re
import psycopg2
import psycopg2.pool
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory, send_file
from flask_cors import CORS
from werkzeug.utils import secure_filename
from PIL import Image, ImageDraw, ImageFont
import pyotp
import time
from datetime import datetime
import json as _json
import traceback
import google.generativeai as _genai

load_dotenv()
app = Flask(__name__)
CORS(app, resources={r"/api/*": {
    "origins": "*",
    "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    "allow_headers": ["Content-Type", "Authorization"]
}})

UPLOAD_FOLDER = 'static/uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
# ── Reports image folder & Gemini setup ──────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if GEMINI_API_KEY:
    _genai.configure(api_key=GEMINI_API_KEY)
REPORT_IMAGE_FOLDER = os.path.join(UPLOAD_FOLDER, 'reports')
os.makedirs(REPORT_IMAGE_FOLDER, exist_ok=True)
ALLOWED_IMAGE_EXTS  = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

if not os.path.exists('templates'):
    os.makedirs('templates')
# 1. Define the settings as a dictionary (mapping)
DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "port": os.getenv("DB_PORT"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASS"),
    "database": os.getenv("DB_NAME")
}

# 2. Pass that dictionary into the Pool
# The ** before DB_CONFIG "unpacks" the dictionary into the function
try:
    pool = psycopg2.pool.SimpleConnectionPool(1, 20, **DB_CONFIG)
    if pool:
        print("Connection pool created successfully using Supabase")
except Exception as e:
    print(f"Error creating connection pool: {e}")

def get_db_connection():
    return pool.getconn()

# ==========================================
# 1. AUTHENTICATION
# ==========================================

@app.route('/api/auth/register', methods=['POST'])
def register():
    try:
        data = request.json
        email = data['email']
        if not email.endswith('@college.edu'):
            return jsonify({"message": "Please use your official College Email ID (@college.edu)"}), 400

        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("SELECT * FROM users WHERE email = %s", (email,))
        if cur.fetchone():
            return jsonify({"message": "Email already exists!"}), 400

        query = "INSERT INTO users (name, email, password, role, status) VALUES (%s, %s, %s, 'student', 'pending')"
        cur.execute(query, (data['name'], email, data['password']))
        
        conn.commit()
        cur.close()
        pool.putconn(conn)
        return jsonify({"message": "Registration Sent! Wait for Admin Approval."})
    except Exception as e:
        return jsonify({"message": str(e)}), 500

@app.route('/api/lecturer/signup', methods=['POST'])
def lecturer_signup():
    try:
        data = request.json
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("SELECT * FROM users WHERE email = %s", (data['email'],))
        if cur.fetchone():
            return jsonify({"message": "Email already exists!"}), 400

        query = """
            INSERT INTO users (name, email, password, role, status, expertise, bio, social_links)
            VALUES (%s, %s, %s, 'lecturer', 'pending', %s, %s, %s)
        """
        cur.execute(query, (
            data['name'], data['email'], data['password'], 
            data.get('expertise', 'General'), data.get('bio', ''), data.get('social_links', '') 
        ))
        
        conn.commit()
        cur.close()
        pool.putconn(conn)
        return jsonify({"message": "Request sent to Admin!"})
    except Exception as e:
        return jsonify({"message": "Server Error"}), 500

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.json
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT id, name, role, status, email FROM users WHERE email = %s AND password = %s", (data['email'], data['password']))
    user = cur.fetchone()
    
    cur.close()
    pool.putconn(conn)

    if user:
        if user[3] == 'pending':
            return jsonify({"message": "Account pending approval."}), 403
            
        return jsonify({
            "message": "Login Successful",
            "user": { "id": user[0], "name": user[1], "role": user[2], "email": user[4] }
        })
    else:
        return jsonify({"message": "Invalid Credentials"}), 401

# ==========================================
# 2. ADMIN MANAGEMENT
# ==========================================

@app.route('/api/admin/create-admin', methods=['POST'])
def create_admin():
    data = request.json
    conn = get_db_connection()
    cur = conn.cursor()
    query = "INSERT INTO users (name, email, password, role, status) VALUES (%s, %s, %s, 'admin', 'active')"
    cur.execute(query, (data['name'], data['email'], data['password']))
    conn.commit()
    cur.close()
    pool.putconn(conn)
    return jsonify({"message": "New Admin Created!"})

@app.route('/api/admin/pending-users', methods=['GET'])
def get_pending_users():
    conn = get_db_connection()
    cur = conn.cursor()
    query = "SELECT id, name, email, role, expertise, bio, social_links FROM users WHERE status = 'pending'"
    cur.execute(query)
    users = cur.fetchall()
    cur.close()
    pool.putconn(conn)

    results = []
    for u in users:
        results.append({ "id": u[0], "name": u[1], "email": u[2], "role": u[3], "expertise": u[4], "bio": u[5], "social_links": u[6] })
    return jsonify(results)

@app.route('/api/admin/approve-user/<int:id>', methods=['PUT'])
def approve_user(id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET status = 'active' WHERE id = %s", (id,))
    conn.commit()
    cur.close()
    pool.putconn(conn)
    return jsonify({"message": "User Approved!"})

# ==========================================
# 3. ROOM MANAGEMENT
# ==========================================

@app.route('/api/rooms', methods=['GET'])
def get_rooms():
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, room_name, room_type, capacity, floor, building, amenities, status
            FROM rooms
            WHERE status = 'active'
            ORDER BY building, floor, room_name
        """)
        rooms = cur.fetchall()
        cur.close()

        return jsonify([
            {
                "id": r[0],
                "room_name": r[1],
                "room_type": r[2],
                "capacity": r[3],
                "floor": r[4],
                "building": r[5],
                "amenities": r[6],
                "status": r[7]
            } for r in rooms
        ])
    finally:
        pool.putconn(conn)


@app.route('/api/rooms/available', methods=['POST'])
def check_room_availability():
    data = request.json
    start_time = data.get('start_time')
    end_time = data.get('end_time')
    exclude_event_id = data.get('exclude_event_id')

    if not start_time or not end_time:
        return jsonify({"error": "Start time and end time required"}), 400

    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, room_name, room_type, capacity, floor, building
            FROM rooms
            WHERE status = 'active'
        """)
        all_rooms = cur.fetchall()

        available_rooms = []
        unavailable_rooms = []

        for room in all_rooms:
            if exclude_event_id:
                cur.execute("""
                    SELECT id, title, time, end_time
                    FROM events
                    WHERE room_id = %s
                      AND id != %s
                      AND time < %s
                      AND end_time > %s
                """, (room[0], exclude_event_id, end_time, start_time))
            else:
                cur.execute("""
                    SELECT id, title, time, end_time
                    FROM events
                    WHERE room_id = %s
                      AND time < %s
                      AND end_time > %s
                """, (room[0], end_time, start_time))

            conflict = cur.fetchone()

            if conflict:
                unavailable_rooms.append({
                    "id": room[0],
                    "room_name": room[1],
                    "conflict": {
                        "event_title": conflict[1],
                        "start_time": str(conflict[2]),
                        "end_time": str(conflict[3])
                    }
                })
            else:
                available_rooms.append({
                    "id": room[0],
                    "room_name": room[1],
                    "room_type": room[2],
                    "capacity": room[3],
                    "floor": room[4],
                    "building": room[5]
                })

        cur.close()
        return jsonify({
            "available": available_rooms,
            "unavailable": unavailable_rooms
        })
    finally:
        pool.putconn(conn)


@app.route('/api/rooms/<int:room_id>/schedule', methods=['GET'])
def get_room_schedule(room_id):
    """Get all bookings for a specific room"""
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    query = """
        SELECT e.id, e.title, e.time, e.end_time, e.description
        FROM events e
        WHERE e.room_id = %s
    """
    params = [room_id]
    
    if start_date and end_date:
        query += " AND e.time >= %s AND e.end_time <= %s"
        params.extend([start_date, end_date])
    
    query += " ORDER BY e.time"
    
    cur.execute(query, params)
    bookings = cur.fetchall()
    cur.close()
    pool.putconn(conn)

    schedule = []
    for b in bookings:
        schedule.append({
            "event_id": b[0],
            "title": b[1],
            "start_time": str(b[2]),
            "end_time": str(b[3]),
            "description": b[4]
        })

    return jsonify(schedule)

# ==========================================
# 4. EVENTS & DASHBOARD DATA
# ==========================================

@app.route('/api/admin/stats', methods=['GET'])
def get_admin_stats():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users;")
    user_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM events;")
    event_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM registrations;")
    reg_count = cur.fetchone()[0]
    cur.close()
    pool.putconn(conn)
    return jsonify({"users": user_count, "events": event_count, "registrations": reg_count})

@app.route('/api/events', methods=['GET'])
def get_events():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT e.id, e.title, e.time, e.location, e.category, e.color, e.description, 
               e.speaker_name, e.speaker_role, e.end_time, e.room_id, r.room_name,
               e.cover_image
        FROM events e
        LEFT JOIN rooms r ON e.room_id = r.id
    """)
    events = cur.fetchall()
    cur.close()
    pool.putconn(conn)

    event_list = []
    for e in events:
        event_list.append({
            "id": e[0], "title": e[1], "time": e[2], "location": e[3],
            "category": e[4], "color": e[5], "description": e[6],
            "speaker_name": e[7], "speaker_role": e[8], "end_time": e[9],
            "room_id": e[10], "room_name": e[11],
            "cover_image": f"http://localhost:5000/static/uploads/{e[12]}" if e[12] else None
        })
    return jsonify(event_list)

# --- CREATE EVENT WITH ROOM BOOKING ---
@app.route('/api/admin/events', methods=['POST'])
def create_event():
    data = request.json
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Extract room and timing information
    room_id = data.get('room_id')
    start_time = data.get('date')
    end_time = data.get('end_date')
    communities = data.get('communities', [])
    # Check for room conflicts if room is specified
    if room_id and start_time and end_time:
        cur.execute("""
            SELECT e.id, e.title, e.time, e.end_time, r.room_name
            FROM events e
            JOIN rooms r ON e.room_id = r.id
            WHERE e.room_id = %s 
            AND e.time < %s 
            AND e.end_time > %s
        """, (room_id, end_time, start_time))
        
        conflict = cur.fetchone()
        
        if conflict:
            cur.close()
            pool.putconn(conn)
            return jsonify({
                "message": f"Room is already booked! '{conflict[4]}' is occupied by '{conflict[1]}' from {conflict[2]} to {conflict[3]}",
                "conflict": {
                    "event_id": conflict[0],
                    "event_title": conflict[1],
                    "start_time": str(conflict[2]),
                    "end_time": str(conflict[3]),
                    "room_name": conflict[4]
                }
            }), 409
    
    # Insert event with room booking
    query = """
        INSERT INTO events (title, time, end_time, location, category, color, description, speaker_name, speaker_role, room_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
    """
    cur.execute(query, (
        data['title'], start_time, end_time, data.get('location', ''), 
        data['category'], data['color'], data['description'],
        data.get('speaker_name', 'TBA'), data.get('speaker_role', 'Lecturer'),
        room_id
    ))
    
    new_event_id = cur.fetchone()[0]

    for cid in communities:
       cur.execute("""
        INSERT INTO community_events (community_id, event_id)
        VALUES (%s, %s)
        ON CONFLICT DO NOTHING
    """, (cid, new_event_id))

    if room_id:
        cur.execute("""
            INSERT INTO room_bookings (room_id, event_id, start_time, end_time, booked_by, status)
            VALUES (%s, %s, %s, %s, %s, 'confirmed')
        """, (room_id, new_event_id, start_time, end_time, data.get('created_by', 1)))
    
    conn.commit()
    cur.close()
    pool.putconn(conn)

    return jsonify({"message": "Event Created!", "id": new_event_id})

@app.route('/api/admin/events/<int:event_id>', methods=['DELETE'])
def delete_event(event_id):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Delete associated bookings
        cur.execute("DELETE FROM room_bookings WHERE event_id = %s", (event_id,))
        cur.execute("DELETE FROM registrations WHERE event_id = %s", (event_id,))
        cur.execute("DELETE FROM events WHERE id = %s", (event_id,))     
        conn.commit()
        msg = "Event Deleted!"
    except Exception as e:
        conn.rollback()
        msg = "Error: " + str(e)
    cur.close()
    pool.putconn(conn)
    return jsonify({"message": msg})

@app.route('/static/uploads/<filename>')
def serve_image(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/api/admin/upload-qr/<int:event_id>', methods=['POST'])
def upload_qr(event_id):
    if 'file' not in request.files: return jsonify({"error": "No file part"}), 400
    file = request.files['file']
    if file.filename == '': return jsonify({"error": "No selected file"}), 400

    filename = f"qr_{event_id}.png" 
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)

    return jsonify({"message": "QR Code Uploaded!"})

@app.route('/api/admin/upload-template/<int:event_id>', methods=['POST'])
def upload_template(event_id):
    if 'file' not in request.files: return jsonify({"error": "No file part"}), 400
    file = request.files['file']
    if file.filename == '': return jsonify({"error": "No selected file"}), 400

    filename = f"template_{event_id}.png" 
    filepath = os.path.join('templates', filename)
    file.save(filepath)

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE events SET certificate_template = %s WHERE id = %s", (filename, event_id))
    conn.commit()
    cur.close()
    pool.putconn(conn)
    return jsonify({"message": "Template Uploaded!"})

@app.route('/api/admin/upload-cover/<int:event_id>', methods=['POST'])
def upload_cover(event_id):
    if 'file' not in request.files: return jsonify({"error": "No file"}), 400
    file = request.files['file']
    if file.filename == '': return jsonify({"error": "No filename"}), 400
    filename = f"cover_{event_id}.png"
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)
    conn = get_db_connection()
    cur  = conn.cursor()
    try:
        cur.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS cover_image VARCHAR(255)")
    except: pass
    cur.execute("UPDATE events SET cover_image = %s WHERE id = %s", (filename, event_id))
    conn.commit(); cur.close(); pool.putconn(conn)
    return jsonify({"message": "Cover uploaded!", "filename": filename})

@app.route('/api/profile', methods=['GET'])
def get_profile():
    user_id = request.args.get('user_id')
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT name, email, phone, bio, profile_pic FROM users WHERE id = %s', (user_id,))
    user = cur.fetchone()
    cur.close()
    pool.putconn(conn)
    
    if user:
        pic_url = f"http://localhost:5000/static/uploads/{user[4]}" if user[4] else None
        return jsonify({ "name": user[0], "email": user[1], "phone": user[2], "bio": user[3], "profile_pic": pic_url })
    return jsonify({"error": "User not found"}), 404

@app.route('/api/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files: return jsonify({'error': 'No file part'}), 400
    file = request.files['file']
    user_id = request.form.get('user_id')

    if file:
        filename = secure_filename(file.filename)
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("UPDATE users SET profile_pic = %s WHERE id = %s", (filename, user_id))
        conn.commit()
        cur.close()
        pool.putconn(conn)
        return jsonify({'message': 'Image uploaded', 'url': f"http://localhost:5000/static/uploads/{filename}"})

@app.route('/api/profile', methods=['POST'])
def update_profile():
    data = request.json
    user_id = data.get('user_id')
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET name=%s, email=%s, phone=%s, bio=%s WHERE id=%s",
                (data['name'], data['email'], data['phone'], data['bio'], user_id))
    conn.commit()
    cur.close()
    pool.putconn(conn)
    return jsonify({"message": "Profile updated!"})

@app.route('/api/event/register', methods=['POST'])
def register_for_event():
    data = request.json
    user_id, event_id = data.get('user_id'), data.get('event_id')
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM registrations WHERE user_id = %s AND event_id = %s", (user_id, event_id))
    if cur.fetchone():
        return jsonify({"message": "Already registered!"}), 400

    cur.execute("INSERT INTO registrations (user_id, event_id, status) VALUES (%s, %s, 'Registered')", (user_id, event_id))
    conn.commit()
    cur.close()
    pool.putconn(conn)
    return jsonify({"message": "Registration successful!"})

@app.route('/api/my-events/<int:user_id>', methods=['GET'])
def get_user_events(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    query = """
        SELECT e.id, e.title, e.time, e.location, e.color, r.status 
        FROM registrations r
        JOIN events e ON r.event_id = e.id
        WHERE r.user_id = %s
    """
    cur.execute(query, (user_id,))
    rows = cur.fetchall()
    cur.close()
    pool.putconn(conn)

    output = []
    for row in rows:
        output.append({ "id": row[0], "title": row[1], "time": row[2], "location": row[3], "color": row[4], "status": row[5] })
    return jsonify(output)

@app.route('/api/mark-attendance', methods=['POST'])
def mark_attendance():
    data = request.json
    user_id  = data.get('user_id')
    event_id = data.get('event_id')
    # Accept custom status so admin can toggle between Attended / Registered
    status   = data.get('status', 'Attended')
    if status not in ('Attended', 'Registered'):
        status = 'Attended'
    conn = get_db_connection()
    cur  = conn.cursor()
    cur.execute("UPDATE registrations SET status = %s WHERE user_id = %s AND event_id = %s", (status, user_id, event_id))
    conn.commit()
    cur.close()
    pool.putconn(conn)
    return jsonify({"success": True, "message": f"Status set to {status}!"})

@app.route('/api/admin/attendees/<int:event_id>', methods=['GET'])
def get_event_attendees(event_id):
    conn = get_db_connection()
    cur = conn.cursor()
    query = """
        SELECT u.id, u.name, u.email, u.roll_no, r.status, r.winner_tag
        FROM registrations r
        JOIN users u ON r.user_id = u.id
        WHERE r.event_id = %s
    """
    cur.execute(query, (event_id,))
    attendees = cur.fetchall()
    cur.close()
    pool.putconn(conn)

    return jsonify([
        { "id": row[0], "name": row[1], "email": row[2], "roll_no": row[3] if row[3] else "N/A", "status": row[4], "winner_tag": row[5] }
        for row in attendees
    ])

@app.route('/api/certificate/<int:event_id>/<int:user_id>', methods=['GET'])
def generate_certificate(event_id, user_id):
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT certificate_template FROM events WHERE id = %s", (event_id,))
    row = cur.fetchone()
    template_file = row[0] if row else None
    
    cur.execute("SELECT name FROM users WHERE id = %s", (user_id,))
    row_user = cur.fetchone()
    student_name = row_user[0] if row_user else "Unknown Student"

    cur.close()
    pool.putconn(conn)

    if not template_file: return jsonify({"error": "No certificate template found"}), 404
    template_path = os.path.join('templates', template_file)
    
    try:
        img = Image.open(template_path)
    except Exception:
        return jsonify({"error": "Corrupted template file"}), 500

    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 80) 
    except:
        font = ImageFont.load_default() 

    W, H = img.size
    left, top, right, bottom = draw.textbbox((0, 0), student_name, font=font)
    text_width = right - left
    x_pos = (W - text_width) / 2
    y_pos = 600
    
    draw.text((x_pos, y_pos), student_name, font=font, fill="black")
    
    output_filename = f"cert_{event_id}_{user_id}.png"
    img.save(output_filename)
    
    return send_file(output_filename, as_attachment=True)

# ==========================================
# 7. DYNAMIC QR ATTENDANCE
# ==========================================

@app.route('/api/admin/dynamic-qr/<int:event_id>', methods=['GET'])
def get_dynamic_qr(event_id):
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT qr_secret FROM events WHERE id = %s", (event_id,))
    row = cur.fetchone()
    
    secret = None
    if row and row[0]:
        secret = row[0]
    else:
        secret = pyotp.random_base32()
    cur.execute("UPDATE events SET qr_secret = %s WHERE id = %s", (secret, event_id))
    conn.commit()

    cur.close()
    pool.putconn(conn)

    totp = pyotp.TOTP(secret, interval=10)
    current_token = totp.now()
    qr_data = f"EVENT:{event_id}:TOKEN:{current_token}"

    return jsonify({"qr_data": qr_data, "seconds_remaining": totp.interval - (time.time() % totp.interval)})

@app.route('/api/mark-attendance-dynamic', methods=['POST'])
def mark_attendance_dynamic():
    data = request.json
    user_id = data.get('user_id')
    raw_qr_data = data.get('qr_data')
    
    try:
        parts = raw_qr_data.split(':')
        event_id = int(parts[1])
        token = parts[3]
    except:
        return jsonify({"success": False, "message": "Invalid QR Format"}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT qr_secret FROM events WHERE id = %s", (event_id,))
    row = cur.fetchone()
    
    if not row or not row[0]:
        return jsonify({"success": False, "message": "Event has no secret key"}), 400
        
    secret = row[0]
    
    totp = pyotp.TOTP(secret, interval=10)
    if not totp.verify(token, valid_window=1): 
        return jsonify({"success": False, "message": "QR Code Expired! Scan again."}), 400

    cur.execute("UPDATE registrations SET status = 'Attended' WHERE user_id = %s AND event_id = %s", (user_id, event_id))
    conn.commit()
    cur.close()
    pool.putconn(conn)

    return jsonify({"success": True, "message": "Attendance Marked Successfully!"})

def _is_moderator(cur, community_id, user_id):
    """Return True if user is head or coordinator of the community."""
    cur.execute(
        "SELECT role FROM community_members WHERE community_id=%s AND user_id=%s",
        (community_id, user_id)
    )
    row = cur.fetchone()
    return bool(row and row[0] in ('head', 'coordinator'))


# ==========================================
# COMMUNITIES CRUD
# ==========================================

@app.route('/api/communities', methods=['GET'])
def get_communities():
    user_id = request.args.get('user_id')
    conn = get_db_connection()
    cur  = conn.cursor()
    cur.execute("""
        SELECT c.id, c.name, c.slug, c.description, c.icon, c.color, c.cover_image,
               COUNT(DISTINCT cm.user_id)                                AS member_count,
               MAX(CASE WHEN cm2.user_id = %s THEN cm2.role END)         AS my_role
        FROM   communities c
        LEFT JOIN community_members cm  ON c.id = cm.community_id
        LEFT JOIN community_members cm2 ON c.id = cm2.community_id AND cm2.user_id = %s
        WHERE  c.status = 'active'
        GROUP  BY c.id
        ORDER  BY c.name
    """, (user_id, user_id))
    rows = cur.fetchall()
    cur.close(); pool.putconn(conn)
    return jsonify([{
        "id": r[0], "name": r[1], "slug": r[2], "description": r[3],
        "icon": r[4], "color": r[5], "cover_image": r[6],
        "member_count": r[7], "my_role": r[8]
    } for r in rows])


@app.route('/api/communities', methods=['POST'])
def create_community():
    """
    Body: { name, description, icon, color, created_by }
    Returns the full new community object so the frontend can append it immediately.
    """
    data = request.json
    if not data or not data.get('name') or not data.get('created_by'):
        return jsonify({"error": "name and created_by are required"}), 400

    slug = re.sub(r'[^a-z0-9]+', '-', data['name'].lower()).strip('-')
    conn = get_db_connection()
    cur  = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO communities (name, slug, description, icon, color, created_by)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id, name, slug, description, icon, color
        """, (
            data['name'], slug,
            data.get('description', ''),
            data.get('icon', '🏛️'),
            data.get('color', '#b5174e'),
            data['created_by']
        ))
        row = cur.fetchone()
        community_id = row[0]

        # Auto-add creator as head
        cur.execute("""
            INSERT INTO community_members (community_id, user_id, role, added_by)
            VALUES (%s, %s, 'head', %s)
            ON CONFLICT (community_id, user_id) DO NOTHING
        """, (community_id, data['created_by'], data['created_by']))

        conn.commit()
    except Exception as e:
        conn.rollback()
        cur.close(); pool.putconn(conn)
        return jsonify({"error": str(e)}), 500

    cur.close(); pool.putconn(conn)
    # Return full object so frontend can add it to state without a second fetch
    return jsonify({
        "id":          row[0], "name":        row[1], "slug":  row[2],
        "description": row[3], "icon":         row[4], "color": row[5],
        "member_count": 1,     "my_role":     "head"
    }), 201


@app.route('/api/communities/<int:community_id>', methods=['GET'])
def get_community(community_id):
    user_id = request.args.get('user_id')
    conn = get_db_connection()
    cur  = conn.cursor()
    cur.execute("""
        SELECT c.id, c.name, c.slug, c.description, c.icon, c.color, c.cover_image,
               COUNT(DISTINCT cm.user_id)                                AS member_count,
               MAX(CASE WHEN cm2.user_id = %s THEN cm2.role END)         AS my_role
        FROM   communities c
        LEFT JOIN community_members cm  ON c.id = cm.community_id
        LEFT JOIN community_members cm2 ON c.id = cm2.community_id AND cm2.user_id = %s
        WHERE  c.id = %s
        GROUP  BY c.id
    """, (user_id, user_id, community_id))
    r = cur.fetchone()
    cur.close(); pool.putconn(conn)
    if not r:
        return jsonify({"error": "Not found"}), 404
    return jsonify({
        "id": r[0], "name": r[1], "slug": r[2], "description": r[3],
        "icon": r[4], "color": r[5], "cover_image": r[6],
        "member_count": r[7], "my_role": r[8]
    })


@app.route('/api/communities/<int:community_id>', methods=['PUT'])
def update_community(community_id):
    data = request.json
    conn = get_db_connection()
    cur  = conn.cursor()
    if not _is_moderator(cur, community_id, data.get('requester_id')):
        cur.close(); pool.putconn(conn)
        return jsonify({"error": "Insufficient permissions"}), 403
    cur.execute("""
        UPDATE communities SET name=%s, description=%s, icon=%s, color=%s WHERE id=%s
    """, (data['name'], data.get('description',''), data.get('icon','🏛️'),
          data.get('color','#b5174e'), community_id))
    conn.commit()
    cur.close(); pool.putconn(conn)
    return jsonify({"message": "Updated"})


@app.route('/api/communities/<int:community_id>/archive', methods=['POST'])
def archive_community(community_id):
    data = request.json
    conn = get_db_connection()
    cur  = conn.cursor()
    cur.execute("SELECT role FROM community_members WHERE community_id=%s AND user_id=%s",
                (community_id, data.get('requester_id')))
    row = cur.fetchone()
    if not row or row[0] != 'head':
        cur.close(); pool.putconn(conn)
        return jsonify({"error": "Only heads can archive"}), 403
    cur.execute("UPDATE communities SET status='archived' WHERE id=%s", (community_id,))
    conn.commit()
    cur.close(); pool.putconn(conn)
    return jsonify({"message": "Community archived"})


# ==========================================
# MEMBER MANAGEMENT
# ==========================================

@app.route('/api/communities/<int:community_id>/members', methods=['GET'])
def get_community_members(community_id):
    conn = get_db_connection()
    cur  = conn.cursor()
    cur.execute("""
        SELECT u.id, u.name, u.email, u.profile_pic, cm.role, cm.joined_at
        FROM   community_members cm
        JOIN   users u ON cm.user_id = u.id
        WHERE  cm.community_id = %s
        ORDER  BY CASE cm.role WHEN 'head' THEN 1 WHEN 'coordinator' THEN 2 ELSE 3 END, u.name
    """, (community_id,))
    rows = cur.fetchall()
    cur.close(); pool.putconn(conn)
    return jsonify([{
        "id": r[0], "name": r[1], "email": r[2],
        "profile_pic": f"http://localhost:5000/static/uploads/{r[3]}" if r[3] else None,
        "role": r[4], "joined_at": str(r[5])
    } for r in rows])


@app.route('/api/communities/<int:community_id>/members', methods=['POST'])
def add_community_member(community_id):
    data = request.json
    conn = get_db_connection()
    cur  = conn.cursor()
    if not _is_moderator(cur, community_id, data.get('requester_id')):
        cur.close(); pool.putconn(conn)
        return jsonify({"error": "Insufficient permissions"}), 403
    try:
        cur.execute("""
            INSERT INTO community_members (community_id, user_id, role, added_by)
            VALUES (%s, %s, %s, %s)
        """, (community_id, data['user_id'], data.get('role','member'), data['requester_id']))
        conn.commit()
        msg = "Member added!"
    except Exception as e:
        conn.rollback(); msg = str(e)
    cur.close(); pool.putconn(conn)
    return jsonify({"message": msg})


@app.route('/api/communities/<int:community_id>/members/<int:user_id>', methods=['PUT'])
def update_member_role(community_id, user_id):
    data = request.json
    conn = get_db_connection()
    cur  = conn.cursor()
    cur.execute("SELECT role FROM community_members WHERE community_id=%s AND user_id=%s",
                (community_id, data.get('requester_id')))
    row = cur.fetchone()
    if not row or row[0] != 'head':
        cur.close(); pool.putconn(conn)
        return jsonify({"error": "Only heads can change roles"}), 403
    cur.execute("UPDATE community_members SET role=%s WHERE community_id=%s AND user_id=%s",
                (data['role'], community_id, user_id))
    conn.commit()
    cur.close(); pool.putconn(conn)
    return jsonify({"message": "Role updated"})


@app.route('/api/communities/<int:community_id>/members/<int:user_id>', methods=['DELETE'])
def remove_community_member(community_id, user_id):
    requester_id = request.args.get('requester_id')
    conn = get_db_connection()
    cur  = conn.cursor()
    if not _is_moderator(cur, community_id, requester_id):
        cur.close(); pool.putconn(conn)
        return jsonify({"error": "Insufficient permissions"}), 403
    cur.execute("DELETE FROM community_members WHERE community_id=%s AND user_id=%s",
                (community_id, user_id))
    conn.commit()
    cur.close(); pool.putconn(conn)
    return jsonify({"message": "Member removed"})


@app.route('/api/communities/<int:community_id>/join', methods=['POST'])
def join_community(community_id):
    data = request.json
    user_id = data.get('user_id')
    conn = get_db_connection()
    cur  = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO community_members (community_id, user_id, role, added_by)
            VALUES (%s, %s, 'member', %s)
        """, (community_id, user_id, user_id))
        conn.commit()
        msg = "Joined!"
    except Exception:
        conn.rollback(); msg = "Already a member"
    cur.close(); pool.putconn(conn)
    return jsonify({"message": msg})


@app.route('/api/communities/<int:community_id>/leave', methods=['POST'])
def leave_community(community_id):
    data = request.json
    conn = get_db_connection()
    cur  = conn.cursor()
    cur.execute("DELETE FROM community_members WHERE community_id=%s AND user_id=%s",
                (community_id, data.get('user_id')))
    conn.commit()
    cur.close(); pool.putconn(conn)
    return jsonify({"message": "Left community"})


# ==========================================
# POSTS & REACTIONS & COMMENTS
# ==========================================

@app.route('/api/communities/<int:community_id>/posts', methods=['GET'])
def get_community_posts(community_id):
    user_id = request.args.get('user_id')
    conn = get_db_connection()
    cur  = conn.cursor()
    cur.execute("""
        SELECT p.id, p.content, p.post_type, p.pinned, p.created_at,
               u.id, u.name, u.profile_pic, cm.role,
               COUNT(DISTINCT r.id)                                        AS reaction_count,
               COUNT(DISTINCT c.id)                                        AS comment_count,
               MAX(CASE WHEN r.user_id = %s THEN r.emoji END)              AS my_reaction
        FROM   community_posts p
        JOIN   users u ON p.author_id = u.id
        LEFT JOIN community_members cm ON cm.community_id = p.community_id AND cm.user_id = p.author_id
        LEFT JOIN community_post_reactions r ON r.post_id = p.id
        LEFT JOIN community_comments       c ON c.post_id = p.id
        WHERE  p.community_id = %s
        GROUP  BY p.id, u.id, cm.role
        ORDER  BY p.pinned DESC, p.created_at DESC
    """, (user_id, community_id))
    rows = cur.fetchall()
    cur.close(); pool.putconn(conn)
    return jsonify([{
        "id": r[0], "content": r[1], "post_type": r[2], "pinned": r[3],
        "created_at": str(r[4]), "author_id": r[5], "author_name": r[6],
        "author_pic": f"http://localhost:5000/static/uploads/{r[7]}" if r[7] else None,
        "author_role": r[8], "reaction_count": r[9], "comment_count": r[10],
        "my_reaction": r[11]
    } for r in rows])


@app.route('/api/communities/<int:community_id>/posts', methods=['POST'])
def create_community_post(community_id):
    data      = request.json
    author_id = data.get('author_id')
    post_type = data.get('post_type', 'post')
    conn = get_db_connection()
    cur  = conn.cursor()
    if post_type == 'announcement' and not _is_moderator(cur, community_id, author_id):
        cur.close(); pool.putconn(conn)
        return jsonify({"error": "Only heads/coordinators can post announcements"}), 403
    cur.execute("""
        INSERT INTO community_posts (community_id, author_id, content, post_type, pinned)
        VALUES (%s, %s, %s, %s, %s) RETURNING id
    """, (community_id, author_id, data['content'], post_type, data.get('pinned', False)))
    post_id = cur.fetchone()[0]
    conn.commit()
    cur.close(); pool.putconn(conn)
    return jsonify({"message": "Post created!", "id": post_id}), 201


@app.route('/api/posts/<int:post_id>', methods=['DELETE'])
def delete_post(post_id):
    requester_id = request.args.get('requester_id')
    conn = get_db_connection()
    cur  = conn.cursor()
    cur.execute("SELECT author_id, community_id FROM community_posts WHERE id=%s", (post_id,))
    post = cur.fetchone()
    if not post:
        cur.close(); pool.putconn(conn)
        return jsonify({"error": "Post not found"}), 404
    if str(post[0]) != str(requester_id) and not _is_moderator(cur, post[1], requester_id):
        cur.close(); pool.putconn(conn)
        return jsonify({"error": "Unauthorized"}), 403
    cur.execute("DELETE FROM community_posts WHERE id=%s", (post_id,))
    conn.commit()
    cur.close(); pool.putconn(conn)
    return jsonify({"message": "Post deleted!"})


@app.route('/api/posts/<int:post_id>/react', methods=['POST'])
def react_to_post(post_id):
    data    = request.json
    user_id = data.get('user_id')
    emoji   = data.get('emoji', '👍')
    conn = get_db_connection()
    cur  = conn.cursor()
    cur.execute("SELECT id, emoji FROM community_post_reactions WHERE post_id=%s AND user_id=%s",
                (post_id, user_id))
    existing = cur.fetchone()
    if existing:
        if existing[1] == emoji:
            cur.execute("DELETE FROM community_post_reactions WHERE id=%s", (existing[0],))
            msg = "Reaction removed"
        else:
            cur.execute("UPDATE community_post_reactions SET emoji=%s WHERE id=%s", (emoji, existing[0]))
            msg = "Reaction updated"
    else:
        cur.execute("INSERT INTO community_post_reactions (post_id, user_id, emoji) VALUES (%s,%s,%s)",
                    (post_id, user_id, emoji))
        msg = "Reacted!"
    conn.commit()
    cur.close(); pool.putconn(conn)
    return jsonify({"message": msg})


@app.route('/api/posts/<int:post_id>/comments', methods=['GET'])
def get_post_comments(post_id):
    conn = get_db_connection()
    cur  = conn.cursor()
    cur.execute("""
        SELECT c.id, c.content, c.created_at, u.id, u.name, u.profile_pic
        FROM   community_comments c
        JOIN   users u ON c.author_id = u.id
        WHERE  c.post_id = %s ORDER BY c.created_at
    """, (post_id,))
    rows = cur.fetchall()
    cur.close(); pool.putconn(conn)
    return jsonify([{
        "id": r[0], "content": r[1], "created_at": str(r[2]),
        "author_id": r[3], "author_name": r[4],
        "author_pic": f"http://localhost:5000/static/uploads/{r[5]}" if r[5] else None
    } for r in rows])


@app.route('/api/posts/<int:post_id>/comments', methods=['POST'])
def add_comment(post_id):
    data = request.json
    conn = get_db_connection()
    cur  = conn.cursor()
    cur.execute("""
        INSERT INTO community_comments (post_id, author_id, content)
        VALUES (%s, %s, %s) RETURNING id
    """, (post_id, data['author_id'], data['content']))
    cid = cur.fetchone()[0]
    conn.commit()
    cur.close(); pool.putconn(conn)
    return jsonify({"message": "Comment added!", "id": cid}), 201


# ==========================================
# COMMUNITY ↔ EVENTS  (linking table)
# ==========================================

@app.route('/api/communities/<int:community_id>/events', methods=['GET'])
def get_community_events(community_id):
    """Return events linked to this community via community_events."""
    conn = get_db_connection()
    cur  = conn.cursor()
    cur.execute("""
        SELECT e.id, e.title, e.time, e.end_time, e.location,
               e.category, e.color, e.description
        FROM   community_events ce
        JOIN   events e ON ce.event_id = e.id
        WHERE  ce.community_id = %s
        ORDER  BY e.time ASC
    """, (community_id,))
    rows = cur.fetchall()
    cur.close(); pool.putconn(conn)
    return jsonify([{
        "id": r[0], "title": r[1],
        "time": str(r[2]), "end_time": str(r[3]),
        "location": r[4], "category": r[5],
        "color": r[6], "description": r[7]
    } for r in rows])


@app.route('/api/communities/<int:community_id>/events', methods=['POST'])
def link_community_event(community_id):
    """Head / coordinator links a single event to this community."""
    data         = request.json
    requester_id = data.get('requester_id')
    conn = get_db_connection()
    cur  = conn.cursor()
    if not _is_moderator(cur, community_id, requester_id):
        cur.close(); pool.putconn(conn)
        return jsonify({"error": "Insufficient permissions"}), 403
    try:
        cur.execute("""
            INSERT INTO community_events (community_id, event_id, linked_by)
            VALUES (%s, %s, %s)
        """, (community_id, data['event_id'], requester_id))
        conn.commit()
        msg = "Event linked!"
    except Exception:
        conn.rollback(); msg = "Already linked"
    cur.close(); pool.putconn(conn)
    return jsonify({"message": msg})


@app.route('/api/communities/<int:community_id>/events/<int:event_id>', methods=['DELETE'])
def unlink_community_event(community_id, event_id):
    """Head / coordinator removes an event from this community."""
    requester_id = request.args.get('requester_id')
    conn = get_db_connection()
    cur  = conn.cursor()
    if not _is_moderator(cur, community_id, requester_id):
        cur.close(); pool.putconn(conn)
        return jsonify({"error": "Insufficient permissions"}), 403
    cur.execute("DELETE FROM community_events WHERE community_id=%s AND event_id=%s",
                (community_id, event_id))
    conn.commit()
    cur.close(); pool.putconn(conn)
    return jsonify({"message": "Event unlinked"})


# ==========================================
# ADMIN ENDPOINTS
# ==========================================

@app.route('/api/admin/communities', methods=['GET'])
def admin_get_all_communities():
    """Admin: all active communities (used in event-creation community selector)."""
    conn = get_db_connection()
    cur  = conn.cursor()
    cur.execute("""
        SELECT c.id, c.name, c.icon, c.color,
               COUNT(cm.user_id) AS member_count
        FROM   communities c
        LEFT JOIN community_members cm ON c.id = cm.community_id
        WHERE  c.status = 'active'
        GROUP  BY c.id ORDER BY c.name
    """)
    rows = cur.fetchall()
    cur.close(); pool.putconn(conn)
    return jsonify([{
        "id": r[0], "name": r[1], "icon": r[2],
        "color": r[3], "member_count": r[4]
    } for r in rows])


@app.route('/api/admin/events/<int:event_id>/communities', methods=['GET'])
def get_event_communities(event_id):
    """Admin: which communities are currently linked to an event."""
    conn = get_db_connection()
    cur  = conn.cursor()
    cur.execute("""
        SELECT c.id, c.name, c.icon, c.color
        FROM   community_events ce
        JOIN   communities c ON ce.community_id = c.id
        WHERE  ce.event_id = %s AND c.status = 'active'
    """, (event_id,))
    rows = cur.fetchall()
    cur.close(); pool.putconn(conn)
    return jsonify([{"id": r[0], "name": r[1], "icon": r[2], "color": r[3]} for r in rows])


@app.route('/api/admin/events/<int:event_id>/communities', methods=['PUT'])
def set_event_communities(event_id):
    """
    Admin: atomically replace all community links for an event.
    Body: { community_ids: [1, 2, 3], admin_id: 5 }
    Passing an empty list removes all links.
    """
    data          = request.json
    community_ids = data.get('community_ids', [])
    admin_id      = data.get('admin_id')

    conn = get_db_connection()
    cur  = conn.cursor()

    # Verify caller is admin
    cur.execute("SELECT role FROM users WHERE id=%s", (admin_id,))
    user = cur.fetchone()
    if not user or user[0] != 'admin':
        cur.close(); pool.putconn(conn)
        return jsonify({"error": "Admin access required"}), 403

    try:
        cur.execute("DELETE FROM community_events WHERE event_id=%s", (event_id,))
        for cid in community_ids:
            cur.execute("""
                INSERT INTO community_events (community_id, event_id, linked_by)
                VALUES (%s, %s, %s)
                ON CONFLICT (community_id, event_id) DO NOTHING
            """, (cid, event_id, admin_id))
        conn.commit()
    except Exception as e:
        conn.rollback()
        cur.close(); pool.putconn(conn)
        return jsonify({"error": str(e)}), 500

    cur.close(); pool.putconn(conn)
    return jsonify({
        "message":       f"Event {event_id} linked to {len(community_ids)} communities",
        "community_ids": community_ids
    })

# ==========================================
# WINNER TAGGING SYSTEM
# Requires: ALTER TABLE registrations ADD COLUMN IF NOT EXISTS winner_tag VARCHAR(50) DEFAULT NULL;
# ==========================================

@app.route('/api/admin/events/<int:event_id>/winners', methods=['GET'])
def get_event_winners(event_id):
    conn = get_db_connection()
    cur  = conn.cursor()
    try:
        cur.execute("""
            SELECT u.id, u.name, u.email, u.roll_no, r.status, r.winner_tag
            FROM   registrations r
            JOIN   users u ON r.user_id = u.id
            WHERE  r.event_id = %s
            ORDER  BY
              CASE r.winner_tag
                WHEN 'Winner'      THEN 1
                WHEN 'Runner-up'   THEN 2
                WHEN 'Third Place' THEN 3
                ELSE 4
              END,
              u.name
        """, (event_id,))
        rows = cur.fetchall()
        cur.close(); pool.putconn(conn)
        return jsonify([{
            "id":         row[0],
            "name":       row[1],
            "email":      row[2],
            "roll_no":    row[3] if row[3] else "N/A",
            "status":     row[4],
            "winner_tag": row[5]
        } for row in rows])
    except Exception as e:
        conn.rollback()
        cur.close(); pool.putconn(conn)
        # Column likely doesn't exist yet — return error so frontend can show migration hint
        return jsonify({"error": str(e), "hint": "Run: ALTER TABLE registrations ADD COLUMN IF NOT EXISTS winner_tag VARCHAR(50) DEFAULT NULL;"}), 500


@app.route('/api/admin/events/<int:event_id>/winners/<int:user_id>', methods=['POST'])
def manage_winner_tag(event_id, user_id):
    """
    Body: { "winner_tag": "Winner" | "Runner-up" | "Third Place" | null }
    Passing null clears the tag.
    Using POST to avoid CORS preflight issues with PUT/DELETE.
    """
    data       = request.json or {}
    winner_tag = data.get('winner_tag')   # None = clear

    VALID_TAGS = {None, 'Winner', 'Runner-up', 'Third Place'}
    if winner_tag not in VALID_TAGS:
        return jsonify({"error": "Invalid tag. Choose from: Winner, Runner-up, Third Place"}), 400

    conn = get_db_connection()
    cur  = conn.cursor()
    try:
        if winner_tag:
            # Remove this tag from whoever currently holds it in this event
            cur.execute(
                "UPDATE registrations SET winner_tag = NULL WHERE event_id = %s AND winner_tag = %s AND user_id != %s",
                (event_id, winner_tag, user_id)
            )
        # Set (or clear) the tag for this user
        cur.execute(
            "UPDATE registrations SET winner_tag = %s WHERE event_id = %s AND user_id = %s",
            (winner_tag, event_id, user_id)
        )
        if cur.rowcount == 0:
            conn.rollback(); cur.close(); pool.putconn(conn)
            return jsonify({"error": f"No registration found for user {user_id} in event {event_id}"}), 404

        conn.commit()
        cur.close(); pool.putconn(conn)
        return jsonify({"message": "Winner tag saved!", "winner_tag": winner_tag})
    except Exception as e:
        conn.rollback(); cur.close(); pool.putconn(conn)
        return jsonify({"error": str(e)}), 500



@app.route('/api/events/<int:event_id>/winners', methods=['GET'])
def get_public_event_winners(event_id):
    """Public endpoint — returns only tagged winners for display on Home page."""
    conn = get_db_connection()
    cur  = conn.cursor()
    try:
        cur.execute("""
            SELECT u.id, u.name, r.winner_tag
            FROM   registrations r
            JOIN   users u ON r.user_id = u.id
            WHERE  r.event_id = %s
              AND  r.winner_tag IS NOT NULL
            ORDER  BY
              CASE r.winner_tag
                WHEN 'Winner'      THEN 1
                WHEN 'Runner-up'   THEN 2
                WHEN 'Third Place' THEN 3
              END
        """, (event_id,))
        rows = cur.fetchall()
        cur.close(); pool.putconn(conn)
        return jsonify([{
            "id":         row[0],
            "name":       row[1],
            "winner_tag": row[2]
        } for row in rows])
    except Exception as e:
        conn.rollback()
        cur.close(); pool.putconn(conn)
        return jsonify([])   # Always return empty array, never error, so Home page is unaffected


# ==========================================
# EVENT REPORTS
# ==========================================

def _allowed_image(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_IMAGE_EXTS


# ── helpers ───────────────────────────────────────────────────────────────────

def _get_report_row(cur, report_id):
    cur.execute("""
        SELECT er.id, er.event_id, er.title, er.content, er.created_at, er.updated_at,
               e.title  AS event_title,
               e.category AS event_category,
               e.location, e.time, e.description
        FROM   event_reports er
        JOIN   events e ON e.id = er.event_id
        WHERE  er.id = %s
    """, (report_id,))
    return cur.fetchone()


def _get_report_images(cur, report_id):
    cur.execute("SELECT file_path FROM report_images WHERE report_id = %s ORDER BY id", (report_id,))
    return [r[0] for r in cur.fetchall()]


def _to_iso(val):
    """Return ISO string whether val is a datetime object or already a string."""
    if val is None:
        return None
    if isinstance(val, str):
        return val
    return val.isoformat()


def _row_to_dict(row, images):
    return {
        "id":             row[0],
        "event_id":       row[1],
        "title":          row[2],
        "content":        row[3],
        "created_at":     _to_iso(row[4]),
        "updated_at":     _to_iso(row[5]),
        "event_title":    row[6],
        "event_category": row[7],
        "location":       row[8],
        "event_time":     _to_iso(row[9]),
        "images":         images,
    }


# ── GET /api/reports  (public — students) ────────────────────────────────────

@app.route('/api/reports', methods=['GET'])
def get_all_reports():
    conn = get_db_connection()
    cur  = conn.cursor()
    try:
        cur.execute("""
            SELECT er.id, er.event_id, er.title, er.content, er.created_at, er.updated_at,
                   e.title AS event_title, e.category AS event_category,
                   e.location, e.time, e.description
            FROM   event_reports er
            JOIN   events e ON e.id = er.event_id
            ORDER  BY er.created_at DESC
        """)
        rows = cur.fetchall()
        result = []
        for row in rows:
            imgs = _get_report_images(cur, row[0])
            result.append(_row_to_dict(row, imgs))
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close(); pool.putconn(conn)


# ── GET /api/reports/<id>  (public) ──────────────────────────────────────────

@app.route('/api/reports/<int:report_id>', methods=['GET'])
def get_report(report_id):
    conn = get_db_connection()
    cur  = conn.cursor()
    try:
        row  = _get_report_row(cur, report_id)
        if not row:
            return jsonify({"error": "Not found"}), 404
        imgs = _get_report_images(cur, report_id)
        return jsonify(_row_to_dict(row, imgs))
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close(); pool.putconn(conn)


# ── POST /api/reports  (admin — create) ──────────────────────────────────────

@app.route('/api/reports', methods=['POST'])
def create_report():
    conn = get_db_connection()
    cur  = conn.cursor()
    try:
        event_id = request.form.get('event_id')
        title    = request.form.get('title', '').strip()
        content  = request.form.get('content', '').strip()

        if not event_id or not title or not content:
            return jsonify({"error": "event_id, title, and content are required"}), 400

        cur.execute(
            "INSERT INTO event_reports (event_id, title, content) VALUES (%s, %s, %s) RETURNING id",
            (event_id, title, content)
        )
        report_id = cur.fetchone()[0]

        # Save uploaded images
        for f in request.files.getlist('images'):
            if f and _allowed_image(f.filename):
                fname = secure_filename(f.filename)
                fname = f"{report_id}_{int(time.time())}_{fname}"
                fpath = os.path.join(REPORT_IMAGE_FOLDER, fname)
                f.save(fpath)
                rel_path = f"static/uploads/reports/{fname}"
                cur.execute(
                    "INSERT INTO report_images (report_id, file_path) VALUES (%s, %s)",
                    (report_id, rel_path)
                )

        conn.commit()
        row  = _get_report_row(cur, report_id)
        imgs = _get_report_images(cur, report_id)
        return jsonify(_row_to_dict(row, imgs)), 201

    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close(); pool.putconn(conn)


# ── PUT /api/reports/<id>  (admin — edit) ────────────────────────────────────

@app.route('/api/reports/<int:report_id>', methods=['PUT'])
def update_report(report_id):
    conn = get_db_connection()
    cur  = conn.cursor()
    try:
        event_id        = request.form.get('event_id')
        title           = request.form.get('title', '').strip()
        content         = request.form.get('content', '').strip()
        existing_raw    = request.form.get('existing_images', '[]')
        existing_images = _json.loads(existing_raw)   # paths to keep

        if not title or not content:
            return jsonify({"error": "title and content are required"}), 400

        # Update core fields
        cur.execute(
            """UPDATE event_reports
               SET event_id  = %s,
                   title     = %s,
                   content   = %s,
                   updated_at = NOW()
               WHERE id = %s""",
            (event_id, title, content, report_id)
        )

        # Remove images not in the keep-list
        cur.execute("SELECT id, file_path FROM report_images WHERE report_id = %s", (report_id,))
        for img_id, fpath in cur.fetchall():
            if fpath not in existing_images:
                cur.execute("DELETE FROM report_images WHERE id = %s", (img_id,))
                full = os.path.join(os.getcwd(), fpath)
                if os.path.exists(full):
                    os.remove(full)

        # Add new images
        for f in request.files.getlist('images'):
            if f and _allowed_image(f.filename):
                fname = secure_filename(f.filename)
                fname = f"{report_id}_{int(time.time())}_{fname}"
                fpath = os.path.join(REPORT_IMAGE_FOLDER, fname)
                f.save(fpath)
                rel_path = f"static/uploads/reports/{fname}"
                cur.execute(
                    "INSERT INTO report_images (report_id, file_path) VALUES (%s, %s)",
                    (report_id, rel_path)
                )

        conn.commit()
        row  = _get_report_row(cur, report_id)
        imgs = _get_report_images(cur, report_id)
        return jsonify(_row_to_dict(row, imgs))

    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close(); pool.putconn(conn)


# ── DELETE /api/reports/<id>  (admin) ────────────────────────────────────────

@app.route('/api/reports/<int:report_id>', methods=['DELETE'])
def delete_report(report_id):
    conn = get_db_connection()
    cur  = conn.cursor()
    try:
        # Delete physical images first
        cur.execute("SELECT file_path FROM report_images WHERE report_id = %s", (report_id,))
        for (fpath,) in cur.fetchall():
            full = os.path.join(os.getcwd(), fpath)
            if os.path.exists(full):
                os.remove(full)

        cur.execute("DELETE FROM event_reports WHERE id = %s", (report_id,))
        conn.commit()
        return jsonify({"message": "Report deleted"})
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close(); pool.putconn(conn)

@app.route('/api/reports/ai-generate', methods=['POST'])
def ai_generate_report():
    import re as _re
    import json as _json
    import traceback
    from datetime import datetime as _dt
    
    data = request.json or {}
    event_id = data.get('event_id')
    
    if not event_id:
        return jsonify({'error': 'event_id required'}), 400

    api_key = os.environ.get('GEMINI_API_KEY')
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # 1. Gather Event Context
        cur.execute('SELECT title, description, category, location, time FROM events WHERE id = %s', (event_id,))
        ev = cur.fetchone()
        if not ev:
            return jsonify({'error': 'Event not found'}), 404
        
        # 2. Call Gemini with Strict Instructions
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name='gemini-2.5-flash')
        
        prompt = (
            f"Write a 3-paragraph report for the event: {ev[0]}.\n"
            "Return ONLY a JSON object with keys 'title' and 'report'. "
            "Do not include any other text or markdown formatting."
        )

        response = model.generate_content(prompt)
        raw_text = response.text.strip()

        # 3. THE FIX: Extract JSON using Regex
        # This looks for the content between the first { and the last }
        match = _re.search(r'(\{.*\})', raw_text, _re.DOTALL)
        
        if match:
            json_str = match.group(1)
            result = _json.loads(json_str)
            
            # Map 'content' to 'report' if AI uses the wrong key
            report_body = result.get('report') or result.get('content', '')
            
            return jsonify({
                'title': result.get('title', 'Event Report'),
                'report': report_body
            })
        else:
            # If no { } found at all
            print(f"DEBUG: Gemini sent non-JSON: {raw_text}")
            return jsonify({'error': 'AI response was not in JSON format'}), 500

    except Exception as e:
        return jsonify({'error': str(e), 'trace': traceback.format_exc()}), 500
    finally:
        cur.close()
        pool.putconn(conn)
@app.route('/api/admin/broadcast', methods=['POST'])
def broadcast_notification():

    data = request.get_json()

    event_id = data.get("event_id")
    message = data.get("message")
    notif_type = data.get("type")

    conn = get_db_connection()
    cur = conn.cursor()

    # get all registered users
    cur.execute("""
        SELECT user_id FROM registrations
        WHERE event_id = %s
    """, (event_id,))

    users = cur.fetchall()

    for user in users:
        cur.execute("""
            INSERT INTO notifications (user_id, event_id, title, message, type, date, is_read)
            VALUES (%s,%s,%s,%s,%s,NOW(),FALSE)
        """, (
            user[0],
            event_id,
            "Event Update",
            message,
            notif_type
        ))

    conn.commit()
    cur.close()
    pool.putconn(conn)

    return jsonify({"success": True})

@app.route('/api/notifications/<int:user_id>', methods=['GET'])
def get_notifications(user_id):

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, title, message, type, date, is_read
        FROM notifications
        WHERE user_id = %s
        ORDER BY date DESC
    """, (user_id,))

    rows = cur.fetchall()

    notifications = []
    for r in rows:
        notifications.append({
            "id": r[0],
            "title": r[1],
            "message": r[2],
            "type": r[3],
            "date": str(r[4]),
            "is_read": r[5]
        })

    cur.close()
    pool.putconn(conn)

    return jsonify(notifications)

@app.route('/api/notifications/<int:notification_id>', methods=['DELETE'])
def delete_notification(notification_id):

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM notifications
        WHERE id = %s
    """, (notification_id,))

    conn.commit()

    cur.close()
    pool.putconn(conn)

    return jsonify({"message": "Notification deleted"})

@app.route('/api/notifications/<int:user_id>', methods=['DELETE'])
def clear_notifications(user_id):

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("DELETE FROM notifications WHERE user_id = %s", (user_id,))
    conn.commit()

    cur.close()
    pool.putconn(conn)

    return jsonify({"message": "Notifications cleared"})

@app.route('/api/notifications/<int:notification_id>/read', methods=['POST'])
def mark_notification_read(notification_id):

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute(
        "UPDATE notifications SET is_read = TRUE WHERE id = %s",
        (notification_id,)
    )

    conn.commit()

    cur.close()
    pool.putconn(conn)

    return jsonify({"message": "Marked as read"})

if __name__ == '__main__':
    app.run(debug=False, port=5000)