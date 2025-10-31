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

# Load .env in local; Render provides env vars automatically
load_dotenv()

app = Flask(__name__)
# Allow all origins so frontend on any domain can call this API
CORS(app, resources={r"/*": {"origins": "*"}})

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

    # Tạo query $or
    or_clauses = []
    if object_ids:
        or_clauses.append({"_id": {"$in": object_ids}})
    if uuid_strings:
        or_clauses.append({"id": {"$in": uuid_strings}})

    # Chỉ truy vấn trường 'type'
    question_types = list(db.questions.find(
        {"$or": or_clauses} if or_clauses else {},
        {"type": 1}
    ))

    mc_count = 0
    essay_count = 0

    for q in question_types:
        q_type = q.get("type", "").lower()
        if q_type == "mc":
            mc_count += 1
        elif q_type == "essay":
            essay_count += 1
        # Nếu không có type: cố gắng xác định dựa trên options (như hàm get_test)
        elif not q_type:
             if q.get("options") and len(q.get("options")) > 0:
                mc_count += 1
             else:
                essay_count += 1

    return mc_count, essay_count

# ==================================================
# ✅ HÀM TÍNH ĐIỂM HELPER MỚI (DÁN VÀO DÒNG 58)
# ==================================================
def calculate_question_points(question_ids, db):
    """
    Nhận vào một danh sách ID câu hỏi (string UUIDs hoặc ObjectIds)
    Trả về một map: { "question_id": points }
    Áp dụng 5 quy tắc tính điểm của bạn, tổng là 10.
    """
    if not question_ids:
        return {}

    # 1. Lấy tất cả câu hỏi từ DB (Sử dụng logic truy vấn phức tạp của bạn)
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
        
    # Chỉ lấy các trường cần thiết (id, _id, difficulty)
    questions = list(db.questions.find(
        {"$or": or_clauses},
        {"id": 1, "_id": 1, "difficulty": 1}
    ))
    
    if not questions:
        return {}

    # 2. Đếm số lượng E, M, H
    counts = {'easy': 0, 'medium': 0, 'hard': 0}
    question_map = {} # Map {id: question_object}
    
    for q in questions:
        # Ưu tiên dùng 'id' (UUID) làm key, fallback về str(_id)
        q_key = q.get('id') or str(q.get('_id'))
        difficulty = q.get('difficulty', 'medium')
        
        if difficulty not in counts:
            difficulty = 'medium'
            
        counts[difficulty] += 1
        # Lưu lại difficulty vào map
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
        
        # Trường hợp không xác định (ví dụ: 0 câu hỏi)
        else:
            print("Cảnh báo: Không có câu hỏi nào được tìm thấy để tính điểm.")

    except ZeroDivisionError:
        print(f"Lỗi chia cho 0 khi tính điểm (E={num_easy}, M={num_medium}, H={num_hard}). Trả về điểm mặc định.")
        default_points = 10.0 / total_questions
        return {q_id: default_points for q_id in question_map.keys()}

    # 4. Tạo map {id: points} cuối cùng
    result_map = {}
    for q_id, difficulty in question_map.items():
        # Làm tròn 2 chữ số thập phân
        result_map[q_id] = round(points_per_difficulty[difficulty], 2)

    return result_map


@app.route("/api/test-deploy", methods=["GET"])
def test_deploy():
    return jsonify({"status": "SUCCESS", "version": "v1.1-MC_ESSAY_FIX"})

# THÊM DÒNG NÀY: Tăng giới hạn dữ liệu request lên 25MB (25 * 1024 * 1024 bytes)
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
    print("❌ Cannot connect to MongoDB. Check MONGODB_URI. Error:", e)
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

# ------------------ GENERIC ERROR HANDLER ------------------
@app.errorhandler(Exception)
def handle_exception(e):
    """
    Xử lý lỗi toàn cục — luôn trả về JSON thay vì HTML
    """
    if isinstance(e, HTTPException):
        return jsonify({
            "success": False,
            "message": e.description
        }), e.code

    return jsonify({
        "success": False,
        "message": "Internal server error",
        "error": str(e)
    }), 500


# ------------------ HEALTH CHECK ------------------
@app.route("/healthz", methods=["GET"])
def health():
    """
    Kiểm tra tình trạng server và kết nối MongoDB.
    """
    try:
        db_stats = db.command("ping")
        db_status = "connected" if db_stats.get("ok") == 1.0 else "error"
    except Exception as e:
        db_status = f"error: {str(e)}"

    return jsonify({
        "status": "ok",
        "timezone": "UTC+7",
        "db": DB_NAME,
        "db_status": db_status
    })


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
        user_id_to_return = found.get("id") or str(found.get("_id"))
        return jsonify({"success": True, "user": {
            "id": found.get("id"), 
            "user": found.get("user"), 
            "role": found.get("role"),
            "fullName": found.get("fullName"),    # <-- BỔ SUNG DÒNG NÀY
            "className": found.get("className") # <-- BỔ SUNG DÒNG NÀY
        }})
    return jsonify({"success": False, "message": "Tên đăng nhập hoặc mật khẩu không đúng."}), 401

@app.route("/register", methods=["POST"])
@app.route("/api/register", methods=["POST"])
@app.route("/api/users", methods=["POST"]) # ✅ Bổ sung POST /api/users
def register():
    data = request.get_json() or {}
    user = data.get("user"); passwd = data.get("pass")
    level = data.get("level",0)
    
    # ✅ THÊM CÁC TRƯỜNG MỚI
    fullName = data.get("fullName"); className = data.get("className")
    gender = data.get("gender") # đã có từ trước
    # ✅ LOGIC ĐỒNG BỘ: Chuyển vai trò 'admin' thành 'teacher' khi lưu
    role_to_save = data.get("role", "student")
    if role_to_save == "admin":
        role_to_save = "teacher" # Buộc lưu là 'teacher' để đồng bộ với Frontend
    if not user or passwd is None:
        return jsonify({"success": False, "message": "Missing user or pass"}), 400
    if db.users.find_one({"user": user}):
        return jsonify({"success": False, "message": "Tên tài khoản đã tồn tại."}), 409
    
    new_user = {
        "id": str(uuid4()), 
        "user": user, 
        "pass": passwd, 
        "fullName": fullName,  # ✅ LƯU HỌ TÊN
        "className": className, # ✅ LƯU LỚP
        "gender": gender, 
        "level": level,
        "role": data.get("role", "student") # Lấy role từ payload, mặc định là student
    }
    db.users.insert_one(new_user)
    to_return = new_user.copy()
    to_return.pop("_id", None)
    return jsonify({"success": True, "user": to_return}), 201

# --------------------- USERS ---------------------
@app.route("/users", methods=["GET"])
@app.route("/api/users", methods=["GET"])
def get_users():
    query = {}
    
    # 1. Lọc theo Vai trò
    role = request.args.get("role")
    if role:
        query["role"] = role
        
    # 2. Lọc theo Lớp
    className = request.args.get("class")
    if className:
        # Tìm kiếm chính xác tên lớp (nếu muốn tìm kiếm tương đối thì dùng $regex)
        query["className"] = className 
        
    # 3. Tìm kiếm theo Tên (Tìm kiếm tương đối)
    nameSearch = request.args.get("name")
    if nameSearch:
        # Tìm kiếm không phân biệt chữ hoa/thường trong trường 'fullName'
        query["fullName"] = {"$regex": nameSearch, "$options": "i"} 
        
    # Lọc theo Giới tính ✅ BỔ SUNG LỌC GIỚI TÍNH
    gender = request.args.get("gender")
    if gender:
        query["gender"] = gender 
    
    # Thực hiện truy vấn và loại trừ _id
    docs = list(db.users.find(query, {"_id": 0}))
    
    # Nếu bạn dùng phân trang, logic sẽ phức tạp hơn:
    # total_users = db.users.count_documents(query)
    # limit = int(request.args.get("limit", 10))
    # offset = int(request.args.get("page", 1) - 1) * limit
    # docs = list(db.users.find(query, {"_id": 0}).skip(offset).limit(limit))
    # return jsonify({"total": total_users, "users": docs})

    return jsonify(docs)

@app.route("/users/<user_id>", methods=["GET"])
@app.route("/api/users/<user_id>", methods=["GET"])
def get_user(user_id):
    """Bổ sung: Lấy thông tin người dùng theo ID để hỗ trợ Sửa (Edit)"""
    doc = db.users.find_one({"id": user_id}, {"_id": 0})
    if not doc:
        return jsonify({"message": "Người dùng không tìm thấy."}), 404
    return jsonify(doc)


@app.route("/users/<user_id>", methods=["PUT", "PATCH"])
@app.route("/api/users/<user_id>", methods=["PUT", "PATCH"])
def update_user(user_id):
    """Bổ sung: Xử lý yêu cầu Sửa/Cập nhật (PUT) thông tin người dùng."""
    data = request.get_json() or {}
    update_fields = {}
    
    # Sử dụng các trường 'user' và 'pass' nhất quán với route /login và /register
    if "user" in data:
        update_fields["user"] = data["user"]
    if "pass" in data:
        update_fields["pass"] = data["pass"]
    if "role" in data:
        role_to_update = data["role"]
        if role_to_update == "admin":
            role_to_update = "teacher" # Buộc lưu là 'teacher' để đồng bộ với Frontend
        update_fields["role"] = role_to_update
    if "fullName" in data: 
        update_fields["fullName"] = data["fullName"] # ✅ TRƯỜNG MỚI
    if "className" in data: 
        update_fields["className"] = data["className"] # ✅ TRƯỜNG MỚI    
    if "dob" in data:
        update_fields["dob"] = data["dob"]
    if "gender" in data:
        update_fields["gender"] = data["gender"]
    if "level" in data:
        update_fields["level"] = data["level"] # <<< CẬP NHẬT LEVEL
        
    if not update_fields:
        return jsonify({"message": "Không có trường nào được cung cấp để cập nhật."}), 400

    # Cập nhật trong MongoDB dựa trên trường 'id'
    res = db.users.update_one({"id": user_id}, {"$set": update_fields})

    if res.matched_count == 0:
        return jsonify({"message": "Người dùng không tìm thấy."}), 404
    
    updated_user = db.users.find_one({"id": user_id}, {"_id": 0})
    return jsonify(updated_user), 200 # Trả về 200 OK với dữ liệu cập nhật

@app.route("/users/<user_id>", methods=["DELETE"])
@app.route("/api/users/<user_id>", methods=["DELETE"])
def delete_user(user_id):
    res = db.users.delete_one({"id": user_id})
    if res.deleted_count > 0:
        return "", 204
    return jsonify({"message": "Người dùng không tìm thấy."}), 404

# --------------------- QUESTIONS ---------------------
@app.route("/questions/image/<file_id>", methods=["GET"])
def get_question_image(file_id):
    """Trả ảnh từ GridFS"""
    try:
        file_obj = fs.get(ObjectId(file_id))
        return send_file(file_obj, mimetype=file_obj.content_type, as_attachment=False, download_name=file_obj.filename)
    except Exception as e:
        return jsonify({"message": f"File not found: {str(e)}"}), 404

@app.route("/questions", methods=["GET"])
@app.route("/api/questions", methods=["GET"])
def list_questions():
    query = {}
    subject = request.args.get("subject")
    level = request.args.get("level")
    # THÊM BỘ LỌC LOẠI CÂU HỎI
    q_type = request.args.get("type") 
    difficulty = request.args.get("difficulty")
    # DÒNG MỚI: LẤY THAM SỐ TÌM KIẾM
    search_keyword = request.args.get("search") 
    if subject: query["subject"] = subject
    if level: query["level"] = level
    # DÒNG QUAN TRỌNG: THÊM BỘ LỌC VÀO TRUY VẤN
    if q_type: query["type"] = q_type
    if difficulty: query["difficulty"] = difficulty
     # THÊM LOGIC TÌM KIẾM BẰNG $regex
    if search_keyword:
        # Tìm kiếm không phân biệt chữ hoa/thường ('i') trong trường 'q'
        query["q"] = {"$regex": search_keyword, "$options": "i"} 
    
    # docs = list(db.questions.find(query, {"_id": 0}))
    docs = list(db.questions.find(query))
    for doc in docs:
        doc['_id'] = str(doc['_id'])
    return jsonify(docs)

@app.route("/questions", methods=["POST"])
@app.route("/api/questions", methods=["POST"])
def create_question():
    data = request.form
    image_file = request.files.get("image")

    image_id = None

    # 1. Upload ảnh lên GridFS nếu có
    if image_file:
        filename = secure_filename(image_file.filename)
        content_type = image_file.mimetype
        try:
            image_id = fs.put(image_file, filename=filename, content_type=content_type)
        except Exception as e:
            return jsonify({"message": f"Lỗi lưu file: {str(e)}"}), 500

    # 2. Parse options/answer
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
    data = request.form
    image_file = request.files.get("image")
    remove_old = data.get("removeOldImage", "false") == "true"

    # 1. Lấy câu hỏi hiện tại
    question = db.questions.find_one({"id": q_id})
    if not question:
        return jsonify({"message": "Không tìm thấy câu hỏi"}), 404

    image_id = question.get("imageId")

    # 2. Xóa ảnh cũ nếu user bấm remove
    if remove_old and image_id:
        try:
            fs.delete(ObjectId(image_id))
        except Exception:
            pass
        image_id = None

    # 3. Upload ảnh mới nếu có
    if image_file:
        try:
            filename = secure_filename(image_file.filename)
            content_type = image_file.mimetype
            new_image_id = fs.put(image_file, filename=filename, content_type=content_type)
            image_id = str(new_image_id)
        except Exception as e:
            return jsonify({"message": f"Lỗi upload ảnh mới: {str(e)}"}), 500

    # 4. Parse options/answer
    try:
        options = json.loads(data.get("options", "[]"))
        answer = data.get("answer", "")
    except json.JSONDecodeError:
        return jsonify({"message": "Lỗi định dạng dữ liệu Options hoặc Answer."}), 400

    # 5. Chuẩn bị dữ liệu update
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

    # 6. Cập nhật MongoDB
    res = db.questions.update_one({"id": q_id}, {"$set": update_fields})
    if res.matched_count == 0:
        return jsonify({"message": "Câu hỏi không tồn tại."}), 404

    updated = db.questions.find_one({"id": q_id}, {"_id": 0})
    return jsonify(updated), 200


@app.route("/questions/<q_id>", methods=["DELETE"])
@app.route("/api/questions/<q_id>", methods=["DELETE"])
def delete_question(q_id):
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



# --------------------- TESTS & QUIZ ---------------------
@app.route('/test.html')
def serve_test_html():
    # Sử dụng os.path.dirname(__file__) để lấy thư mục của file server.py
    # và phục vụ file test.html từ thư mục đó.
    current_dir = os.path.dirname(os.path.abspath(__file__))
    return send_from_directory(current_dir, 'test.html') 

@app.route("/tests", methods=["GET"])
@app.route("/api/tests", methods=["GET"])
def list_tests():
    query = {}
    subject = request.args.get("subject")
    level = request.args.get("level")
    # THÊM: Lấy tham số lọc theo ngày tạo (Lớn hơn hoặc bằng)
    createdAtGte = request.args.get("createdAtGte") 

    if subject: 
        query["subject"] = subject
    if level: 
        query["level"] = level
    
    if createdAtGte:
        # Lọc theo ngày tạo Lớn hơn hoặc bằng (Frontend gửi YYYY-MM-DD)
        # So sánh chuỗi ISO-8601 (YYYY-MM-DDTHH:MM:SS...) với YYYY-MM-DD bằng $gte hoạt động.
        query["createdAt"] = {"$gte": createdAtGte}

    docs = list(db.tests.find(query, {"_id": 0}))
    return jsonify(docs)
    
@app.route("/quizzes/<test_id>", methods=["GET"])
@app.route("/api/quizzes/<test_id>", methods=["GET"])
@app.route("/tests/<test_id>", methods=["GET"])
@app.route("/api/tests/<test_id>", methods=["GET"])
def get_test(test_id):
    """
    Lấy đề thi theo test_id. Hỗ trợ:
      - tests collection lưu mảng question ids (strings hoặc ObjectId)
      - tests collection lưu mảng objects (rút gọn) cần bù đắp nội dung
    Trả về doc với field 'questions' là list các question full (mỗi question có 'id' là string).
    """
    # Tìm trong tests trước, fallback sang quizzes
    doc = db.tests.find_one({"id": test_id}, {"_id": 0})
    if not doc:
        doc = db.quizzes.find_one({"id": test_id}, {"_id": 0})

    if not doc:
        return jsonify({"message": "Bài kiểm tra không tồn tại."}), 404

    question_list = doc.get("questions", [])
    # Nếu không có questions hoặc rỗng: trả nguyên doc
    if not question_list:
        return jsonify(doc)

    # Chuẩn hoá: nếu items là dicts và đã có nội dung (q hoặc question), trả luôn
    first_item = question_list[0]
    if isinstance(first_item, dict):
        # Nếu mọi object đã có field 'q' (nội dung) hoặc 'question', coi là đầy đủ
        if all(isinstance(x, dict) and ("q" in x or "question" in x) for x in question_list):
            return jsonify(doc)
        # Nếu là list of dict nhưng rút gọn (chỉ id/_id), ta phải bù đắp
        # trích các id string cần resolve
        ids_to_resolve = []
        for q in question_list:
            qid = None
            if isinstance(q.get("id"), str) and q.get("id").strip():
                qid = q.get("id")
            elif q.get("_id"):
                qid = str(q.get("_id"))
            if qid:
                ids_to_resolve.append(qid)
    elif isinstance(first_item, str):
        # list of ids (string) - xử lý bình thường
        ids_to_resolve = question_list.copy()
    else:
        # Không xác định shape -> trả nguyên doc
        return jsonify(doc)

    if not ids_to_resolve:
        return jsonify(doc)

    # Phân loại ids: ObjectId-able vs UUID strings
    object_ids = []
    uuid_strings = []
    for qid_str in ids_to_resolve:
        try:
            object_ids.append(ObjectId(qid_str))
        except Exception:
            uuid_strings.append(qid_str)

    # Tạo query $or
    or_clauses = []
    if object_ids:
        or_clauses.append({"_id": {"$in": object_ids}})
    if uuid_strings:
        or_clauses.append({"id": {"$in": uuid_strings}})

    full_questions = []
    if or_clauses:
        full_questions = list(db.questions.find(
    {"$or": or_clauses},
    {
        "_id": 1,
        "id": 1,
        "q": 1,
        "options": 1,
        "points": 1,
        "type": 1,
        "subject": 1,
        "level": 1,
        "difficulty": 1,
        "answer": 1,
        "imageId": 1, 
    }
))

    # Map bằng cả id (uuid) và str(_id)
    id_map = {}
    for q in full_questions:
        # convert _id to string key
        if q.get("_id") is not None:
            id_map[str(q["_id"])] = q
        if q.get("id"):
            id_map[q["id"]] = q

    # Xây final_questions giữ nguyên thứ tự ban đầu
    final_questions = []
    if isinstance(first_item, dict):
        # mapping nhanh bằng id/_id lấy từ object rút gọn
        for q_lite in question_list:
            # thử lấy id hoặc _id string
            id_key = None
            if isinstance(q_lite.get("id"), str) and q_lite.get("id").strip():
                id_key = q_lite.get("id")
            elif q_lite.get("_id"):
                id_key = str(q_lite.get("_id"))
            if id_key and id_key in id_map:
                q_full = id_map[id_key].copy()
                # chuẩn hoá: convert _id thành string và đảm bảo 'id' field tồn tại
                q_full["_id"] = str(q_full.get("_id")) if q_full.get("_id") is not None else None
                q_full["id"] = q_full.get("id") or q_full["_id"]
                # Loại bỏ trường nội bộ Mongo nếu bạn không muốn trả về _id thô
                # nếu muốn xóa: q_full.pop("_id", None)
                final_questions.append(q_full)
            else:
                # không tìm thấy bản đầy đủ -> giữ nguyên object rút gọn
                final_questions.append(q_lite)
    else:
        # list of ids (strings)
        for qid in ids_to_resolve:
            if qid in id_map:
                q_full = id_map[qid].copy()
                q_full["_id"] = str(q_full.get("_id")) if q_full.get("_id") is not None else None
                q_full["id"] = q_full.get("id") or q_full["_id"]
                final_questions.append(q_full)
            else:
                # không tìm thấy -> skip hoặc giữ id rỗng; mình sẽ skip
                app.logger.warning(f"Question id {qid} not found in questions collection.")
                # bạn có thể append placeholder nếu muốn
                # final_questions.append({"id": qid, "q": "(Không tìm thấy nội dung)"})

    # Gán lại questions và trả
    doc["questions"] = final_questions
    # 🔹 BỔ SUNG: Đảm bảo mọi câu hỏi đều có field 'type'
    for q in doc.get("questions", []):
        # Nếu chưa có type, tự xác định
        if "type" not in q or not q["type"]:
            if q.get("options") and len(q["options"]) > 0:
                q["type"] = "mc"  # trắc nghiệm
            else:
                q["type"] = "essay"  # tự luận
    return jsonify(doc)

# ==================================================
# ✅ THAY THẾ HÀM TẠO ĐỀ THỦ CÔNG (Dòng 483)
# ==================================================
@app.route("/tests", methods=["POST"])
@app.route("/api/tests", methods=["POST"])
def create_test():
    data = request.get_json() or {}
    
    # 1. Lấy dữ liệu từ JS
    name = data.get("name", "Bài thi thủ công")
    time = data.get("time", 45)
    level = data.get("level")
    subject = data.get("subject")
    
    # JS của bạn gửi một danh sách các string ID
    # (Hàm create_test cũ của bạn phức tạp hơn, nhưng hàm này mới đúng)
    question_ids = data.get("questions", []) 

    if not subject:
        return jsonify({"success": False, "message": "Vui lòng chọn Môn học"}), 400
    if not question_ids:
        return jsonify({"success": False, "message": "Vui lòng chọn ít nhất 1 câu hỏi"}), 400

    # 2. ✅ GỌI HÀM TÍNH ĐIỂM MỚI
    # Trả về map: {"q_id_1": 1.5, "q_id_2": 0.5}
    points_map = calculate_question_points(question_ids, db)

    # 3. Định dạng lại mảng câu hỏi để lưu vào DB
    formatted_questions = []
    
    # Lấy 'type' của các câu hỏi (dùng hàm helper có sẵn)
    mc_count, essay_count = calculate_question_counts(question_ids, db)

    for q_id in question_ids: # Giữ nguyên thứ tự từ FE
        points = points_map.get(q_id, 0) # Lấy điểm đã tính
        formatted_questions.append({
            "id": q_id,      # ID của câu hỏi
            "points": points # Điểm đã được tính
        })

    # 4. Tạo tài liệu Test mới
    new_test = {
        "id": str(uuid4()),
        "name": name,
        "time": time,
        "level": level,
        "subject": subject,
        "questions": formatted_questions, # Mảng câu hỏi đã chứa điểm
        "isAutoGenerated": False,
        "createdAt": now_vn_iso(),
        "mcCount": mc_count,
        "essayCount": essay_count,
        "count": len(question_ids) # Thêm count
    }

    # 5. Lưu vào DB
    try:
        db.tests.insert_one(new_test)
        new_test.pop('_id', None) 
        return jsonify(new_test), 201
    except Exception as e:
        return jsonify({"success": False, "message": f"Lỗi server: {e}"}), 500


#from uuid import uuid4
#import datetime
from flask import request, jsonify

# Assuming imports like Flask, jsonify, request, db, uuid4, now_vn_iso, calculate_question_counts are done above

# ==================================================
# ✅ THAY THẾ HÀM TẠO ĐỀ TỰ ĐỘNG (Dòng 542)
# ==================================================
@app.route("/tests/auto", methods=["POST"])
@app.route("/api/tests/auto", methods=["POST"])
def create_test_auto():
    data = request.get_json() or {}
    
    # 1. Lấy dữ liệu từ JS
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

    # 2. Xây dựng query
    query = {}
    if subject:
        query["subject"] = subject
    if level:
        query["level"] = level

    # 3. Lấy câu hỏi ngẫu nhiên (dùng $sample)
    def pick(diff, count):
        if count == 0: return []
        q = {**query, "difficulty": diff}
        pipeline = [
            {"$match": q},
            {"$sample": {"size": count}},
            {"$project": {"id": 1, "_id": 1, "type": 1}} # Chỉ lấy ID và type
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

    # 4. ✅ GỌI HÀM TÍNH ĐIỂM MỚI
    points_map = calculate_question_points(all_question_ids, db)

    # 5. Định dạng mảng câu hỏi và đếm type
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
            
    # 6. Tạo tài liệu Test mới
    new_test = {
        "id": str(uuid4()),
        "name": name,
        "time": time,
        "subject": subject,
        "level": level,
        "questions": formatted_questions,
        "isAutoGenerated": True,
        "createdAt": now_vn_iso(),
        "mcCount": mc_count,
        "essayCount": essay_count,
        "count": len(formatted_questions)
    }
    
    # 7. Lưu vào DB
    try:
        db.tests.insert_one(new_test)
        new_test.pop('_id', None)
        return jsonify(new_test), 201
    except Exception as e:
        return jsonify({"success": False, "message": f"Lỗi server: {e}"}), 500


# ==================================================
# ✅ THAY THẾ HÀM CẬP NHẬT ĐỀ THI (Dòng 629)
# ==================================================
@app.route("/tests/<test_id>", methods=["PUT"])
@app.route("/api/tests/<test_id>", methods=["PUT"])
def update_test(test_id):
    data = request.get_json() or {}
    
    # 1. Lấy dữ liệu mới từ JS
    name = data.get("name")
    time = data.get("time")
    level = data.get("level")
    subject = data.get("subject")
    
    # JS gửi một danh sách các object: [{"_id": "uuid1"}, ...]
    # (Trường 'points' trong payload này không dùng, ta sẽ tính lại)
    questions_from_js = data.get("questions", [])
    
    # Lấy ID (ưu tiên 'id', fallback về '_id')
    question_ids = [q.get('id') or q.get('_id') for q in questions_from_js if q.get('id') or q.get('_id')]

    if not subject:
        return jsonify({"success": False, "message": "Vui lòng chọn Môn học"}), 400
    if not question_ids:
        return jsonify({"success": False, "message": "Vui lòng chọn ít nhất 1 câu hỏi"}), 400

    # 2. ✅ GỌI LẠI HÀM TÍNH ĐIỂM
    points_map = calculate_question_points(question_ids, db)

    # 3. Định dạng lại mảng câu hỏi để lưu vào DB
    formatted_questions = []
    
    # Lấy 'type' của các câu hỏi (dùng hàm helper có sẵn)
    mc_count, essay_count = calculate_question_counts(question_ids, db)

    for q_id in question_ids: # Giữ nguyên thứ tự mới
        points = points_map.get(q_id, 0)
        formatted_questions.append({
            "id": q_id,
            "points": points
        })
            
    # 4. Tạo đối tượng $set để cập nhật
    update_data = {
        "name": name,
        "time": time,
        "level": level,
        "subject": subject,
        "questions": formatted_questions, # Danh sách câu hỏi MỚI với điểm MỚI
        "mcCount": mc_count,
        "essayCount": essay_count,
        "count": len(question_ids)
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


@app.route("/tests/<test_id>", methods=["DELETE"])
@app.route("/api/tests/<test_id>", methods=["DELETE"])
def delete_test(test_id):
    try:
        # Tìm và xóa đề theo id
        result = db.tests.delete_one({"id": test_id})
        if result.deleted_count == 0:
            return jsonify({"message": "Bài kiểm tra không tồn tại."}), 404
        return jsonify({"message": "Đã xóa đề thi thành công!"}), 200
    except Exception as e:
        print("Error deleting test:", e)
        return jsonify({"message": "Không thể xóa đề thi.", "error": str(e)}), 500

# --------------------- ASSIGNS ---------------------
@app.route("/assigns", methods=["GET"])
@app.route("/api/assigns", methods=["GET"])
def list_assigns():
    """
    API tổng hợp cho giáo viên (Admin View).
    Dùng Aggregation để JOIN Assignments + Tests + Results.
    """
    try:
        studentId = request.args.get("studentId")
        # Không cần studentId nếu dùng cho giáo viên (có thể thêm logic sau)
        # Nếu không có studentId, trả về tất cả assignments nếu cần
        if not studentId:
             # Nếu không có studentId (dùng cho Teacher), sẽ lấy tất cả assignments
             match_stage = {}
        else:
             match_stage = {"studentId": studentId}


        pipeline = [
            {"$match": match_stage},

            # Join tests (sử dụng id trong testInfo)
            {
                "$lookup": {
                    "from": "tests",
                    "localField": "testId",
                    "foreignField": "id",
                    "as": "testInfo"
                }
            },
            {"$unwind": {"path": "$testInfo", "preserveNullAndEmptyArrays": True}},

            # Join results (sử dụng assignmentId)
            {
                "$lookup": {
                    "from": "results",
                    "localField": "id",
                    "foreignField": "assignmentId",
                    "as": "resultInfo"
                }
            },
            {"$unwind": {"path": "$resultInfo", "preserveNullAndEmptyArrays": True}},
            
            {
                "$project": {
                    "_id": 0,
                    "id": 1,
                    "testId": 1,
                    "studentId": 1,
                    "deadline": 1,
                    "status": 1,
                    "assignedAt": {"$ifNull": ["$assignedAt", "$timeAssigned"]}, # Chuẩn hóa trường assignedAt
                    "submittedAt": "$resultInfo.submittedAt",
                    
                    # ✅ LẤY TỪ RESULTS ĐÃ CHUẨN HÓA
                    "gradingStatus": "$resultInfo.gradingStatus",
                    "totalScore": {"$ifNull": ["$resultInfo.totalScore", None]},
                    "mcScore": {"$ifNull": ["$resultInfo.mcScore", None]},
                    "essayScore": {"$ifNull": ["$resultInfo.essayScore", None]},
                    
                    # Thông tin Test
                    "testName": "$testInfo.name",
                    "subject": "$testInfo.subject",
                    "time": "$testInfo.time",
                    "mcCount": "$testInfo.mcCount",
                    "essayCount": "$testInfo.essayCount",
                }
            }
        ]

        docs = list(db.assignments.aggregate(pipeline))

        # Auto-map status submitted (dựa trên sự tồn tại của resultInfo)
        for a in docs:
            # Nếu có submittedAt, chắc chắn bài đã nộp
            if a.get("submittedAt"):
                a["status"] = "submitted"
            
            # Làm tròn điểm
            if a.get("totalScore") is not None:
                a["totalScore"] = round(a["totalScore"], 2)
            if a.get("mcScore") is not None:
                a["mcScore"] = round(a["mcScore"], 2)
            if a.get("essayScore") is not None:
                a["essayScore"] = round(a["essayScore"], 2)


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
            "id": str(uuid4()),
            "testId": test_id,
            "studentId": sid,
            "deadline": data.get("deadline"),
            "status": "assigned",
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

# --------------------- ASSIGNS (BULK ASSIGN) ---------------------
@app.route("/api/assigns/bulk", methods=["POST"])
def bulk_assign_tests():
    """
    Xử lý giao một hoặc nhiều đề thi (testIds) cho học sinh (studentIds).
    Payload dự kiến: {testIds: array, studentIds: array, teacherId: string, deadline: string | null}
    """
    try:
        data = request.get_json() or {}
        
        # 1. Lấy dữ liệu từ Frontend
        test_ids = data.get("testIds", [])      
        student_ids = data.get("studentIds", [])
        teacher_id = data.get("teacherId")
        deadline_iso = data.get("deadline") 
        
        # Kiểm tra dữ liệu đầu vào cơ bản
        if not isinstance(test_ids, list) or not isinstance(student_ids, list) or not teacher_id:
            return jsonify({"message": "Dữ liệu đầu vào thiếu hoặc không đúng định dạng (testIds, studentIds, teacherId).", "count": 0}), 400
        
        # Đảm bảo phải có đề thi và học sinh để giao
        if not test_ids or not student_ids:
            return jsonify({"message": "Vui lòng chọn ít nhất một đề thi và một học sinh.", "count": 0}), 400
        
        # 2. Lấy thông tin học sinh dựa trên student_ids
        students_cursor = db.users.find(
            {"id": {"$in": student_ids}}, 
            {"id": 1, "fullName": 1, "className": 1}
        )
        student_map = {s['id']: s for s in students_cursor}
        valid_student_ids = list(student_map.keys())
        
        if not valid_student_ids:
            return jsonify({"message": f"Không tìm thấy học sinh hợp lệ nào từ danh sách đã chọn.", "count": 0}), 200

        # 3. Lấy thông tin chi tiết của tất cả đề thi
        test_docs_cursor = db.tests.find(
            {"id": {"$in": test_ids}},
            {"_id": 0, "id": 1, "name": 1, "subject": 1}
        )
        test_map = {t['id']: t for t in test_docs_cursor}

        assignments_to_insert = []
        
        # 4. Xử lý Logic Giao Bài (Vòng lặp lồng nhau)
        for t_id in test_ids:
            test_info = test_map.get(t_id)
            if not test_info:
                print(f"⚠️ Test ID {t_id} không tìm thấy. Bỏ qua.")
                continue

            for stu_id in valid_student_ids:
                student = student_map.get(stu_id) 
                if not student: continue 
                
                # Kiểm tra bài giao đã tồn tại
                existing_assignment = db.assignments.find_one({
                    "testId": t_id,
                    "studentId": stu_id,
                })

                if existing_assignment:
                    # Nếu đã giao: CẬP NHẬT deadline, teacherId và assignedAt (trong trường hợp assignedAt bị thiếu)
                    update_set = {
                        "teacherId": teacher_id,
                        "deadline": deadline_iso,
                    }
                    # Đảm bảo assignedAt tồn tại cho các bản ghi cũ
                    if "assignedAt" not in existing_assignment and "createdAt" not in existing_assignment:
                         update_set["assignedAt"] = now_vn_iso()
                         
                    db.assignments.update_one(
                        {"id": existing_assignment["id"]},
                        {"$set": update_set}
                    )
                else:
                    # Nếu chưa giao: CHÈN MỚI
                    new_assign = {
                        "id": str(uuid4()), 
                        "testId": t_id,
                        "testName": test_info.get("name"), 
                        "studentId": stu_id,
                        "studentName": student.get("fullName"), 
                        "className": student.get("className"), 
                        "teacherId": teacher_id,
                        "deadline": deadline_iso,
                        "status": "pending",
                        "assignedAt": now_vn_iso(), # ✅ GHI BẰNG assignedAt
                    }
                    assignments_to_insert.append(new_assign)
        
        # 5. Chèn tất cả bài giao mới (Bulk Insert)
        if assignments_to_insert:
            db.assignments.insert_many(assignments_to_insert)

        # 6. Cập nhật trạng thái "Đã giao" cho các đề thi đã chọn
        db.tests.update_many(
            {"id": {"$in": test_ids}},
            {"$set": {"assignmentStatus": "assigned"}}
        )

        total_processed_count = len(test_ids) * len(valid_student_ids) 
        
        # 7. Trả về kết quả
        return jsonify({
            "success": True, 
            "count": len(test_ids),
            "totalAssignmentsProcessed": total_processed_count,
            "message": f"Đã giao thành công {len(test_ids)} đề thi cho {len(valid_student_ids)} học sinh (Tổng: {total_processed_count} bài giao)."
        }), 201

    except Exception as e:
        print(f"Lỗi khi thực hiện bulk_assign_tests: {e}")
        return jsonify({"message": "Lỗi máy chủ khi giao/cập nhật đề.", "count": 0}), 500

@app.route("/api/tests/<test_id>/assignments", methods=["GET"])
def get_test_assignments(test_id):
    """
    Lấy danh sách assignments chi tiết cho một đề thi, 
    bao gồm tên học sinh và trạng thái làm bài (done).
    """
    try:
        # 1. Lấy tất cả Assignments cho test_id
        assignments = list(db.assignments.find({"testId": test_id}, {"_id": 0}))
        
        # 2. Lấy danh sách ID học sinh
        student_ids = [a.get("studentId") for a in assignments if a.get("studentId")]
        
        # 3. Lấy thông tin học sinh (Tên, Lớp, Role)
        # 🔥 ĐÃ SỬA: Truy vấn 'fullName' và 'className' thay vì 'name' và 'class'
        students_cursor = db.users.find(
            {"id": {"$in": student_ids}}, 
            {"_id": 0, "id": 1, "fullName": 1, "className": 1, "role": 1}
        )
        student_map = {s["id"]: s for s in students_cursor}

        # 4. Ghép dữ liệu và trả về
        results = []
        for a in assignments:
            # 🔥 ĐÃ SỬA: Đảm bảo sử dụng 'fullName' và 'className'
            student_info = student_map.get(a.get("studentId"), {
                "fullName": "Không rõ", 
                "className": "N/A",
                "role": "student"
            })
            
            # Gán dữ liệu cho Frontend (sử dụng .get() an toàn hơn)
            a['studentName'] = student_info.get('fullName', 'Không rõ') 
            a['studentClass'] = student_info.get('className', 'N/A')
            a['studentRole'] = student_info.get('role', 'student')
            
            results.append(a)
            
        return jsonify(results), 200

    except Exception as e:
        # Bạn nên sử dụng logging thay vì print trong môi trường production
        print(f"Lỗi khi lấy assignment cho test {test_id}: {e}")
        return jsonify({"message": "Lỗi máy chủ."}), 500

@app.route("/api/assignments/bulk-delete", methods=["POST"])
def bulk_delete_assignments():
    """Xóa nhiều assignments cùng lúc dựa trên danh sách ID."""
    data = request.get_json() or {}
    assignment_ids = data.get("assignmentIds", [])

    if not assignment_ids:
        return jsonify({"message": "Thiếu danh sách assignmentIds", "deletedCount": 0}), 400

    try:
        # Xóa tất cả tài liệu có ID nằm trong danh sách
        result = db.assignments.delete_many({"id": {"$in": assignment_ids}})
        
        return jsonify({"message": f"Đã xóa {result.deleted_count} assignments.", "deletedCount": result.deleted_count}), 200

    except Exception as e:
        print(f"Lỗi khi xóa hàng loạt assignments: {e}")
        return jsonify({"message": "Lỗi máy chủ khi xóa hàng loạt assignment.", "deletedCount": 0}), 500

# --------------------- ASSIGNMENTS (Student View) ---------------------
@app.route("/api/assignments", methods=["GET"])
def get_assignments_for_student():
    student_id = request.args.get("studentId")
    if not student_id:
        return jsonify({"success": False, "message": "Missing studentId parameter"}), 400

    # Tìm tất cả assignments cho student_id này
    assignments = list(db.assignments.find({
        "studentId": student_id
    }, {"_id": 0})) 

    if not assignments:
        return jsonify({"success": True, "assignments": []})

    # Gộp thông tin bài thi (testName, subject, time,...)
    test_ids = [a["testId"] for a in assignments if a.get("testId")]
    tests = db.tests.find({"id": {"$in": test_ids}}, 
                           {"_id": 0, "id": 1, "name": 1, "subject": 1, "time": 1, "mcCount": 1, "essayCount": 1})
    tests_map = {t["id"]: t for t in tests}

    # Tạo danh sách kết quả cuối cùng
    result_list = []
    for a in assignments:
        test_info = tests_map.get(a["testId"], {})
        
        # 🔥 FIX: Ưu tiên lấy assignedAt, nếu không có thì lấy createdAt để tương thích với bản ghi cũ
        assigned_date = a.get("assignedAt") or a.get("createdAt") 
        
        result_list.append({
            "assignmentId": a.get("id"),
            "testId": a["testId"],
            "testName": test_info.get("name", a.get("testName", "N/A")), # Fallback về testName trong assignment
            "subject": test_info.get("subject", "N/A"),
            "time": test_info.get("time"),
            "mcCount": test_info.get("mcCount", 0),
            "essayCount": test_info.get("essayCount", 0),
            "deadline": a.get("deadline"),
            "assignedAt": assigned_date, # ✅ TRUYỀN DỮ LIỆU ĐÃ ĐƯỢC CHUẨN HÓA
            "status": a.get("status", "pending"),
        })
        
    return jsonify({"success": True, "assignments": result_list})


# --------------------- RESULTS ---------------------
# ==================================================
# ✅ THAY THẾ HÀM NỘP BÀI (Dòng 777)
# ==================================================
@app.route("/results", methods=["POST"])
@app.route("/api/results", methods=["POST"])
def create_result():
    try:
        data = request.get_json() or {}
        student_id = data.get("studentId") or data.get("student_id")
        assignment_id = data.get("assignmentId") or data.get("assignment_id")
        test_id = data.get("testId") or data.get("test_id")
        student_answers = data.get("studentAnswers", []) or data.get("answers", []) or []

        if not student_id or not assignment_id or not test_id:
            return jsonify({"message": "Thiếu ID (studentId, assignmentId, testId)"}), 400

        # 1. Lấy thông tin Test
        test_doc = db.tests.find_one({"id": test_id})
        if not test_doc:
            return jsonify({"message": "Không tìm thấy đề thi"}), 404

        test_questions = test_doc.get("questions", []) or []
        
        # 2. ✅ TẠO MAP ĐIỂM SỐ (Lấy từ test_doc)
        # test_questions bây giờ là: [{'id': 'q1_id', 'points': 1.5}, ...]
        points_map = {q.get('id'): q.get('points', 1) for q in test_questions}
        question_ids_in_test = list(points_map.keys())

        # 3. Lấy đáp án đúng và type (vẫn phải lấy từ db.questions)
        correct_questions = list(db.questions.find({"id": {"$in": question_ids_in_test}}))

        correct_answer_map = {}
        type_map = {}
        has_essay = False
        
        for q in correct_questions:
            q_id = q.get("id")
            q_type = q.get("type", "mc")
            type_map[q_id] = q_type
            
            if q_type == "mc":
                correct_opt = next((opt.get("text") for opt in q.get("options", []) if opt.get("correct")), None)
                correct_answer_map[q_id] = correct_opt
            elif q_type == "essay":
                correct_answer_map[q_id] = q.get("answer") # Gợi ý
                has_essay = True

        # 4. Tạo map câu trả lời của học sinh (Từ hàm cũ của bạn)
        student_ans_map = {}
        for ans in student_answers:
            if not isinstance(ans, dict): continue
            qkey = ans.get("questionId") or ans.get("question_id") or ans.get("qid") or ans.get("id")
            if qkey:
                student_ans_map[str(qkey)] = ans.get("answer") or ans.get("studentAnswer") or ans.get("value") or ans.get("selected") or ""

        mc_score = 0.0
        detailed_results = []

        # helper: chuẩn hoá string để so sánh
        def norm_str(x):
            if x is None: return ""
            return str(x).strip().lower()

        # 5. LẶP VÀ TÍNH ĐIỂM
        for q_id in question_ids_in_test:
            q_type = type_map.get(q_id, "mc")
            
            # ✅ SỬA LOGIC: Lấy điểm TỪ BÀI THI
            max_points = float(points_map.get(q_id, 1))

            student_ans_value = student_ans_map.get(q_id, None)

            is_correct = None
            points_gained = 0.0
            
            correct_ans_text = correct_answer_map.get(q_id)

            # Xử lý Trắc nghiệm (MC)
            if q_type == "mc":
                is_correct = (student_ans_value is not None) and \
                             (correct_ans_text is not None) and \
                             (norm_str(student_ans_value) == norm_str(correct_ans_text))

                if is_correct:
                    points_gained = max_points
                    mc_score += max_points

            # Xử lý Tự luận (Essay)
            elif q_type == "essay":
                essay_count += 1
                points_gained = 0.0 # Chờ chấm
                is_correct = None # Chờ chấm
            
            # (Các loại khác nếu có)
            else:
                is_correct = (student_ans_value is not None) and \
                             (correct_ans_text is not None) and \
                             (norm_str(student_ans_value) == norm_str(correct_ans_text))
                if is_correct:
                    points_gained = max_points
                    mc_score += max_points


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
        import traceback
        traceback.print_exc()
        return jsonify({"message": f"Server error: {str(e)}"}), 500
    
# Chấm bài tự luận
from flask import abort
#from datetime import datetime, timedelta

# ==================================================
# ✅ THAY THẾ HÀM CHẤM ĐIỂM (Dòng 924)
# ==================================================
@app.route("/api/results/<result_id>/grade", methods=["POST"])
def grade_result(result_id):
    """
    Giáo viên chấm điểm (Logic đã sửa theo yêu cầu):
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
        # test_doc["questions"] là: [{'id': 'q1', 'points': 1.5}, {'id': 'q2', 'points': 0.5}, ...]
        points_map = {q.get('id'): q.get('points', 1) for q in test_doc.get('questions', [])}

        # === 3. LẤY ĐIỂM TRẮC NGHIỆM ĐÃ CHẤM TỰ ĐỘNG (FIXED) ===
        # Chúng ta tin tưởng điểm MC đã được tính đúng lúc nộp bài (create_result)
        new_mc_score = result.get("mcScore", 0.0) 
        new_essay_score = 0.0
        
        # === 4. XỬ LÝ ĐIỂM TỰ LUẬN MỚI TỪ GIÁO VIÊN ===
        has_ungraded_essay = False # Flag để kiểm tra xem GV có bỏ sót câu nào không

        # Lặp qua TẤT CẢ các câu trong bài làm
        for q_id_str, det in detailed_map.items():
            
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
                    
                    # ✅ LOGIC KHỐNG CHẾ ĐIỂM (THEO YÊU CẦU CỦA BẠN)
                    if ts_float > max_points:
                        ts_float = max_points # Không cho phép vượt điểm tối đa
                    if ts_float < 0:
                        ts_float = 0.0 # Không cho phép điểm âm
                        
                    # Cập nhật chi tiết
                    det["teacherScore"] = ts_float
                    det["teacherNote"] = essay_data.get("teacherNote", "")
                    det["pointsGained"] = ts_float
                    det["isCorrect"] = ts_float > 0
                    
                    new_essay_score += ts_float # Cộng vào điểm tự luận tổng
                
                else:
                    # Giáo viên KHÔNG chấm câu này (bỏ qua/để trống)
                    if det.get("teacherScore") is None:
                        # Nếu trước đó nó chưa được chấm (vẫn là None)
                        has_ungraded_essay = True # Đánh dấu là còn câu chưa chấm
                    else:
                        # Nếu GV không chấm, nhưng trước đó đã có điểm, ta giữ điểm đó
                        new_essay_score += float(det.get("pointsGained", 0.0))

            # (Chúng ta không cần làm gì với câu 'mc' vì new_mc_score đã lấy ở trên)

        # === 5. Tính điểm tổng và xác định trạng thái ===
        new_total_score = new_mc_score + new_essay_score
        graded_at = now_vn_iso()
        
        if has_ungraded_essay:
             new_status = "Đang Chấm" # Nếu còn câu chưa chấm -> Vẫn là Đang Chấm
        elif current_regrade + 1 >= 2:
            new_status = "Hoàn tất" # Đã chấm hết và đủ số lần -> Hoàn tất
        else:
            new_status = "Đã Chấm" # Đã chấm hết lần 1 -> Đã Chấm

        # === 6. Cập nhật DB ===
        update_payload = {
            "detailedResults": list(detailed_map.values()), # Lưu chi tiết mới
            "totalScore": round(new_total_score, 2),       # Tổng điểm MỚI
            "mcScore": round(new_mc_score, 2),             # Điểm MC (Giữ nguyên)
            "essayScore": round(new_essay_score, 2),       # Điểm Tự luận MỚI
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
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e), "message": "Internal Server Error"}), 500


# API mới để lấy danh sách kết quả tổng hợp cho giáo viên (Yêu cầu 1)
@app.route("/api/results_summary", methods=["GET"])
def get_results_summary():

    # 1. Truy vấn Aggregation để join dữ liệu
    pipeline = [
        # Giai đoạn 1: THAY THẾ LOOKUP 'users'
        {
            "$lookup": {
                "from": "users",
                "let": { "sid": "$studentId" }, 
                "pipeline": [
                    { "$match": {
                        "$expr": {
                            "$or": [
                                { "$eq": [ "$id", "$$sid" ] }, 
                                { "$eq": [ { "$toString": "$_id" }, "$$sid" ] }
                            ]
                        }
                    }},
                    # Chỉ lấy các trường cần thiết
                    { "$project": { "fullName": 1, "className": 1, "_id": 0 } } 
                ],
                "as": "student_info"
            }
        },
        {"$unwind": {"path": "$student_info", "preserveNullAndEmptyArrays": True}},

        # Giai đoạn 2: Join với collection 'tests' (Giữ nguyên)
        {
            "$lookup": {
                "from": "tests",
                "localField": "testId",
                "foreignField": "id",
                "as": "test_info"
            }
        },
        {"$unwind": {"path": "$test_info", "preserveNullAndEmptyArrays": True}},

        # Giai đoạn 3: Project (Giữ nguyên)
        {
            "$project": {
                "_id": 0, 
                "id": "$id",
                "studentId": "$studentId",
                "testId": "$testId",

                "totalScore": {"$ifNull": ["$totalScore", 0.0]},
                "mcScore": {"$ifNull": ["$mcScore", 0.0]},
                "essayScore": {"$ifNull": ["$essayScore", 0.0]},
                "gradingStatus": {"$ifNull": ["$gradingStatus", "Đang Chấm"]},
                "gradedAt": {"$ifNull": ["$gradedAt", None]}, 
                "submittedAt": "$submittedAt",

                "testName": {"$ifNull": ["$test_info.name", "Đã Xóa"]},
                "studentName": {"$ifNull": ["$student_info.fullName", "N/A"]}, # Sửa lỗi N/A
                "className": {"$ifNull": ["$student_info.className", "N/A"]}, # Sửa lỗi N/A
            }
        }
    ]

    docs = list(db.results.aggregate(pipeline))
    
    # 2. Xử lý logic nghiệp vụ (CHUẨN HÓA TRẠNG THÁI CHO FRONTEND)
    for doc in docs:
        doc.pop("detailedResults", None) 
        
        status_from_db = doc.get("gradingStatus")
        
        # 1. Trạng thái Hoàn tất (Đảm bảo tất cả các trạng thái đã xong đều là Hoàn tất)
        if status_from_db in ["Hoàn tất", "Tự động hoàn tất", "Đã Chấm Lại"]:
            doc["gradingStatus"] = "Hoàn tất"
        
        # 2. Trạng thái đã chấm (Lần 1)
        elif status_from_db == "Đã Chấm":
             doc["gradingStatus"] = "Đã Chấm" 
             
        # 3. Trạng thái Đang Chấm (Bao gồm Chưa Chấm, Lỗi, hoặc bất kỳ giá trị không hợp lệ nào)
        else:
             doc["gradingStatus"] = "Đang Chấm"
        
        # Chuyển đổi và làm tròn điểm
        doc["totalScore"] = round(doc.get("totalScore", 0.0), 2)
        doc["mcScore"] = round(doc.get("mcScore", 0.0), 2)
        doc["essayScore"] = round(doc.get("essayScore", 0.0), 2)
        
    return jsonify(docs)

from flask import jsonify
# Giả định db (MongoDB client) đã được định nghĩa và khởi tạo
# Ví dụ: from app import db

@app.route("/results/<result_id>", methods=["GET"])
@app.route("/api/results/<result_id>", methods=["GET"])
def get_result_detail(result_id):
    """
    Lấy chi tiết kết quả bài làm theo ID, sử dụng Aggregation để join với tests (tên bài thi)
    và users (tên học sinh/lớp) để khắc phục lỗi N/A.
    """
    # Cần đảm bảo thư viện ObjectId được import ở đầu file Python
    from bson.objectid import ObjectId 
    
    try:
        # 1. Match the specific Result (match bằng id hoặc _id)
        match_query = {"$or": [{"id": result_id}]}
        try:
            # Cố gắng thêm ObjectId nếu result_id là chuỗi 24 ký tự
            match_query["$or"].append({"_id": ObjectId(result_id)})
        except Exception:
            pass

        pipeline = [
            {"$match": match_query}
        ]

        # 2. Join với Tests (Giữ nguyên - Đã hoạt động)
        pipeline.append({
            "$lookup": {
                "from": "tests",
                "localField": "testId",
                "foreignField": "id",
                "as": "test_info"
            }
        })
        pipeline.append({"$unwind": {"path": "$test_info", "preserveNullAndEmptyArrays": True}})

        # 3. Join với Users (CRITICAL FIX: JOIN LINH HOẠT QUA PIPELINE)
        pipeline.append({
            "$lookup": {
                "from": "users",
                "let": { "sid": "$studentId" }, # Lưu studentId từ Results vào biến
                "pipeline": [
                    { "$match": {
                        "$expr": {
                            "$or": [
                                # 1. Khớp với trường 'id' (UUID string)
                                { "$eq": [ "$id", "$$sid" ] }, 
                                # 2. Khớp với trường '_id' (ObjectId) - chuyển đổi sang string
                                { "$eq": [ { "$toString": "$_id" }, "$$sid" ] }
                            ]
                        }
                    }},
                    { "$project": { "fullName": 1, "className": 1, "_id": 0 } } # Chỉ lấy tên/lớp
                ],
                "as": "student_info"
            }
        })
        # LƯU Ý: 'True' phải viết hoa trong Python
        pipeline.append({"$unwind": {"path": "$student_info", "preserveNullAndEmptyArrays": True}})

        # 4. Project Final - Đưa các trường Join vào Document chính
        pipeline.append({
            "$project": {
                # Trường gốc
                "_id": 0,
                "id": {"$ifNull": ["$id", {"$toString": "$_id"}]}, # Đảm bảo ID là string
                "assignmentId": 1,
                "testId": 1,
                "studentId": 1, 
                "submittedAt": 1,
                "gradedAt": 1,
                "gradingStatus": 1,
                "totalScore": 1,
                "mcScore": 1,
                "essayScore": 1,
                "teacherNote": 1,
                "regradeCount": 1,
                "studentAnswers": 1,
                "detailedResults": 1,
                
                # Trường từ Join Test
                "testName": {"$ifNull": ["$test_info.name", "Bài thi đã xóa"]},
                "subject": {"$ifNull": ["$test_info.subject", "khác"]}, 
                
                # Trường từ Join User (SỬA LỖI N/A)
                "studentName": {"$ifNull": ["$student_info.fullName", "N/A"]},
                "className": {"$ifNull": ["$student_info.className", "N/A"]}
            }
        })

        results = list(db.results.aggregate(pipeline))

        if not results:
            return jsonify({"message": "Result not found"}), 404

        # Endpoint này chỉ trả về 1 document duy nhất
        return jsonify(results[0])

    except Exception as e:
        # Ghi log lỗi để debug
        print(f"Lỗi khi lấy chi tiết result {result_id}: {e}")
        # Trả về lỗi server nếu có
        return jsonify({"message": f"Server error: {e}"}), 500

# API mới để thống kê bài giao (Yêu cầu 3 - ĐÃ CẬP NHẬT)
@app.route("/api/assignment_stats", methods=["GET"])
def get_assignment_stats():
    try:
        # Đếm tổng số bài thi đã tạo
        total_tests_created = db.tests.count_documents({})

        # Đếm tổng số lượt giao bài
        total_assignments = db.assignments.count_documents({})

        # Đếm số học sinh duy nhất đã được giao bài (Vẫn giữ nguyên)
        # Giả định rằng chỉ giao bài cho các vai trò học sinh/cán bộ lớp
        unique_students_assigned_list = db.assignments.distinct("studentId")
        unique_students_assigned = len(unique_students_assigned_list)

        # Đếm tổng số kết quả đã nộp
        total_results_submitted = db.results.count_documents({})

        # --- ✅ SỬA LOGIC ĐẾM TỔNG SỐ HỌC SINH ---
        # Bao gồm các vai trò: student, monitor, vice_monitor, team_leader
        student_roles = ["student", "monitor", "vice_monitor", "team_leader"]
        total_students_with_roles = db.users.count_documents({"role": {"$in": student_roles}})
        # --- KẾT THÚC SỬA ---

        # Trả về thống kê
        return jsonify({
            "totalTestsCreated": total_tests_created,
            "totalAssignments": total_assignments,
            "uniqueStudentsAssigned": unique_students_assigned,
            "totalResultsSubmitted": total_results_submitted,
            # ✅ TRẢ VỀ SỐ LƯỢNG ĐÃ LỌC THEO VAI TRÒ
            "totalStudents": total_students_with_roles
        })
    except Exception as e:
        print(f"Lỗi khi lấy thống kê assignments: {e}")
        # Trả về lỗi
        return jsonify({
            # ... giá trị mặc định ...
             "totalTestsCreated": 0, "totalAssignments": 0, "uniqueStudentsAssigned": 0, "totalResultsSubmitted": 0, "totalStudents": 0, "error": str(e)
        }), 500


# ✅ API Lấy danh sách Results cho học sinh (FIXED: Đã thêm $lookup để lấy testName)
@app.route("/api/results", methods=["GET"])
def get_results_for_student():
    """
    Lấy tất cả các bài đã làm (Results) cho một học sinh cụ thể (sử dụng studentId).
    FIX: Đã thêm $lookup để lấy testName.
    """
    student_id = request.args.get("studentId")
    if not student_id:
        return jsonify({"message": "Missing studentId parameter"}), 400

    try:
        # SỬA LỖI: Dùng Aggregation để join tên
        pipeline = [
            {"$match": {"studentId": student_id}},
            # Join với tests
            {
                "$lookup": {
                    "from": "tests",
                    "localField": "testId",
                    "foreignField": "id",
                    "as": "test_info"
                }
            },
            {"$unwind": {"path": "$test_info", "preserveNullAndEmptyArrays": True}},

            # Project để trả về cấu trúc giống Result gốc + testName (ĐÃ SỬA LỖI HOÀN CHỈNH)
            {"$project": {
                # 1. Bỏ _id
                "_id": 0, 
                
                # 2. SỬA LỖI ID:
                # Lấy trường "id" (nếu có), nếu không có,
                # lấy trường "_id" và chuyển nó thành chuỗi (string)
                "id": {"$ifNull": ["$id", {"$toString": "$_id"}]}, 
                
                # 3. GIỮ LẠI TẤT CẢ CÁC TRƯỜNG DỮ LIỆU CẦN THIẾT
                "assignmentId": 1,
                "testId": 1,
                "testName": {"$ifNull": ["$test_info.name", "Bài thi đã xóa"]},
                "subject": {"$ifNull": ["$test_info.subject", "khác"]}, 
                "submittedAt": 1,
                "gradedAt": 1,
                "gradingStatus": 1,
                "totalScore": 1,
                "mcScore": 1,
                "essayScore": 1,
                "studentAnswers": 1, 
                "detailedResults": 1 
            }}
        ]
        results = list(db.results.aggregate(pipeline))

        # Frontend (hàm processAssignments) mong đợi một mảng các Results.
        return jsonify(results)

    except Exception as e:
        print(f"Lỗi khi lấy results cho student {student_id}: {e}")
        return jsonify([]), 500

# Serve frontend files (unchanged)
@app.route("/", methods=["GET"])
def index():
    try:
        return send_from_directory(".", "index.html")
    except Exception:
        return jsonify({"message": "Index not found"}), 404

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
