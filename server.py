from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from pymongo import MongoClient
from uuid import uuid4
import os
from dotenv import load_dotenv
from werkzeug.exceptions import HTTPException
import datetime

# Load .env in local; Render provides env vars automatically
load_dotenv()

app = Flask(__name__, static_folder="static", template_folder="templates")
# Allow all origins so frontend on any domain can call this API
CORS(app, resources={r"/*": {"origins": "*"}})

MONGODB_URI = os.getenv("MONGODB_URI")
DB_NAME = os.getenv("DB_NAME", "quiz")
PORT = int(os.getenv("PORT", 3000))

if not MONGODB_URI:
    raise RuntimeError("MONGODB_URI is not set. Set it in environment variables.")

# Connect to MongoDB
client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
try:
    client.server_info()
except Exception as e:
    print("❌ Cannot connect to MongoDB. Check MONGODB_URI. Error:", e)
    raise

db = client[DB_NAME]
print(f"✅ Connected to MongoDB database: {DB_NAME}")

def remove_id(doc):
    if not doc:
        return doc
    doc.pop("_id", None)
    return doc

def remove_id_from_list(docs):
    return [remove_id(d) for d in docs]

# Generic error handler
@app.errorhandler(Exception)
def handle_exception(e):
    if isinstance(e, HTTPException):
        return jsonify({"message": e.description}), e.code
    return jsonify({"message": "Internal server error", "error": str(e)}), 500

# Health
@app.route("/healthz", methods=["GET"])
def health():
    return jsonify({"status": "ok", "db": DB_NAME})

# --------------------- AUTH ---------------------
@app.route("/login", methods=["POST"])
@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json() or {}
    user = data.get("user")
    passwd = data.get("pass")
    if not user or passwd is None:
        return jsonify({"success": False, "message": "Missing credentials"}), 400
    found = db.users.find_one({"user": user, "pass": passwd})
    if found:
        return jsonify({"success": True, "user": {"id": found.get("id"), "user": found.get("user"), "role": found.get("role")}})
    return jsonify({"success": False, "message": "Tên đăng nhập hoặc mật khẩu không đúng."}), 401

@app.route("/register", methods=["POST"])
@app.route("/api/register", methods=["POST"])
def register():
    data = request.get_json() or {}
    user = data.get("user"); passwd = data.get("pass")
    dob = data.get("dob"); gender = data.get("gender")
    if not user or passwd is None:
        return jsonify({"success": False, "message": "Missing user or pass"}), 400
    if db.users.find_one({"user": user}):
        return jsonify({"success": False, "message": "Tên tài khoản đã tồn tại."}), 409
    new_user = {"id": str(uuid4()), "user": user, "pass": passwd, "dob": dob, "gender": gender, "role": "student"}
    db.users.insert_one(new_user)
    to_return = new_user.copy()
    to_return.pop("_id", None)
    return jsonify({"success": True, "user": to_return}), 201

# --------------------- USERS ---------------------
@app.route("/users", methods=["GET"])
@app.route("/api/users", methods=["GET"])
def get_users():
    docs = list(db.users.find({}, {"_id": 0}))
    return jsonify(docs)

@app.route("/users/<user_id>", methods=["DELETE"])
@app.route("/api/users/<user_id>", methods=["DELETE"])
def delete_user(user_id):
    res = db.users.delete_one({"id": user_id})
    if res.deleted_count > 0:
        return "", 204
    return jsonify({"message": "Người dùng không tìm thấy."}), 404

# --------------------- QUESTIONS ---------------------
@app.route("/questions", methods=["GET"])
@app.route("/api/questions", methods=["GET"])
def list_questions():
    query = {}
    subject = request.args.get("subject")
    level = request.args.get("level")
    if subject: query["subject"] = subject
    if level: query["level"] = level
    docs = list(db.questions.find(query, {"_id": 0}))
    return jsonify(docs)

@app.route("/questions", methods=["POST"])
@app.route("/api/questions", methods=["POST"])
def create_question():
    data = request.get_json() or {}
    newq = {
        "id": str(uuid4()),
        "q": data.get("q"),
        "imageUrl": data.get("imageUrl"),
        "type": data.get("type"),
        "points": data.get("points"),
        "subject": data.get("subject"),
        "level": data.get("level"),
        "options": data.get("options")
    }
    db.questions.insert_one(newq)
    to_return = newq.copy(); to_return.pop("_id", None)
    return jsonify(to_return), 201

@app.route("/questions/<q_id>", methods=["GET"])
@app.route("/api/questions/<q_id>", methods=["GET"])
def get_question(q_id):
    doc = db.questions.find_one({"id": q_id}, {"_id": 0})
    if not doc: return jsonify({"message": "Câu hỏi không tồn tại."}), 404
    return jsonify(doc)

@app.route("/questions/<q_id>", methods=["PUT"])
@app.route("/api/questions/<q_id>", methods=["PUT"])
def update_question(q_id):
    data = request.get_json() or {}
    data.pop("_id", None)
    res = db.questions.update_one({"id": q_id}, {"$set": data})
    if res.matched_count > 0:
        updated = db.questions.find_one({"id": q_id}, {"_id": 0})
        return jsonify(updated)
    return jsonify({"message": "Câu hỏi không tồn tại."}), 404

@app.route("/questions/<q_id>", methods=["DELETE"])
@app.route("/api/questions/<q_id>", methods=["DELETE"])
def delete_question(q_id):
    res = db.questions.delete_one({"id": q_id})
    if res.deleted_count > 0:
        return "", 204
    return jsonify({"message": "Câu hỏi không tìm thấy."}), 404

# --------------------- TESTS ---------------------
@app.route("/tests", methods=["GET"])
@app.route("/api/tests", methods=["GET"])
def list_tests():
    # Bắt đầu khối hàm (thụt lề 4 dấu cách so với def)
    query = {}
    subject = request.args.get("subject")
    level = request.args.get("level")
    if subject: query["subject"] = subject
    if level: query["level"] = level
    docs = list(db.tests.find(query, {"_id": 0}))
    return jsonify(docs) # Đây là câu lệnh return hợp lệ, vì nó nằm trong hàm.
    # Kết thúc khối hàm

@app.route("/tests/<test_id>", methods=["GET"])
@app.route("/api/tests/<test_id>", methods=["GET"])
def get_test(test_id):
    doc = db.tests.find_one({"id": test_id}, {"_id": 0})
    if not doc: return jsonify({"message": "Bài kiểm tra không tồn tại."}), 404
    return jsonify(doc)

@app.route("/tests", methods=["POST"])
@app.route("/api/tests", methods=["POST"])
def create_test():
    data = request.get_json() or {}
    newt = {
        "id": str(uuid4()),
        "name": data.get("name"),
        "time": data.get("time"),
        "subject": data.get("subject"),
        "level": data.get("level"),
        "questions": data.get("questions", []),
        "teacherId": data.get("teacherId")
    }
    db.tests.insert_one(newt)
    to_return = newt.copy(); to_return.pop("_id", None)
    return jsonify(to_return), 201

@app.route("/tests/<test_id>", methods=["PUT"])
@app.route("/api/tests/<test_id>", methods=["PUT"])
def update_test(test_id):
    data = request.get_json() or {}
    data.pop("_id", None)
    res = db.tests.update_one({"id": test_id}, {"$set": data})
    if res.matched_count > 0:
        updated = db.tests.find_one({"id": test_id}, {"_id": 0})
        return jsonify(updated)
    return jsonify({"message": "Bài kiểm tra không tồn tại."}), 404

@app.route("/tests/<test_id>", methods=["DELETE"])
@app.route("/api/tests/<test_id>", methods=["DELETE"])
def delete_test(test_id):
    res = db.tests.delete_one({"id": test_id})
    if res.deleted_count > 0:
        return "", 204
    return jsonify({"message": "Bài kiểm tra không tìm thấy."}), 404

# --------------------- ASSIGNS ---------------------
@app.route("/assigns", methods=["GET"])
@app.route("/api/assigns", methods=["GET"])
def list_assigns():
    query = {}
    studentId = request.args.get("studentId")
    if studentId: query["studentId"] = studentId
    docs = list(db.assigns.find(query, {"_id": 0}))
    return jsonify(docs)

@app.route("/assigns", methods=["POST"])
@app.route("/api/assigns", methods=["POST"])
def create_assign():
    data = request.get_json() or {}
    newa = {
        "id": str(uuid4()),
        "testId": data.get("testId"),
        "studentId": data.get("studentId"),
        "deadline": data.get("deadline"),
        "status": data.get("status"),
        "timeAssigned": data.get("timeAssigned") or datetime.datetime.utcnow().isoformat()
    }
    db.assigns.insert_one(newa)
    to_return = newa.copy(); to_return.pop("_id", None)
    return jsonify(to_return), 201

@app.route("/api/assign-test", methods=["POST"])
def alias_assign_test():
    return create_assign()

# --------------------- RESULTS ---------------------
@app.route("/results", methods=["GET"])
@app.route("/api/results", methods=["GET"])
def list_results():
    query = {}
    studentId = request.args.get("studentId")
    if studentId: query["studentId"] = studentId
    docs = list(db.results.find(query, {"_id": 0}))
    return jsonify(docs)

@app.route("/results", methods=["POST"])
@app.route("/api/results", methods=["POST"])
def create_result():
    data = request.get_json() or {}
    newr = {"id": str(uuid4()), **data, "submittedAt": datetime.datetime.utcnow().isoformat()}
    db.results.insert_one(newr)
    to_return = newr.copy(); to_return.pop("_id", None)
    return jsonify(to_return), 201

@app.route("/results/<result_id>", methods=["GET"])
@app.route("/api/results/<result_id>", methods=["GET"])
def get_result(result_id):
    doc = db.results.find_one({"id": result_id}, {"_id": 0})
    if not doc: return jsonify({"message": "Kết quả không tìm thấy."}), 404
    return jsonify(doc)

# Serve frontend files (unchanged)
@app.route("/", methods=["GET"])
def index():
    try:
        return send_from_directory(".", "index.html")
    except Exception:
        return jsonify({"message": "Index not found"}), 404

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
