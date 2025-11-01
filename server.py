# =================================================================
# SAO CHÉP VÀ THAY THẾ TOÀN BỘ FILE server31.py CỦA BẠN BẰNG CODE NÀY
# =================================================================

from bson.objectid import ObjectId
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from pymongo import MongoClient
from uuid import uuid4
import os
from dotenv import load_dotenv
from werkzeug.exceptions import HTTPException
from datetime import datetime, timedelta, timezone
import json
from werkzeug.utils import secure_filename
from gridfs import GridFS
import random # Thêm thư viện random
import traceback # Thêm thư viện traceback để debug
from io import BytesIO
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, PageBreak
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from flask import send_file

# Load .env in local; Render provides env vars automatically
load_dotenv()

app = Flask(__name__)
# Allow all origins so frontend on any domain can call this API
CORS(app, resources={r"/*": {"origins": "*"}})

# Tăng giới hạn dữ liệu request lên 25MB
app.config['MAX_CONTENT_LENGTH'] = 25 * 1024 * 1024 

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
    print(f"❌ Cannot connect to MongoDB. Check MONGODB_URI. Error: {e}")
    raise

db = client[DB_NAME]
fs = GridFS(db)
print(f"✅ Connected to MongoDB database: {DB_NAME}")

def remove_id(doc):
    if not doc:
        return doc
    doc.pop("_id", None)
    return doc

def remove_id_from_list(docs):
    return [remove_id(d) for d in docs]

# Hàm lấy thời gian hiện tại theo múi giờ Việt Nam (UTC+7)
def now_vn_iso():
    return datetime.now(timezone(timedelta(hours=7))).isoformat()

# ==================================================
# ✅ HÀM HELPER TÍNH ĐIỂM (THEO 5 QUY TẮC)
# ==================================================
def calculate_question_points(question_ids, db):
    """
    Nhận vào một danh sách ID câu hỏi (string UUIDs hoặc ObjectIds)
    Trả về một map: { "question_id": points }
    Áp dụng 5 quy tắc tính điểm, tổng là 10.
    """
    if not question_ids:
        return {}

    # 1. Lấy tất cả câu hỏi từ DB (Sử dụng logic truy vấn phức tạp)
    object_ids = []
    uuid_strings = []
    for qid_str in question_ids:
        try:
            object_ids.append(ObjectId(qid_str))
        except Exception:
            uuid_strings.append(qid_str)

    or_clauses = []
    if object_ids:
        or_clauses.append({"_id": {"$in": object_ids}})
    if uuid_strings:
        or_clauses.append({"id": {"$in": uuid_strings}})
    
    if not or_clauses:
        return {}
        
    questions = list(db.questions.find(
        {"$or": or_clauses},
        {"id": 1, "_id": 1, "difficulty": 1}
    ))
    
    if not questions:
        return {}

    # 2. Đếm số lượng E, M, H
    counts = {'easy': 0, 'medium': 0, 'hard': 0}
    question_map = {} # Map {id: difficulty}
    
    for q in questions:
        q_key = q.get('id') or str(q.get('_id'))
        difficulty = q.get('difficulty', 'medium')
        
        if difficulty not in counts:
            difficulty = 'medium'
            
        counts[difficulty] += 1
        question_map[q_key] = difficulty 

    num_easy = counts['easy']
    num_medium = counts['medium']
    num_hard = counts['hard']
    total_questions = len(questions)

    # 3. Khởi tạo điểm số
    points_per_difficulty = {'easy': 0, 'medium': 0, 'hard': 0}
    has_easy = num_easy > 0
    has_medium = num_medium > 0
    has_hard = num_hard > 0
    
    # ÁP DỤNG 5 QUY TẮC
    try:
        # Case 1: Đủ 3 loại (E, M, H)
        if has_easy and has_medium and has_hard:
            points_per_difficulty['medium'] = 1.0
            points_per_difficulty['easy'] = 0.5
            remaining_score = 10.0 - (num_medium * 1.0) - (num_easy * 0.5)
            
            if remaining_score < 0:
                print(f"Cảnh báo Quy tắc 1 (E={num_easy}, M={num_medium}, H={num_hard}): Tổng điểm E+M >= 10. Điểm câu khó sẽ là 0.")
                points_per_difficulty['hard'] = 0
            else:
                points_per_difficulty['hard'] = remaining_score / num_hard

        # Case 2: Chỉ 1 loại
        elif has_easy and not has_medium and not has_hard:
            points_per_difficulty['easy'] = 10.0 / num_easy
        elif not has_easy and has_medium and not has_hard:
            points_per_difficulty['medium'] = 10.0 / num_medium
        elif not has_easy and not has_medium and has_hard:
            points_per_difficulty['hard'] = 10.0 / num_hard
            
        # Case 3: 2 loại (Dễ + Trung bình) - M = 2*E
        elif has_easy and has_medium and not has_hard:
            denominator = num_easy + (2.0 * num_medium)
            points_per_difficulty['easy'] = 10.0 / denominator
            points_per_difficulty['medium'] = 2.0 * points_per_difficulty['easy']
            
        # Case 4: 2 loại (Trung bình + Khó) - H = 2*M
        elif not has_easy and has_medium and has_hard:
            denominator = num_medium + (2.0 * num_hard)
            points_per_difficulty['medium'] = 10.0 / denominator
            points_per_difficulty['hard'] = 2.0 * points_per_difficulty['medium']
            
        # Case 5: 2 loại (Dễ + Khó) - H = 1.5*E
        elif has_easy and not has_medium and has_hard:
            denominator = num_easy + (1.5 * num_hard)
            points_per_difficulty['easy'] = 10.0 / denominator
            points_per_difficulty['hard'] = 1.5 * points_per_difficulty['easy']
        
        else:
            print("Cảnh báo: Không có câu hỏi nào được tìm thấy để tính điểm.")

    except ZeroDivisionError:
        print(f"Lỗi chia cho 0 khi tính điểm (E={num_easy}, M={num_medium}, H={num_hard}). Trả về điểm mặc định.")
        default_points = 10.0 / total_questions
        return {q_id: default_points for q_id in question_map.keys()}

    # 4. Tạo map {id: points} cuối cùng
    result_map = {}
    for q_id, difficulty in question_map.items():
        result_map[q_id] = round(points_per_difficulty[difficulty], 2)

    return result_map

# ==================================================
# ✅ HÀM HELPER CŨ (ĐỂ TÍNH COUNT)
# ==================================================
def calculate_question_counts(question_ids, db):
    """Tính toán số câu MC và Essay từ danh sách ID câu hỏi."""
    if not question_ids:
        return 0, 0

    object_ids = []
    uuid_strings = []
    for qid_str in question_ids:
        try:
            object_ids.append(ObjectId(qid_str))
        except Exception:
            uuid_strings.append(qid_str)

    or_clauses = []
    if object_ids:
        or_clauses.append({"_id": {"$in": object_ids}})
    if uuid_strings:
        or_clauses.append({"id": {"$in": uuid_strings}})

    if not or_clauses:
        return 0, 0
        
    question_types = list(db.questions.find(
        {"$or": or_clauses},
        {"type": 1, "options": 1} # Lấy cả 'options' để fallback
    ))

    mc_count = 0
    essay_count = 0

    for q in question_types:
        q_type = q.get("type", "").lower()
        if q_type == "mc":
            mc_count += 1
        elif q_type == "essay":
            essay_count += 1
        elif not q_type: # Fallback
             if q.get("options") and len(q.get("options")) > 0:
                mc_count += 1
             else:
                essay_count += 1

    return mc_count, essay_count
# ==================================================


@app.route("/api/test-deploy", methods=["GET"])
def test_deploy():
    return jsonify({"status": "SUCCESS", "version": "v1.2-Point_Logic_Fix"})

# ------------------ GENERIC ERROR HANDLER ------------------
@app.errorhandler(Exception)
def handle_exception(e):
    if isinstance(e, HTTPException):
        return jsonify({ "success": False, "message": e.description }), e.code
    traceback.print_exc() # In lỗi chi tiết ra log server
    return jsonify({ "success": False, "message": "Internal server error", "error": str(e) }), 500

# ... (Hàm /healthz và /login giữ nguyên) ...
@app.route("/healthz", methods=["GET"])
def health():
    try:
        db_stats = db.command("ping")
        db_status = "connected" if db_stats.get("ok") == 1.0 else "error"
    except Exception as e:
        db_status = f"error: {str(e)}"
    return jsonify({ "status": "ok", "db": DB_NAME, "db_status": db_status })

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
        return jsonify({"success": True, "user": {
            "id": found.get("id"), 
            "user": found.get("user"), 
            "role": found.get("role"),
            "fullName": found.get("fullName"),
            "className": found.get("className")
        }})
    return jsonify({"success": False, "message": "Tên đăng nhập hoặc mật khẩu không đúng."}), 401

# ... (Các hàm /register, /users, /users/<id> GET/PUT/DELETE giữ nguyên) ...
@app.route("/register", methods=["POST"])
@app.route("/api/register", methods=["POST"])
@app.route("/api/users", methods=["POST"])
def register():
    data = request.get_json() or {}
    user = data.get("user"); passwd = data.get("pass")
    level = data.get("level",0)
    fullName = data.get("fullName"); className = data.get("className")
    gender = data.get("gender")
    role_to_save = data.get("role", "student")
    if role_to_save == "admin":
        role_to_save = "teacher"
    if not user or passwd is None:
        return jsonify({"success": False, "message": "Missing user or pass"}), 400
    if db.users.find_one({"user": user}):
        return jsonify({"success": False, "message": "Tên tài khoản đã tồn tại."}), 409
    
    new_user = {
        "id": str(uuid4()), 
        "user": user, 
        "pass": passwd, 
        "fullName": fullName,
        "className": className,
        "gender": gender, 
        "level": level,
        "role": role_to_save # ✅ Sửa: Dùng biến đã qua xử lý
    }
    db.users.insert_one(new_user)
    to_return = new_user.copy()
    to_return.pop("_id", None)
    return jsonify({"success": True, "user": to_return}), 201

@app.route("/users", methods=["GET"])
@app.route("/api/users", methods=["GET"])
def get_users():
    query = {}
    role = request.args.get("role")
    if role: query["role"] = role
    className = request.args.get("class")
    if className: query["className"] = className 
    nameSearch = request.args.get("name")
    if nameSearch: query["fullName"] = {"$regex": nameSearch, "$options": "i"} 
    gender = request.args.get("gender")
    if gender: query["gender"] = gender 
    docs = list(db.users.find(query, {"_id": 0}))
    return jsonify(docs)

@app.route("/users/<user_id>", methods=["GET"])
@app.route("/api/users/<user_id>", methods=["GET"])
def get_user(user_id):
    doc = db.users.find_one({"id": user_id}, {"_id": 0})
    if not doc:
        return jsonify({"message": "Người dùng không tìm thấy."}), 404
    return jsonify(doc)

@app.route("/users/<user_id>", methods=["PUT", "PATCH"])
@app.route("/api/users/<user_id>", methods=["PUT", "PATCH"])
def update_user(user_id):
    data = request.get_json() or {}
    update_fields = {}
    
    if "user" in data: update_fields["user"] = data["user"]
    if "pass" in data: update_fields["pass"] = data["pass"]
    if "role" in data:
        role_to_update = data["role"]
        if role_to_update == "admin":
            role_to_update = "teacher"
        update_fields["role"] = role_to_update
    if "fullName" in data: update_fields["fullName"] = data["fullName"]
    if "className" in data: update_fields["className"] = data["className"]
    if "dob" in data: update_fields["dob"] = data["dob"]
    if "gender" in data: update_fields["gender"] = data["gender"]
    if "level" in data: update_fields["level"] = data["level"]
        
    if not update_fields:
        return jsonify({"message": "Không có trường nào được cung cấp để cập nhật."}), 400

    res = db.users.update_one({"id": user_id}, {"$set": update_fields})
    if res.matched_count == 0:
        return jsonify({"message": "Người dùng không tìm thấy."}), 404
    updated_user = db.users.find_one({"id": user_id}, {"_id": 0})
    return jsonify(updated_user), 200

@app.route("/users/<user_id>", methods=["DELETE"])
@app.route("/api/users/<user_id>", methods=["DELETE"])
def delete_user(user_id):
    res = db.users.delete_one({"id": user_id})
    if res.deleted_count > 0:
        return "", 204
    return jsonify({"message": "Người dùng không tìm thấy."}), 404
#--In PDF ĐỀ THI
@app.route("/api/export-tests", methods=["GET"])
def export_tests_pdf():
    ids_param = request.args.get("ids", "")
    test_ids = [i.strip() for i in ids_param.split(",") if i.strip()]
    if not test_ids:
        return jsonify({"error": "Thiếu danh sách ID"}), 400

    tests = list(db.tests.find({"id": {"$in": test_ids}}, {"_id": 0}))
    if not tests:
        return jsonify({"error": "Không tìm thấy đề"}), 404

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    story = []

    for t in tests:
        story.append(Paragraph(f"<b>{t.get('name', 'Đề thi')}</b>", styles["Title"]))
        story.append(Paragraph(f"<b>Môn:</b> {t.get('subject', '')} — <b>Khối:</b> {t.get('level', '')}", styles["Normal"]))
        story.append(Spacer(1, 12))

        for idx, q in enumerate(t.get("questions", []), start=1):
            story.append(Paragraph(f"<b>Câu {idx}:</b> {q.get('q','')}", styles["Normal"]))
            story.append(Spacer(1, 6))

            if q.get("imageId"):
                try:
                    file_obj = fs.get(ObjectId(q["imageId"]))
                    img = ImageReader(file_obj)
                    story.append(Image(img, width=400, height=200))
                    story.append(Spacer(1, 6))
                except Exception:
                    pass

            if q.get("options"):
                for opt in q["options"]:
                    story.append(Paragraph(f"- {opt}", styles["Normal"]))
                story.append(Spacer(1, 8))
        story.append(PageBreak())

    doc.build(story)
    buffer.seek(0)
    return send_file(buffer, as_attachment=True,
                     download_name="de_thi.pdf", mimetype="application/pdf")


# ... (Các hàm /questions... (GET, POST, PUT, DELETE, image) giữ nguyên) ...
@app.route("/questions/image/<file_id>", methods=["GET"])
def get_question_image(file_id):
    try:
        file_obj = fs.get(ObjectId(file_id))
        return send_file(file_obj, mimetype=file_obj.content_type, as_attachment=False)
    except Exception as e:
        print("❌ Lỗi lấy ảnh:", e)
        return jsonify({"message": f"File not found: {str(e)}"}), 404

@app.route("/questions", methods=["GET"])
@app.route("/api/questions", methods=["GET"])
def list_questions():
    query = {}
    subject = request.args.get("subject")
    level = request.args.get("level")
    q_type = request.args.get("type") 
    difficulty = request.args.get("difficulty")
    search_keyword = request.args.get("search") 
    if subject: query["subject"] = subject
    if level: query["level"] = level
    if q_type: query["type"] = q_type
    if difficulty: query["difficulty"] = difficulty
    if search_keyword:
        query["q"] = {"$regex": search_keyword, "$options": "i"} 
    
    # === LOGIC MỚI BẮT ĐẦU ===
    # 1. Lấy tất cả ID câu hỏi (UUID) nằm trong các đề đã được giao
    assigned_test_ids = set(db.assignments.distinct("testId"))
    assigned_q_ids = set()
    
    if assigned_test_ids:
        # Dùng pipeline để lấy tất cả question.id từ các test đã giao
        pipeline = [
            {"$match": {"id": {"$in": list(assigned_test_ids)}}},
            {"$unwind": "$questions"},
            {"$group": {"_id": "$questions.id"}} # Gom nhóm theo question.id
        ]
        assigned_q_refs = list(db.tests.aggregate(pipeline))
        # Tạo một Set chứa các ID (UUID) của câu hỏi đã được giao
        assigned_q_ids = {q_ref["_id"] for q_ref in assigned_q_refs if q_ref["_id"]}
    # === LOGIC MỚI KẾT THÚC ===

    docs = list(db.questions.find(query))
    for doc in docs:
        # Thêm cờ 'isAssigned' vào tài liệu
        q_uuid = doc.get("id")
        doc['isAssigned'] = (q_uuid in assigned_q_ids)
        doc['_id'] = str(doc['_id'])
        
    return jsonify(docs)

@app.route("/questions", methods=["POST"])
@app.route("/api/questions", methods=["POST"])
def create_question():
    data = request.form
    image_file = request.files.get("image")
    image_id = None
    if image_file:
        filename = secure_filename(image_file.filename)
        content_type = image_file.mimetype
        try:
            image_id = fs.put(image_file, filename=filename, content_type=content_type)
        except Exception as e:
            return jsonify({"message": f"Lỗi lưu file: {str(e)}"}), 500
    try:
        options = json.loads(data.get("options", "[]"))
        answer = data.get("answer", "")
    except json.JSONDecodeError:
        return jsonify({"message": "Lỗi định dạng dữ liệu Options hoặc Answer."}), 400
    newq = {
        "id": str(uuid4()),
        "q": data.get("q"),
        "type": data.get("type"),
        "points": int(data.get("points", 1)),
        "subject": data.get("subject"),
        "level": data.get("level"),
        "difficulty": data.get("difficulty", "medium"),
        "options": options,
        "answer": answer,
        "imageId": str(image_id) if image_id else None
    }
    db.questions.insert_one(newq)
    to_return = newq.copy()
    to_return.pop("_id", None)
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
    
    # === LOGIC MỚI BẮT ĐẦU ===
    # q_id ở đây là UUID (question.id)
    # 1. Tìm tất cả các test ID có chứa câu hỏi này
    tests_with_q = list(db.tests.find({"questions.id": q_id}, {"id": 1}))
    if tests_with_q:
        test_ids = [t['id'] for t in tests_with_q]
        
        # 2. Kiểm tra xem bất kỳ test nào trong số đó đã được giao chưa
        if db.assignments.find_one({"testId": {"$in": test_ids}}):
            return jsonify({"success": False, "message": "Câu hỏi nằm trong đề đã được giao không thể sửa."}), 403 # 403 Forbidden
    # === LOGIC MỚI KẾT THÚC ===

    data = request.form
    image_file = request.files.get("image")
    remove_old = data.get("removeOldImage", "false") == "true"
    question = db.questions.find_one({"id": q_id})
    if not question:
        return jsonify({"message": "Không tìm thấy câu hỏi"}), 404
    image_id = question.get("imageId")
    if remove_old and image_id:
        try:
            fs.delete(ObjectId(image_id))
        except Exception:
            pass
        image_id = None
    if image_file:
        try:
            filename = secure_filename(image_file.filename)
            content_type = image_file.mimetype
            new_image_id = fs.put(image_file, filename=filename, content_type=content_type)
            image_id = str(new_image_id)
        except Exception as e:
            return jsonify({"message": f"Lỗi upload ảnh mới: {str(e)}"}), 500
    try:
        options = json.loads(data.get("options", "[]"))
        answer = data.get("answer", "")
    except json.JSONDecodeError:
        return jsonify({"message": "Lỗi định dạng dữ liệu Options hoặc Answer."}), 400
    update_fields = {
        "q": data.get("q"),
        "type": data.get("type"),
        "points": int(data.get("points", 1)),
        "subject": data.get("subject"),
        "level": data.get("level"),
        "difficulty": data.get("difficulty", "medium"),
        "options": options,
        "answer": answer,
        "imageId": image_id
    }
    res = db.questions.update_one({"id": q_id}, {"$set": update_fields})
    if res.matched_count == 0:
        return jsonify({"message": "Câu hỏi không tồn tại."}), 404
    updated = db.questions.find_one({"id": q_id}, {"_id": 0})
    return jsonify(updated), 200

@app.route("/questions/<q_id>", methods=["DELETE"])
@app.route("/api/questions/<q_id>", methods=["DELETE"])
def delete_question(q_id):
    
    # === LOGIC MỚI BẮT ĐẦU ===
    # q_id ở đây là UUID (question.id)
    # 1. Tìm tất cả các test ID có chứa câu hỏi này
    tests_with_q = list(db.tests.find({"questions.id": q_id}, {"id": 1}))
    if tests_with_q:
        test_ids = [t['id'] for t in tests_with_q]
        
        # 2. Kiểm tra xem bất kỳ test nào trong số đó đã được giao chưa
        if db.assignments.find_one({"testId": {"$in": test_ids}}):
            return jsonify({"success": False, "message": "Câu hỏi nằm trong đề đã được giao, không thể xóa."}), 403 # 403 Forbidden
    # === LOGIC MỚI KẾT THÚC ===

    res = db.questions.delete_one({"id": q_id})
    if res.deleted_count > 0:
        return "", 204
    return jsonify({"message": "Câu hỏi không tìm thấy."}), 404

@app.route("/images/<image_id>", methods=["GET"])
def get_image(image_id):
    try:
        file_obj = fs.get(ObjectId(image_id))
        return app.response_class(file_obj.read(), mimetype=file_obj.content_type)
    except Exception as e:
        return jsonify({"message": "Không tìm thấy ảnh", "error": str(e)}), 404

# ... (Hàm /test.html và /tests (GET) giữ nguyên) ...
@app.route('/test.html')
def serve_test_html():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    return send_from_directory(current_dir, 'test.html') 

@app.route("/tests", methods=["GET"])
@app.route("/api/tests", methods=["GET"])
def list_tests():
    query = {}
    subject = request.args.get("subject")
    level = request.args.get("level")
    createdAtGte = request.args.get("createdAtGte") 
    if subject: query["subject"] = subject
    if level: query["level"] = level
    if createdAtGte:
        query["createdAt"] = {"$gte": createdAtGte}
    docs = list(db.tests.find(query, {"_id": 0}))
    return jsonify(docs)

# ... (Hàm /tests/<test_id> (GET) giữ nguyên, nó đã rất tốt) ...
@app.route("/quizzes/<test_id>", methods=["GET"])
@app.route("/api/quizzes/<test_id>", methods=["GET"])
@app.route("/tests/<test_id>", methods=["GET"])
@app.route("/api/tests/<test_id>", methods=["GET"])
def get_test(test_id):
    doc = db.tests.find_one({"id": test_id}, {"_id": 0})
    if not doc:
        doc = db.quizzes.find_one({"id": test_id}, {"_id": 0})
    if not doc:
        return jsonify({"message": "Bài kiểm tra không tồn tại."}), 404

    question_list = doc.get("questions", [])
    if not question_list:
        return jsonify(doc)

    first_item = question_list[0]
    
    # ✅ SỬA LỖI BUG 2 (HIỂN THỊ ĐIỂM 0 KHI SỬA):
    # Logic cũ của bạn (isinstance(first_item, dict)) bị sai
    # Logic mới: Kiểm tra xem 'points' có trong dict không.
    if isinstance(first_item, dict) and "points" in first_item:
        # Đã là định dạng mới: [{"id": "...", "points": 1.5}]
        # Giờ chúng ta cần BÙ ĐẮP (hydrate) nó với nội dung câu hỏi
        ids_to_resolve = [q.get("id") for q in question_list if q.get("id")]
        points_map = {q.get("id"): q.get("points") for q in question_list}
    
    elif isinstance(first_item, dict) and "q" in first_item:
        # Đây là định dạng rất cũ (lưu full câu hỏi), chỉ cần trả về
         return jsonify(doc)
         
    else:
        # Đây là định dạng cũ: ["id1", "id2"]
        # Chúng ta phải TÍNH TOÁN LẠI ĐIỂM theo 5 quy tắc
        ids_to_resolve = [str(q) for q in question_list]
        print(f"Cảnh báo: Đề thi {test_id} dùng logic điểm cũ. Đang tính toán lại...")
        points_map = calculate_question_points(ids_to_resolve, db)

    # BÙ ĐẮP (HYDRATE)
    object_ids = []
    uuid_strings = []
    for qid_str in ids_to_resolve:
        try:
            object_ids.append(ObjectId(qid_str))
        except Exception:
            uuid_strings.append(qid_str)

    or_clauses = []
    if object_ids: or_clauses.append({"_id": {"$in": object_ids}})
    if uuid_strings: or_clauses.append({"id": {"$in": uuid_strings}})

    full_questions = []
    if or_clauses:
        full_questions = list(db.questions.find({"$or": or_clauses}))

    id_map = {}
    for q in full_questions:
        if q.get("id"): id_map[str(q.get("id"))] = q
        if q.get("_id"): id_map[str(q.get("_id"))] = q

    final_questions = []
    for qid in ids_to_resolve:
        if qid in id_map:
            q_full = id_map[qid].copy()
            q_full["_id"] = str(q_full.get("_id"))
            q_full["id"] = q_full.get("id") or q_full["_id"]
            
            # ✅ GÁN ĐIỂM ĐÃ TÍNH (TỪ 5 QUY TẮC) VÀO
            q_full["points"] = points_map.get(qid, 1.0)
            
            final_questions.append(q_full)
        else:
            app.logger.warning(f"Question id {qid} not found in questions collection.")

    doc["questions"] = final_questions
    for q in doc.get("questions", []):
        if "type" not in q or not q["type"]:
            if q.get("options") and len(q["options"]) > 0:
                q["type"] = "mc"
            else:
                q["type"] = "essay"
    return jsonify(doc)

# ==================================================
# ✅ THAY THẾ HÀM TẠO ĐỀ THỦ CÔNG (Dòng 483)
# ==================================================
@app.route("/tests", methods=["POST"])
@app.route("/api/tests", methods=["POST"])
def create_test():
    data = request.get_json() or {}
    
    name = data.get("name", "Bài thi thủ công")
    time = data.get("time", 45)
    level = data.get("level")
    subject = data.get("subject")
    
    # JS (hàm saveManualTest) gửi một danh sách các string _id
    question_oids_from_fe = data.get("questions", []) 

    if not subject:
        return jsonify({"success": False, "message": "Vui lòng chọn Môn học"}), 400
    if not question_oids_from_fe:
        return jsonify({"success": False, "message": "Vui lòng chọn ít nhất 1 câu hỏi"}), 400

    # 1. CHUYỂN ĐỔI _id SANG id (UUID)
    object_ids = []
    for oid_str in question_oids_from_fe:
        try:
            object_ids.append(ObjectId(oid_str))
        except Exception:
            pass 

    questions_from_db = list(db.questions.find(
        {"_id": {"$in": object_ids}},
        {"id": 1, "_id": 1} # Chỉ lấy 2 trường ID
    ))
    
    id_map = {str(q.get("_id")): q.get("id") for q in questions_from_db if q.get("id")}
    
    question_uuids_to_save = []
    for oid_str in question_oids_from_fe:
        uuid = id_map.get(oid_str)
        if uuid:
            question_uuids_to_save.append(uuid)
        else:
            question_uuids_to_save.append(oid_str) 
            
    # 2. GỌI HÀM TÍNH ĐIỂM (dùng ID nào cũng được)
    points_map = calculate_question_points(question_uuids_to_save, db)

    # 3. Định dạng lại mảng câu hỏi để lưu vào DB
    formatted_questions = []
    mc_count, essay_count = calculate_question_counts(question_uuids_to_save, db)

    for q_id in question_uuids_to_save: 
        points = points_map.get(q_id, 0)
        formatted_questions.append({
            "id": q_id,      # ✅ ĐÃ LƯU BẰNG id (UUID)
            "points": points 
        })

    # 4. Tạo tài liệu Test mới
    new_test = {
        "id": str(uuid4()),
        "name": name,
        "time": time,
        "level": level,
        "subject": subject,
        "questions": formatted_questions, 
        "isAutoGenerated": False,
        "createdAt": now_vn_iso(),
        "mcCount": mc_count,
        "essayCount": essay_count,
        # ✅ SỬA LỖI NAMEERROR TẠI ĐÂY:
        "count": len(question_uuids_to_save) 
    }

    # 5. Lưu vào DB
    try:
        db.tests.insert_one(new_test)
        new_test.pop('_id', None) 
        return jsonify(new_test), 201
    except Exception as e:
        return jsonify({"success": False, "message": f"Lỗi server: {e}"}), 500

# ==================================================
# ✅ THAY THẾ HÀM TẠO ĐỀ TỰ ĐỘNG (Dòng 542)
# ==================================================
@app.route("/tests/auto", methods=["POST"])
@app.route("/api/tests/auto", methods=["POST"])
def create_test_auto():
    data = request.get_json() or {}
    
    name = data.get("name", "Bài thi tự động")
    subject = data.get("subject", "")
    level = data.get("level", "")
    time = int(data.get("time", 30))
    dist = data.get("dist", {"easy": 0, "medium": 0, "hard": 0})
    
    num_easy = int(dist.get("easy", 0))
    num_medium = int(dist.get("medium", 0))
    num_hard = int(dist.get("hard", 0))
    total_questions_needed = num_easy + num_medium + num_hard
    
    if total_questions_needed == 0:
        return jsonify({"success": False, "message": "Vui lòng chọn ít nhất 1 câu hỏi"}), 400

    query = {}
    if subject: query["subject"] = subject
    if level: query["level"] = level

    def pick(diff, count):
        if count == 0: return []
        q = {**query, "difficulty": diff}
        pipeline = [
            {"$match": q},
            {"$sample": {"size": count}},
            {"$project": {"id": 1, "_id": 1, "type": 1}}
        ]
        return list(db.questions.aggregate(pipeline))

    easy_questions = pick("easy", num_easy)
    medium_questions = pick("medium", num_medium)
    hard_questions = pick("hard", num_hard)
    
    all_questions = easy_questions + medium_questions + hard_questions
    
    # Lấy ID (ưu tiên 'id', fallback về str(_id))
    all_question_ids = [q.get('id') or str(q.get('_id')) for q in all_questions]
    
    if not all_question_ids:
         return jsonify({"success": False, "message": "Không tìm thấy câu hỏi nào phù hợp"}), 404

    # 1. ✅ GỌI HÀM TÍNH ĐIỂM MỚI
    points_map = calculate_question_points(all_question_ids, db)

    # 2. Định dạng mảng câu hỏi và đếm type
    formatted_questions = []
    mc_count = 0
    essay_count = 0
    
    for q in all_questions:
        q_id = q.get('id') or str(q.get('_id'))
        points = points_map.get(q_id, 0)
        formatted_questions.append({
            "id": q_id,
            "points": points
        })
        if q.get('type') == 'essay':
            essay_count += 1
        else:
            mc_count += 1
            
    # 3. Tạo tài liệu Test mới
    new_test = {
        "id": str(uuid4()),
        "name": name,
        "time": time,
        "subject": subject,
        "level": level,
        "questions": formatted_questions, # ✅ Mảng câu hỏi đã chứa điểm
        "isAutoGenerated": True,
        "createdAt": now_vn_iso(),
        "mcCount": mc_count,
        "essayCount": essay_count,
        "count": len(formatted_questions)
    }
    
    # 4. Lưu vào DB
    try:
        db.tests.insert_one(new_test)
        new_test.pop('_id', None)
        return jsonify(new_test), 201
    except Exception as e:
        return jsonify({"success": False, "message": f"Lỗi server: {e}"}), 500

# ==================================================
# ✅ DÁN HÀM MỚI NÀY VÀO (Khoảng dòng 628)
# ==================================================
@app.route("/api/tests/preview-auto", methods=["POST"])
def preview_auto_test():
    """
    API mới: Chỉ xem trước đề tự động, tính điểm, và trả về, KHÔNG LƯU.
    """
    data = request.get_json() or {}
    
    # 1. Lấy cấu hình
    subject = data.get("subject", "")
    level = data.get("level", "")
    dist = data.get("dist", {"easy": 0, "medium": 0, "hard": 0})
    
    num_easy = int(dist.get("easy", 0))
    num_medium = int(dist.get("medium", 0))
    num_hard = int(dist.get("hard", 0))
    total_questions_needed = num_easy + num_medium + num_hard
    
    if total_questions_needed == 0:
        return jsonify({"success": False, "message": "Vui lòng chọn ít nhất 1 câu hỏi"}), 400

    query = {}
    if subject: query["subject"] = subject
    if level: query["level"] = level

    # 2. Lấy câu hỏi ngẫu nhiên (dùng $sample)
    def pick(diff, count):
        if count == 0: return []
        q = {**query, "difficulty": diff}
        pipeline = [
            {"$match": q},
            {"$sample": {"size": count}}
            # Lấy đầy đủ nội dung để xem trước
        ]
        return list(db.questions.aggregate(pipeline))

    easy_questions = pick("easy", num_easy)
    medium_questions = pick("medium", num_medium)
    hard_questions = pick("hard", num_hard)
    
    all_questions = easy_questions + medium_questions + hard_questions
    
    # Lấy ID (ưu tiên 'id', fallback về str(_id))
    all_question_ids = [q.get('id') or str(q.get('_id')) for q in all_questions]
    
    if not all_question_ids:
         return jsonify({"success": False, "message": "Không tìm thấy câu hỏi nào phù hợp"}), 404

    # 3. ✅ GỌI HÀM TÍNH ĐIỂM
    points_map = calculate_question_points(all_question_ids, db)

    # 4. Gán điểm vào các câu hỏi
    for q in all_questions:
        q_id = q.get('id') or str(q.get('_id'))
        q["points"] = points_map.get(q_id, 0)
        q["_id"] = str(q.get("_id")) # Đảm bảo _id là string

    # 5. Trả về danh sách câu hỏi đã được gán điểm
    return jsonify(all_questions), 200


# ==================================================
# ✅ THAY THẾ HÀM CẬP NHẬT ĐỀ THI (Dòng 629)
# ==================================================
@app.route("/tests/<test_id>", methods=["PUT"])
@app.route("/api/tests/<test_id>", methods=["PUT"])
def update_test(test_id):
    
    # === LOGIC MỚI BẮT ĐẦU ===
    # Kiểm tra xem testId này đã có trong collection 'assignments' chưa
    if db.assignments.find_one({"testId": test_id}):
        return jsonify({"success": False, "message": "Đề thi đã được giao, không sửa được đề."}), 403 # 403 Forbidden
    # === LOGIC MỚI KẾT THÚC ===

    data = request.get_json() or {}
    
    # 1. Lấy dữ liệu mới từ JS
    name = data.get("name")
    time = data.get("time")
    level = data.get("level")
    subject = data.get("subject")
    
    # JS (hàm getEditedTestQuestions) gửi: [{"_id": "oid_str", ...}, ...]
    questions_from_js = data.get("questions", [])
    
    # Lấy _id string từ payload
    question_oids_from_fe = [q.get('_id') for q in questions_from_js if q.get('_id')]

    if not subject:
        return jsonify({"success": False, "message": "Vui lòng chọn Môn học"}), 400
    if not question_oids_from_fe:
        return jsonify({"success": False, "message": "Vui lòng chọn ít nhất 1 câu hỏi"}), 400

    # ✅ SỬA LỖI: CHUYỂN ĐỔI _id SANG id (UUID) (Giống hệt create_test)
    object_ids = []
    for oid_str in question_oids_from_fe:
        try:
            object_ids.append(ObjectId(oid_str))
        except Exception:
            pass 

    questions_from_db = list(db.questions.find(
        {"_id": {"$in": object_ids}},
        {"id": 1, "_id": 1}
    ))
    id_map = {str(q.get("_id")): q.get("id") for q in questions_from_db if q.get("id")}
    
    question_uuids_to_save = []
    for oid_str in question_oids_from_fe:
        uuid = id_map.get(oid_str)
        if uuid:
            question_uuids_to_save.append(uuid)
        else:
            question_uuids_to_save.append(oid_str) 
            
    # 2. GỌI LẠI HÀM TÍNH ĐIỂM
    points_map = calculate_question_points(question_uuids_to_save, db)

    # 3. Định dạng lại mảng câu hỏi
    formatted_questions = []
    mc_count, essay_count = calculate_question_counts(question_uuids_to_save, db)

    for q_id in question_uuids_to_save:
        points = points_map.get(q_id, 0)
        formatted_questions.append({
            "id": q_id,         # ✅ ĐÃ LƯU BẰNG id (UUID)
            "points": points
        })
            
    # 4. Tạo đối tượng $set
    update_data = {
        "name": name,
        "time": time,
        "level": level,
        "subject": subject,
        "questions": formatted_questions, # ✅ Danh sách MỚI với điểm MỚI
        "mcCount": mc_count,
        "essayCount": essay_count,
        "count": len(question_uuids_to_save) # ✅ Sửa: Dùng biến đã qua xử lý
    }

    # 5. Cập nhật vào DB
    try:
        result = db.tests.update_one(
            {"id": test_id},
            {"$set": update_data}
        )
        
        if result.matched_count == 0:
            return jsonify({"success": False, "message": "Không tìm thấy bài thi để cập nhật"}), 404
            
        updated_test = db.tests.find_one({"id": test_id})
        updated_test.pop('_id', None)
        
        return jsonify(updated_test), 200

    except Exception as e:
        return jsonify({"success": False, "message": f"Lỗi server: {e}"}), 500

# ... (Hàm /tests/<test_id> (DELETE) giữ nguyên) ...
@app.route("/tests/<test_id>", methods=["DELETE"])
@app.route("/api/tests/<test_id>", methods=["DELETE"])
def delete_test(test_id):
    
    # === LOGIC MỚI BẮT ĐẦU ===
    # Kiểm tra xem testId này đã có trong collection 'assignments' chưa
    if db.assignments.find_one({"testId": test_id}):
        return jsonify({"success": False, "message": "Đề thi đã được giao, không thể xóa."}), 403 # 403 Forbidden
    # === LOGIC MỚI KẾT THÚC ===

    try:
        result = db.tests.delete_one({"id": test_id})
        if result.deleted_count == 0:
            return jsonify({"message": "Bài kiểm tra không tồn tại."}), 404
        return jsonify({"message": "Đã xóa đề thi thành công!"}), 200
    except Exception as e:
        print("Error deleting test:", e)
        return jsonify({"message": "Không thể xóa đề thi.", "error": str(e)}), 500

# ... (Các hàm /assigns (GET), /assigns (POST), /assign-multiple, /debug/tests, /assigns/bulk, /tests/<id>/assignments, /assignments/bulk-delete, /assignments (GET) giữ nguyên) ...
@app.route("/assigns", methods=["GET"])
@app.route("/api/assigns", methods=["GET"])
def list_assigns():
    try:
        studentId = request.args.get("studentId")
        match_stage = {"studentId": studentId} if studentId else {}
        pipeline = [
            {"$match": match_stage},
            {"$lookup": {"from": "tests", "localField": "testId", "foreignField": "id", "as": "testInfo"}},
            {"$unwind": {"path": "$testInfo", "preserveNullAndEmptyArrays": True}},
            {"$lookup": {"from": "results", "localField": "id", "foreignField": "assignmentId", "as": "resultInfo"}},
            {"$unwind": {"path": "$resultInfo", "preserveNullAndEmptyArrays": True}},
            {"$project": {
                "_id": 0, "id": 1, "testId": 1, "studentId": 1, "deadline": 1, "status": 1,
                "assignedAt": {"$ifNull": ["$assignedAt", "$timeAssigned"]},
                "submittedAt": "$resultInfo.submittedAt",
                "gradingStatus": "$resultInfo.gradingStatus",
                "totalScore": {"$ifNull": ["$resultInfo.totalScore", None]},
                "mcScore": {"$ifNull": ["$resultInfo.mcScore", None]},
                "essayScore": {"$ifNull": ["$resultInfo.essayScore", None]},
                "testName": "$testInfo.name", "subject": "$testInfo.subject", "time": "$testInfo.time",
                "mcCount": "$testInfo.mcCount", "essayCount": "$testInfo.essayCount",
            }}
        ]
        docs = list(db.assignments.aggregate(pipeline))
        for a in docs:
            if a.get("submittedAt"): a["status"] = "submitted"
            if a.get("totalScore") is not None: a["totalScore"] = round(a["totalScore"], 2)
            if a.get("mcScore") is not None: a["mcScore"] = round(a["mcScore"], 2)
            if a.get("essayScore") is not None: a["essayScore"] = round(a["essayScore"], 2)
        return jsonify(docs)
    except Exception as e:
        print("list_assigns error:", e)
        return jsonify([]), 500

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
        "timeAssigned": data.get("timeAssigned") or now_vn_iso()
    }
    db.assignments.insert_one(newa)
    to_return = newa.copy(); to_return.pop("_id", None)
    return jsonify(to_return), 201

@app.route("/api/assign-test", methods=["POST"])
def alias_assign_test():
    return create_assign()

@app.route("/api/assign-multiple", methods=["POST"])
def assign_multiple():
    data = request.get_json() or {}
    test_id = data.get("testId")
    students = data.get("students", [])
    if not test_id or not students:
        return jsonify({"success": False, "message": "Thiếu testId hoặc danh sách học sinh"}), 400
    created = []
    for sid in students:
        newa = {
            "id": str(uuid4()), "testId": test_id, "studentId": sid,
            "deadline": data.get("deadline"), "status": "assigned",
            "timeAssigned": now_vn_iso()
        }
        db.assignments.insert_one(newa)
        newa.pop("_id", None)
        created.append(newa)
    return jsonify({"success": True, "count": len(created), "assigns": created}), 201

@app.route("/debug/tests", methods=["GET"])
def debug_list_tests():
    docs = list(db.tests.find({}, {"_id": 0, "id": 1, "name": 1}))
    return jsonify(docs)

@app.route("/api/assigns/bulk", methods=["POST"])
def bulk_assign_tests():
    try:
        data = request.get_json() or {}
        test_ids = data.get("testIds", [])      
        student_ids = data.get("studentIds", [])
        teacher_id = data.get("teacherId")
        deadline_iso = data.get("deadline") 
        if not isinstance(test_ids, list) or not isinstance(student_ids, list) or not teacher_id:
            return jsonify({"message": "Dữ liệu đầu vào thiếu hoặc không đúng định dạng (testIds, studentIds, teacherId).", "count": 0}), 400
        if not test_ids or not student_ids:
            return jsonify({"message": "Vui lòng chọn ít nhất một đề thi và một học sinh.", "count": 0}), 400
        
        students_cursor = db.users.find({"id": {"$in": student_ids}}, {"id": 1, "fullName": 1, "className": 1})
        student_map = {s['id']: s for s in students_cursor}
        valid_student_ids = list(student_map.keys())
        if not valid_student_ids:
            return jsonify({"message": "Không tìm thấy học sinh hợp lệ nào từ danh sách đã chọn.", "count": 0}), 200

        test_docs_cursor = db.tests.find({"id": {"$in": test_ids}}, {"_id": 0, "id": 1, "name": 1, "subject": 1})
        test_map = {t['id']: t for t in test_docs_cursor}
        assignments_to_insert = []
        
        for t_id in test_ids:
            test_info = test_map.get(t_id)
            if not test_info: continue
            for stu_id in valid_student_ids:
                student = student_map.get(stu_id); 
                if not student: continue 
                existing_assignment = db.assignments.find_one({"testId": t_id, "studentId": stu_id})
                if existing_assignment:
                    update_set = {"teacherId": teacher_id, "deadline": deadline_iso}
                    if "assignedAt" not in existing_assignment and "createdAt" not in existing_assignment:
                         update_set["assignedAt"] = now_vn_iso()
                    db.assignments.update_one({"id": existing_assignment["id"]}, {"$set": update_set})
                else:
                    new_assign = {
                        "id": str(uuid4()), "testId": t_id,
                        "testName": test_info.get("name"), "studentId": stu_id,
                        "studentName": student.get("fullName"), "className": student.get("className"), 
                        "teacherId": teacher_id, "deadline": deadline_iso,
                        "status": "pending", "assignedAt": now_vn_iso(),
                    }
                    assignments_to_insert.append(new_assign)
        
        if assignments_to_insert:
            db.assignments.insert_many(assignments_to_insert)
        db.tests.update_many({"id": {"$in": test_ids}}, {"$set": {"assignmentStatus": "assigned"}})
        total_processed_count = len(test_ids) * len(valid_student_ids) 
        
        return jsonify({
            "success": True, "count": len(test_ids),
            "totalAssignmentsProcessed": total_processed_count,
            "message": f"Đã giao thành công {len(test_ids)} đề thi cho {len(valid_student_ids)} học sinh."
        }), 201
    except Exception as e:
        print(f"Lỗi khi thực hiện bulk_assign_tests: {e}")
        return jsonify({"message": "Lỗi máy chủ khi giao/cập nhật đề.", "count": 0}), 500

@app.route("/api/tests/<test_id>/assignments", methods=["GET"])
def get_test_assignments(test_id):
    try:
        assignments = list(db.assignments.find({"testId": test_id}, {"_id": 0}))
        student_ids = [a.get("studentId") for a in assignments if a.get("studentId")]
        students_cursor = db.users.find(
            {"id": {"$in": student_ids}}, 
            {"_id": 0, "id": 1, "fullName": 1, "className": 1, "role": 1}
        )
        student_map = {s["id"]: s for s in students_cursor}
        results = []
        for a in assignments:
            student_info = student_map.get(a.get("studentId"), {
                "fullName": "Không rõ", "className": "N/A", "role": "student"
            })
            a['studentName'] = student_info.get('fullName', 'Không rõ') 
            a['studentClass'] = student_info.get('className', 'N/A')
            a['studentRole'] = student_info.get('role', 'student')
            results.append(a)
        return jsonify(results), 200
    except Exception as e:
        print(f"Lỗi khi lấy assignment cho test {test_id}: {e}")
        return jsonify({"message": "Lỗi máy chủ."}), 500

@app.route("/api/assignments/bulk-delete", methods=["POST"])
def bulk_delete_assignments():
    data = request.get_json() or {}
    assignment_ids = data.get("assignmentIds", [])
    if not assignment_ids:
        return jsonify({"message": "Thiếu danh sách assignmentIds", "deletedCount": 0}), 400
    try:
        result = db.assignments.delete_many({"id": {"$in": assignment_ids}})
        return jsonify({"message": f"Đã xóa {result.deleted_count} assignments.", "deletedCount": result.deleted_count}), 200
    except Exception as e:
        print(f"Lỗi khi xóa hàng loạt assignments: {e}")
        return jsonify({"message": "Lỗi máy chủ khi xóa hàng loạt assignment.", "deletedCount": 0}), 500

@app.route("/api/assignments", methods=["GET"])
def get_assignments_for_student():
    student_id = request.args.get("studentId")
    if not student_id:
        return jsonify({"success": False, "message": "Missing studentId parameter"}), 400
    assignments = list(db.assignments.find({"studentId": student_id}, {"_id": 0})) 
    if not assignments:
        return jsonify({"success": True, "assignments": []})
    test_ids = [a["testId"] for a in assignments if a.get("testId")]
    tests = db.tests.find({"id": {"$in": test_ids}}, 
                           {"_id": 0, "id": 1, "name": 1, "subject": 1, "time": 1, "mcCount": 1, "essayCount": 1})
    tests_map = {t["id"]: t for t in tests}
    result_list = []
    for a in assignments:
        test_info = tests_map.get(a["testId"], {})
        assigned_date = a.get("assignedAt") or a.get("createdAt") 
        result_list.append({
            "assignmentId": a.get("id"),
            "testId": a["testId"],
            "testName": test_info.get("name", a.get("testName", "N/A")),
            "subject": test_info.get("subject", "N/A"),
            "time": test_info.get("time"),
            "mcCount": test_info.get("mcCount", 0),
            "essayCount": test_info.get("essayCount", 0),
            "deadline": a.get("deadline"),
            "assignedAt": assigned_date,
            "status": a.get("status", "pending"),
        })
    return jsonify({"success": True, "assignments": result_list})

# ==================================================
# ✅ THAY THẾ HÀM NỘP BÀI (Dòng 777)
# ==================================================
@app.route("/results", methods=["POST"])
@app.route("/api/results", methods=["POST"])
def create_result():
    try:
        data = request.get_json() or {}
        student_id = data.get("studentId")
        assignment_id = data.get("assignmentId")
        test_id = data.get("testId")
        student_answers = data.get("studentAnswers", [])

        if not student_id or not assignment_id or not test_id:
            return jsonify({"message": "Thiếu ID (studentId, assignmentId, testId)"}), 400

        # 1. Lấy thông tin Test
        test_doc = db.tests.find_one({"id": test_id})
        if not test_doc:
            return jsonify({"message": "Không tìm thấy đề thi"}), 404

        test_questions = test_doc.get("questions", []) or []
        
        # 2. ✅ TẠO MAP ĐIỂM SỐ (Lấy từ test_doc)
        # test_doc["questions"] là: [{'id': 'q1', 'points': 1.5}, ...]
        points_map = {q.get('id'): q.get('points', 1) for q in test_questions}
        question_ids_in_test = list(points_map.keys())

        # 3. Lấy đáp án đúng và type (vẫn phải lấy từ db.questions)
        # ✅ SỬA LOGIC TRUY VẤN (Sử dụng 'id' và '_id' của file gốc)
        object_ids = []
        uuid_strings = []
        for qid_str in question_ids_in_test:
            try:
                object_ids.append(ObjectId(qid_str))
            except Exception:
                uuid_strings.append(qid_str)
        or_clauses = []
        if object_ids: or_clauses.append({"_id": {"$in": object_ids}})
        if uuid_strings: or_clauses.append({"id": {"$in": uuid_strings}})
        
        correct_questions = []
        if or_clauses:
             correct_questions = list(db.questions.find({"$or": or_clauses}))

        correct_answer_map = {}
        type_map = {}
        has_essay = False
        
        for q in correct_questions:
            # Dùng cả 'id' và str(_id) làm key
            q_id_uuid = q.get("id")
            q_id_obj_str = str(q.get("_id"))
            q_type = q.get("type", "mc")

            correct_ans_text = None
            if q_type == "mc":
                correct_ans_text = next((opt.get("text") for opt in q.get("options", []) if opt.get("correct")), None)
            elif q_type == "essay":
                correct_ans_text = q.get("answer") # Gợi ý
                has_essay = True

            if q_id_uuid:
                type_map[q_id_uuid] = q_type
                correct_answer_map[q_id_uuid] = correct_ans_text
            if q_id_obj_str:
                type_map[q_id_obj_str] = q_type
                correct_answer_map[q_id_obj_str] = correct_ans_text


        # 4. Tạo map câu trả lời của học sinh (Từ hàm cũ của bạn)
        student_ans_map = {}
        for ans in student_answers:
            if not isinstance(ans, dict): continue
            qkey = ans.get("questionId") # FE gửi qIdForPayload (là 'id' hoặc str(_id))
            if qkey:
                student_ans_map[str(qkey)] = ans.get("answer")

        mc_score = 0.0
        detailed_results = []
        
        # ✅ SỬA LỖI BUG 3: KHỞI TẠO essay_count
        essay_count = 0 

        def norm_str(x):
            if x is None: return ""
            return str(x).strip().lower()

        # 5. LẶP VÀ TÍNH ĐIỂM
        for q_id in question_ids_in_test: # Lặp qua ID từ db.tests
            
            # ✅ SỬA LỖI BUG 4/5: Khớp ID
            # q_id là ID từ 'points_map' (có thể là UUID hoặc str(_id))
            q_type = type_map.get(q_id, "mc")
            max_points = float(points_map.get(q_id, 1))
            student_ans_value = student_ans_map.get(q_id, None)
            correct_ans_text = correct_answer_map.get(q_id)

            is_correct = None
            points_gained = 0.0

            if q_type == "mc":
                is_correct = (student_ans_value is not None) and \
                             (correct_ans_text is not None) and \
                             (norm_str(student_ans_value) == norm_str(correct_ans_text))
                
                if is_correct:
                    points_gained = max_points
                    mc_score += max_points

            elif q_type == "essay":
                essay_count += 1
                is_correct = None # Chờ chấm

            detailed_results.append({
                "questionId": q_id,
                "studentAnswer": student_ans_value,
                "correctAnswer": correct_ans_text,
                "maxPoints": max_points,
                "pointsGained": round(points_gained, 2),
                "isCorrect": is_correct,
                "type": q_type,
                "teacherScore": None,
                "teacherNote": ""
            })

        # 6. Xác định trạng thái chấm
        # ✅ SỬA LỖI BUG 3: Dùng 'has_essay' flag
        grading_status = "Đang Chấm" if has_essay else "Hoàn tất"
        result_id = str(uuid4())
        total_score = round(mc_score, 2)

        # 7. Lấy thông tin user
        user_info = db.users.find_one({"id": student_id}) or {}

        new_result = {
            "id": result_id,
            "studentId": student_id,
            "assignmentId": assignment_id,
            "testId": test_id,
            "studentName": user_info.get("fullName", user_info.get("user")),
            "className": user_info.get("className"),
            "testName": test_doc.get("name"),
            "studentAnswers": student_answers,
            "detailedResults": detailed_results,
            "gradingStatus": grading_status,
            "mcScore": round(mc_score, 2),
            "essayScore": 0.0,
            "totalScore": total_score,
            "submittedAt": now_vn_iso(),
            "gradedAt": None
        }
        
        # 8. Dùng replace_one (UPSERT)
        db.results.replace_one(
            {"studentId": student_id, "assignmentId": assignment_id},
            new_result,
            upsert=True
        )

        db.assignments.update_one(
            {"id": assignment_id},
            {"$set": {"status": "submitted", "submittedAt": new_result["submittedAt"], "resultId": result_id}}
        )
        
        new_result.pop("_id", None) # Xóa _id (ObjectId)
        return jsonify(new_result), 201

    except Exception as e:
        print("create_result error:", e)
        traceback.print_exc()
        return jsonify({"message": f"Server error: {str(e)}"}), 500
    
# ==================================================
# ✅ THAY THẾ HÀM CHẤM ĐIỂM (Dòng 924)
# ==================================================
@app.route("/api/results/<result_id>/grade", methods=["POST"])
def grade_result(result_id):
    """
    Giáo viên chấm điểm (Logic đã sửa theo yêu cầu của bạn):
    1. Nhận điểm tự luận (Essay) từ payload.
    2. Lấy điểm trắc nghiệm (MC) đã được chấm tự động (lúc nộp bài) từ 'db.results'.
    3. Lấy điểm tối đa (maxPoints) của câu tự luận từ 'db.tests' (đã tính theo 5 quy tắc).
    4. Khống chế điểm giáo viên chấm không vượt quá maxPoints.
    5. Tính tổng = (Điểm MC cũ) + (Điểm Essay mới).
    """
    try:
        data = request.get_json() or {}
        essays_payload = [e for e in data.get("essays", []) if isinstance(e, dict)] # Lấy payload của GV

        # === 1. Lấy bài làm (Result) ===
        result = db.results.find_one({"id": result_id})
        if not result:
            return jsonify({"error": "Không tìm thấy bài làm"}), 404

        current_regrade = result.get("regradeCount", 0)
        detailed_list = result.get("detailedResults", [])
        detailed_map = { str(d.get("questionId")): d for d in detailed_list if d.get("questionId") }

        # === 2. LẤY BÀI THI GỐC (ĐỂ LẤY ĐIỂM TỐI ĐA CỦA TỪNG CÂU) ===
        test_id = result.get("testId")
        test_doc = db.tests.find_one({"id": test_id})
        if not test_doc:
            return jsonify({"error": f"Không tìm thấy bài thi gốc (ID: {test_id})."}), 404
        
        # Tạo "Master Point Map" (Nguồn điểm chuẩn)
        points_map = {q.get('id') or str(q.get('_id')): q.get('points', 1) for q in test_doc.get('questions', [])}

        # === 3. LẤY ĐIỂM TRẮC NGHIỆM ĐÃ CHẤM TỰ ĐỘNG (FIXED) ===
        # Tin tưởng điểm MC đã được tính đúng lúc nộp bài (create_result)
        new_mc_score = result.get("mcScore", 0.0) 
        new_essay_score = 0.0
        
        # === 4. XỬ LÝ ĐIỂM TỰ LUẬN MỚI TỪ GIÁO VIÊN ===
        has_ungraded_essay = False # Flag để kiểm tra xem GV có bỏ sót câu nào không

        for q_id_str, det in detailed_map.items():
            
            # Chỉ xử lý câu Tự luận
            if det.get("type") == "essay":
                # Tìm xem GV có chấm câu này trong payload không
                essay_data = next((e for e in essays_payload if str(e.get("questionId")) == q_id_str), None)
                
                # Lấy điểm tối đa (max_points) của câu này từ đề thi gốc
                max_points = float(points_map.get(q_id_str, 1.0)) 
                
                if essay_data and essay_data.get("teacherScore") is not None:
                    # Giáo viên CÓ chấm câu này
                    ts_float = 0.0
                    try: 
                        ts_float = float(essay_data.get("teacherScore"))
                    except: 
                        ts_float = 0.0
                    
                    # ✅ LOGIC KHỐNG CHẾ ĐIỂM
                    if ts_float > max_points:
                        ts_float = max_points 
                    if ts_float < 0:
                        ts_float = 0.0
                        
                    det["teacherScore"] = ts_float
                    det["teacherNote"] = essay_data.get("teacherNote", "")
                    det["pointsGained"] = ts_float
                    det["isCorrect"] = ts_float > 0
                    
                    new_essay_score += ts_float # Cộng vào điểm tự luận tổng
                
                else:
                    # Giáo viên KHÔNG chấm câu này
                    if det.get("teacherScore") is None:
                        has_ungraded_essay = True
                    else:
                        # Giữ điểm đã chấm từ lần trước (nếu có)
                        new_essay_score += float(det.get("pointsGained", 0.0))

            # (Chúng ta không làm gì với câu 'mc')

        # === 5. Tính điểm tổng và xác định trạng thái ===
        new_total_score = new_mc_score + new_essay_score
        graded_at = now_vn_iso()
        
        if has_ungraded_essay:
             new_status = "Đang Chấm"
        elif current_regrade + 1 >= 2:
            new_status = "Hoàn tất"
        else:
            new_status = "Đã Chấm"

        # === 6. Cập nhật DB ===
        update_payload = {
            "detailedResults": list(detailed_map.values()),
            "totalScore": round(new_total_score, 2),
            "mcScore": round(new_mc_score, 2), # Điểm MC (Giữ nguyên)
            "essayScore": round(new_essay_score, 2), # Điểm Tự luận MỚI
            "gradingStatus": new_status,
            "gradedAt": graded_at,
        }

        db.results.update_one(
            {"id": result_id},
            {
                "$set": update_payload,
                "$inc": { "regradeCount": 1 }
            }
        )

        # === 7. Trả về ===
        return jsonify({
            "success": True,
            "message": f"{new_status}! Tổng điểm: {round(new_total_score,2):.2f}",
            "totalScore": round(new_total_score,2),
            "mcScore": round(new_mc_score, 2),
            "essayScore": round(new_essay_score, 2),
            "gradingStatus": new_status,
            "regradeCount": current_regrade + 1
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e), "message": "Internal Server Error"}), 500

# ... (Các hàm /results_summary, /results/<id> (GET), /assignment_stats, /results (GET) giữ nguyên) ...
@app.route("/api/results_summary", methods=["GET"])
def get_results_summary():
    pipeline = [
        {"$lookup": {
            "from": "users", "let": { "sid": "$studentId" }, 
            "pipeline": [
                { "$match": { "$expr": { "$or": [ { "$eq": [ "$id", "$$sid" ] }, { "$eq": [ { "$toString": "$_id" }, "$$sid" ] } ] }}},
                { "$project": { "fullName": 1, "className": 1, "_id": 0 } } 
            ], "as": "student_info"
        }},
        {"$unwind": {"path": "$student_info", "preserveNullAndEmptyArrays": True}},
        {"$lookup": {"from": "tests", "localField": "testId", "foreignField": "id", "as": "test_info"}},
        {"$unwind": {"path": "$test_info", "preserveNullAndEmptyArrays": True}},
        {"$project": {
            "_id": 0, "id": "$id", "studentId": "$studentId", "testId": "$testId",
            "totalScore": {"$ifNull": ["$totalScore", 0.0]},
            "mcScore": {"$ifNull": ["$mcScore", 0.0]},
            "essayScore": {"$ifNull": ["$essayScore", 0.0]},
            "gradingStatus": {"$ifNull": ["$gradingStatus", "Đang Chấm"]},
            "gradedAt": {"$ifNull": ["$gradedAt", None]}, 
            "submittedAt": "$submittedAt",
            "testName": {"$ifNull": ["$test_info.name", "Đã Xóa"]},
            "studentName": {"$ifNull": ["$student_info.fullName", "N/A"]},
            "className": {"$ifNull": ["$student_info.className", "N/A"]},
        }}
    ]
    docs = list(db.results.aggregate(pipeline))
    for doc in docs:
        doc.pop("detailedResults", None) 
        status_from_db = doc.get("gradingStatus")
        if status_from_db in ["Hoàn tất", "Tự động hoàn tất", "Đã Chấm Lại"]:
            doc["gradingStatus"] = "Hoàn tất"
        elif status_from_db == "Đã Chấm":
             doc["gradingStatus"] = "Đã Chấm" 
        else:
             doc["gradingStatus"] = "Đang Chấm"
        doc["totalScore"] = round(doc.get("totalScore", 0.0), 2)
        doc["mcScore"] = round(doc.get("mcScore", 0.0), 2)
        doc["essayScore"] = round(doc.get("essayScore", 0.0), 2)
    return jsonify(docs)

@app.route("/results/<result_id>", methods=["GET"])
@app.route("/api/results/<result_id>", methods=["GET"])
def get_result_detail(result_id):
    try:
        match_query = {"$or": [{"id": result_id}]}
        try:
            match_query["$or"].append({"_id": ObjectId(result_id)})
        except Exception:
            pass
        pipeline = [{"$match": match_query}]
        pipeline.append({"$lookup": {"from": "tests", "localField": "testId", "foreignField": "id", "as": "test_info"}})
        pipeline.append({"$unwind": {"path": "$test_info", "preserveNullAndEmptyArrays": True}})
        pipeline.append({
            "$lookup": {
                "from": "users", "let": { "sid": "$studentId" }, 
                "pipeline": [
                    { "$match": { "$expr": { "$or": [ { "$eq": [ "$id", "$$sid" ] }, { "$eq": [ { "$toString": "$_id" }, "$$sid" ] } ] }}},
                    { "$project": { "fullName": 1, "className": 1, "_id": 0 } }
                ], "as": "student_info"
            }
        })
        pipeline.append({"$unwind": {"path": "$student_info", "preserveNullAndEmptyArrays": True}})
        pipeline.append({
            "$project": {
                "_id": 0, "id": {"$ifNull": ["$id", {"$toString": "$_id"}]},
                "assignmentId": 1, "testId": 1, "studentId": 1, "submittedAt": 1, "gradedAt": 1,
                "gradingStatus": 1, "totalScore": 1, "mcScore": 1, "essayScore": 1,
                "teacherNote": 1, "regradeCount": 1, "studentAnswers": 1, "detailedResults": 1,
                "testName": {"$ifNull": ["$test_info.name", "Bài thi đã xóa"]},
                "subject": {"$ifNull": ["$test_info.subject", "khác"]}, 
                "studentName": {"$ifNull": ["$student_info.fullName", "N/A"]},
                "className": {"$ifNull": ["$student_info.className", "N/A"]}
            }
        })
        results = list(db.results.aggregate(pipeline))
        if not results:
            return jsonify({"message": "Result not found"}), 404
        return jsonify(results[0])
    except Exception as e:
        print(f"Lỗi khi lấy chi tiết result {result_id}: {e}")
        return jsonify({"message": f"Server error: {e}"}), 500

@app.route("/api/assignment_stats", methods=["GET"])
def get_assignment_stats():
    try:
        total_tests_created = db.tests.count_documents({})
        total_assignments = db.assignments.count_documents({})
        unique_students_assigned_list = db.assignments.distinct("studentId")
        unique_students_assigned = len(unique_students_assigned_list)
        total_results_submitted = db.results.count_documents({})
        student_roles = ["student", "monitor", "vice_monitor", "team_leader"]
        total_students_with_roles = db.users.count_documents({"role": {"$in": student_roles}})
        
        return jsonify({
            "totalTestsCreated": total_tests_created,
            "totalAssignments": total_assignments,
            "uniqueStudentsAssigned": unique_students_assigned,
            "totalResultsSubmitted": total_results_submitted,
            "totalStudents": total_students_with_roles
        })
    except Exception as e:
        print(f"Lỗi khi lấy thống kê assignments: {e}")
        return jsonify({
             "totalTestsCreated": 0, "totalAssignments": 0, "uniqueStudentsAssigned": 0, "totalResultsSubmitted": 0, "totalStudents": 0, "error": str(e)
        }), 500

@app.route("/api/results", methods=["GET"])
def get_results_for_student():
    student_id = request.args.get("studentId")
    if not student_id:
        return jsonify({"message": "Missing studentId parameter"}), 400
    try:
        pipeline = [
            {"$match": {"studentId": student_id}},
            {"$lookup": {"from": "tests", "localField": "testId", "foreignField": "id", "as": "test_info"}},
            {"$unwind": {"path": "$test_info", "preserveNullAndEmptyArrays": True}},
            {"$project": {
                "_id": 0, "id": {"$ifNull": ["$id", {"$toString": "$_id"}]}, 
                "assignmentId": 1, "testId": 1,
                "testName": {"$ifNull": ["$test_info.name", "Bài thi đã xóa"]},
                "subject": {"$ifNull": ["$test_info.subject", "khác"]}, 
                "submittedAt": 1, "gradedAt": 1, "gradingStatus": 1,
                "totalScore": 1, "mcScore": 1, "essayScore": 1,
                "studentAnswers": 1, "detailedResults": 1 
            }}
        ]
        results = list(db.results.aggregate(pipeline))
        return jsonify(results)
    except Exception as e:
        print(f"Lỗi khi lấy results cho student {student_id}: {e}")
        return jsonify([]), 500

# Serve frontend files (unchanged)
@app.route("/", methods=["GET"])
def index():
    try:
        return send_from_directory(".", "index.html") # ✅ Sửa: Luôn trỏ đến index.html
    except Exception:
        return jsonify({"message": "Index not found"}), 404

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
