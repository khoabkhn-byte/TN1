from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from pymongo import MongoClient
from uuid import uuid4
import os
from dotenv import load_dotenv
from werkzeug.exceptions import HTTPException
import datetime, random

# Load .env in local; Render provides env vars automatically
load_dotenv()

app = Flask(__name__, static_folder="static", template_folder="templates")
app = Flask(__name__, static_folder="static", template_folder="templates")

# ✅ Cho phép tất cả domain truy cập (bao gồm file://)
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)

@app.after_request
def apply_cors_headers(response):
    """Fix CORS preflight for local files & Render"""
    response.headers.add("Access-Control-Allow-Origin", "*")
    response.headers.add("Access-Control-Allow-Headers", "Content-Type,Authorization")
    response.headers.add("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS")
    response.headers.add("Access-Control-Allow-Credentials", "true")
    return response
MONGODB_URI = os.getenv("MONGODB_URI")
DB_NAME = os.getenv("DB_NAME", "quiz")
PORT = int(os.getenv("PORT", 3000))

if not MONGODB_URI:
    raise RuntimeError("MONGODB_URI is not set. Set it in environment variables.")

from pymongo import MongoClient, errors
import time

# === MongoDB Connection (Optimized for Render) ===
def connect_mongo():
    retries = 5
    for i in range(retries):
        try:
            client = MongoClient(
                MONGODB_URI,
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=5000,
                socketTimeoutMS=None,
                maxPoolSize=20,
                minPoolSize=2,
                maxIdleTimeMS=60000,   # Giữ kết nối tối đa 60s khi idle
                connect=False,         # Lazy connection, chỉ kết nối khi cần
                retryWrites=True,
                tls=True if "mongodb+srv" in MONGODB_URI else False
            )
            client.admin.command('ping')  # Kiểm tra kết nối
            print("✅ MongoDB connected successfully")
            return client
        except errors.ServerSelectionTimeoutError as e:
            print(f"⚠️ MongoDB connection failed (attempt {i+1}/{retries}): {e}")
            time.sleep(3)
        except Exception as e:
            print(f"❌ Unexpected error: {e}")
            time.sleep(3)
    raise RuntimeError("❌ Cannot connect to MongoDB after multiple retries.")

client = connect_mongo()
db = client[DB_NAME]

def remove_id(doc):
    if not doc: return doc
    doc.pop("_id", None)
    return doc

def remove_id_from_list(docs):
    return [remove_id(d) for d in docs]

# --------------------- ERROR ---------------------
@app.errorhandler(Exception)
def handle_exception(e):
    if isinstance(e, HTTPException):
        return jsonify({"message": e.description}), e.code
    return jsonify({"message": "Internal server error", "error": str(e)}), 500

# --------------------- HEALTH ---------------------
@app.route("/healthz")
def health():
    return jsonify({"status": "ok", "db": DB_NAME})

# --------------------- AUTH ---------------------
@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json() or {}
    user, passwd = data.get("user"), data.get("pass")
    if not user or passwd is None:
        return jsonify({"success": False, "message": "Missing credentials"}), 400
    found = db.users.find_one({"user": user, "pass": passwd})
    if found:
        return jsonify({"success": True, "user": {"id": found.get("id"), "user": found.get("user"), "role": found.get("role")}})
    return jsonify({"success": False, "message": "Tên đăng nhập hoặc mật khẩu không đúng."}), 401

@app.route("/api/register", methods=["POST"])
def register():
    data = request.get_json() or {}
    user, passwd = data.get("user"), data.get("pass")
    if not user or passwd is None:
        return jsonify({"success": False, "message": "Thiếu thông tin đăng ký."}), 400
    if db.users.find_one({"user": user}):
        return jsonify({"success": False, "message": "Tên tài khoản đã tồn tại."}), 409
    new_user = {"id": str(uuid4()), "user": user, "pass": passwd, "role": "student", "createdAt": datetime.datetime.utcnow().isoformat()}
    db.users.insert_one(new_user)
    return jsonify({"success": True, "user": remove_id(new_user)}), 201

# --------------------- QUESTIONS ---------------------
@app.route("/api/questions", methods=["GET"])
def list_questions():
    query = {}
    subject = request.args.get("subject")
    level = request.args.get("level")
    q_type = request.args.get("type")
    if subject: query["subject"] = subject
    if level: query["level"] = level
    if q_type: query["type"] = q_type
    docs = list(db.questions.find(query, {"_id": 0}))
    return jsonify(docs)

@app.route("/api/questions", methods=["POST"])
def create_question():
    data = request.get_json() or {}
    newq = {
        "id": str(uuid4()),
        "q": data.get("q"),
        "imageUrl": data.get("imageUrl"),
        "type": data.get("type"),
        "points": data.get("points", 1),
        "subject": data.get("subject"),
        "level": data.get("level"),
        "difficulty": data.get("difficulty", "medium"),
        "options": data.get("options", [])
    }
    db.questions.insert_one(newq)
    return jsonify(remove_id(newq)), 201

# --------------------- TESTS ---------------------
@app.route("/api/tests", methods=["GET"])
def list_tests():
    query = {}
    subject = request.args.get("subject")
    level = request.args.get("level")
    if subject: query["subject"] = subject
    if level: query["level"] = level
    docs = list(db.tests.find(query, {"_id": 0}))
    return jsonify(docs)

@app.route("/api/tests/<test_id>", methods=["GET"])
def get_test(test_id):
    doc = db.tests.find_one({"id": test_id}, {"_id": 0})
    if not doc: return jsonify({"message": "Bài kiểm tra không tồn tại."}), 404
    return jsonify(doc)

@app.route("/api/tests", methods=["POST"])
def create_test():
    data = request.get_json() or {}
    newt = {
        "id": str(uuid4()),
        "name": data.get("name"),
        "time": data.get("time", 45),
        "subject": data.get("subject"),
        "level": data.get("level"),
        "questions": data.get("questions", []),
        "teacherId": data.get("teacherId"),
        "pointsEach": data.get("pointsEach", 1),
        "createdAt": datetime.datetime.utcnow().isoformat()
    }
    db.tests.insert_one(newt)
    return jsonify(remove_id(newt)), 201

# ----------- TẠO BÀI KIỂM TRA NGẪU NHIÊN NÂNG CAO -----------
@app.route("/api/tests/auto", methods=["POST"])
def create_auto_test():
    data = request.get_json(force=True)
    subject = data.get("subject", "")
    level = data.get("level", "")
    total = int(data.get("count", 10))
    dist = data.get("dist", {})
    teacher = data.get("createdBy", "system")

    dist_easy = int(dist.get("easy", 0))
    dist_medium = int(dist.get("medium", 0))
    dist_hard = int(dist.get("hard", 0))

    # Lọc câu hỏi theo môn & khối
    q_filter = {}
    if subject: q_filter["subject"] = subject
    if level: q_filter["level"] = level
    all_questions = list(db.questions.find(q_filter))

    if not all_questions:
        return jsonify({"success": False, "message": "Không có câu hỏi phù hợp trong ngân hàng."}), 400

    # Phân loại độ khó
    easy_q = [q for q in all_questions if q.get("difficulty") in ["easy", 1, "1"] or str(q.get("level")) in ["1","2"]]
    medium_q = [q for q in all_questions if q.get("difficulty") in ["medium", 2, "2", "3"]]
    hard_q = [q for q in all_questions if q.get("difficulty") in ["hard", 3, "4", "5"]]

    def pick_random(arr, n):
        return random.sample(arr, min(len(arr), n)) if arr else []

    selected = []
    selected += pick_random(easy_q, dist_easy)
    selected += pick_random(medium_q, dist_medium)
    selected += pick_random(hard_q, dist_hard)

    # Bổ sung ngẫu nhiên nếu thiếu
    if len(selected) < total:
        remaining = [q for q in all_questions if q not in selected]
        selected += pick_random(remaining, total - len(selected))

    if not selected:
        return jsonify({"success": False, "message": "Không chọn được câu hỏi nào."}), 400

    per_point = round(10 / len(selected), 2)
    test_name = data.get("name", "Bài ngẫu nhiên") + f" ({len(selected)} câu)"

    new_test = {
        "id": str(uuid4()),
        "name": test_name,
        "subject": subject or "Tổng hợp",
        "level": level or "All",
        "time": data.get("time", 45),
        "questions": [q.get("id") or q.get("_id") for q in selected],
        "pointsEach": per_point,
        "createdBy": teacher,
        "createdAt": datetime.datetime.utcnow().isoformat(),
        "type": "auto"
    }

    db.tests.insert_one(new_test)
    return jsonify({"success": True, "test": remove_id(new_test)}), 201

# --------------------- RESULTS ---------------------
@app.route("/api/results", methods=["GET"])
def list_results():
    query = {}
    studentId = request.args.get("studentId")
    if studentId: query["studentId"] = studentId
    docs = list(db.results.find(query, {"_id": 0}))
    return jsonify(docs)

@app.route("/api/results", methods=["POST"])
def create_result():
    data = request.get_json() or {}
    newr = {"id": str(uuid4()), **data, "submittedAt": datetime.datetime.utcnow().isoformat()}
    db.results.insert_one(newr)
    return jsonify(remove_id(newr)), 201

# --------------------- STATIC ---------------------

@app.route("/", methods=["GET"])
def index():
    try:
        return send_from_directory(".", "index.html")
    except Exception:
        return jsonify({"message": "Index not found"}), 404


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
