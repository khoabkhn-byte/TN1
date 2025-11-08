# =================================================================
# SAO CHÉP VÀ THAY THẾ TOÀN BỘ FILE server31.py CỦA BẠN BẰNG CODE NÀY
# =================================================================

from bson.objectid import ObjectId
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from pymongo import MongoClient, DESCENDING
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
import pandas as pd
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
# ✅ THAY THẾ TOÀN BỘ HÀM NÀY (Khoảng dòng 228)
# ==================================================
def calculate_question_counts(question_ids, db):
    """Tính toán số câu MC, Essay, TF, Fill, Draw từ danh sách ID câu hỏi."""
    if not question_ids:
        return 0, 0, 0, 0, 0 # Trả về 5 giá trị

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
        return 0, 0, 0, 0, 0
        
    question_types = list(db.questions.find(
        {"$or": or_clauses},
        {"type": 1, "options": 1} # Lấy cả 'options' để fallback
    ))

    mc_count = 0
    essay_count = 0
    tf_count = 0  
    fill_count = 0 
    draw_count = 0 # <-- THÊM MỚI

    for q in question_types:
        q_type = q.get("type", "").lower()
        
        if q_type == "essay":
            essay_count += 1
        elif q_type == "draw":
            draw_count += 1 # <-- SỬA TỪ essay_count
        elif q_type == "true_false": 
            tf_count += 1
        elif q_type == "fill_blank": 
            fill_count += 1
        elif q_type == "mc":
            mc_count += 1
        elif not q_type: # Fallback
             if q.get("options") and len(q.get("options")) > 0:
                mc_count += 1 
             else:
                essay_count += 1

    return mc_count, essay_count, tf_count, fill_count, draw_count # <-- Trả về 5 giá trị

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

# THAY THẾ HÀM CŨ 'get_question_stats' (khoảng dòng 452) BẰNG HÀM NÀY
@app.route("/api/questions/<question_id>/stats", methods=["GET"])
def get_question_stats(question_id):
    """
    API Phân tích Nâng cao: Xử lý MC, Đúng/Sai (TF), và Điền từ (Fill).
    """
    try:
        # 1. Lấy thông tin câu hỏi
        question = db.questions.find_one({"id": question_id})
        if not question:
            try:
                question = db.questions.find_one({"_id": ObjectId(question_id)})
            except Exception:
                return jsonify({"message": "Không tìm thấy câu hỏi"}), 404
        if not question:
            return jsonify({"message": "Không tìm thấy câu hỏi"}), 404

        q_type = question.get("type", "mc").lower()
        q_text = question.get("q")
        # Lấy ID chính (ưu tiên UUID, fallback về str(ObjectID))
        q_id_str = question.get("id") or str(question.get("_id")) 

        # 2. Lấy tất cả 'detailedResults' liên quan
        pipeline = [
            {"$match": {"detailedResults.questionId": q_id_str}},
            {"$unwind": "$detailedResults"},
            {"$match": {"detailedResults.questionId": q_id_str}},
            {"$project": {"answer": "$detailedResults.studentAnswer"}}
        ]
        results = list(db.results.aggregate(pipeline))
        all_answers = [r.get("answer") for r in results]

        analysis_data = {}

        # 3. Phân tích dựa trên loại câu hỏi
        if q_type == "mc":
            labels = []
            correct_answer_text = ""
            for opt in question.get("options", []):
                text = opt.get("text")
                labels.append(text)
                if opt.get("correct"):
                    correct_answer_text = text
            
            data_map = {}
            for ans in all_answers:
                # Xử lý cả trường hợp 'None' (bỏ trống)
                ans_str = str(ans) if ans is not None else "[Bỏ trống]"
                data_map[ans_str] = data_map.get(ans_str, 0) + 1
            
            final_data = [data_map.get(label, 0) for label in labels]
            # Thêm "Bỏ trống" nếu có
            if "[Bỏ trống]" in data_map and "[Bỏ trống]" not in labels:
                labels.append("[Bỏ trống]")
                final_data.append(data_map["[Bỏ trống]"])
                
            analysis_data = {
                "labels": labels,
                "data": final_data,
                "correctAnswer": correct_answer_text
            }

        elif q_type == "true_false":
            # Labels là các mệnh đề
            labels = [opt.get("text", f"Mệnh đề {i+1}") for i, opt in enumerate(question.get("options", []))]
            # Đáp án đúng là [true, false, true, ...]
            correct_answers = [opt.get("correct") for opt in question.get("options", [])]
            num_items = len(labels)
            
            chose_true = [0] * num_items
            chose_false = [0] * num_items
            chose_null = [0] * num_items
            
            for ans_array in all_answers:
                if isinstance(ans_array, list):
                    for i in range(num_items):
                        if i < len(ans_array):
                            student_choice = ans_array[i]
                            if student_choice is True:
                                chose_true[i] += 1
                            elif student_choice is False:
                                chose_false[i] += 1
                            else:
                                chose_null[i] += 1 # Bỏ trống (null)
                        else:
                            chose_null[i] += 1 # Bỏ trống (mảng ngắn hơn)
                else:
                    # Học sinh bỏ trống toàn bộ câu (studentAnswer = null)
                    for i in range(num_items):
                        chose_null[i] += 1

            analysis_data = {
                "labels": labels,
                "choseTrue": chose_true,
                "choseFalse": chose_false,
                "choseNull": chose_null,
                "correct": correct_answers
            }

        elif q_type == "fill_blank":
            # Đáp án đúng là 1 mảng các string
            correct_answers = [opt.get("text") for opt in question.get("options", [])]
            num_blanks = len(correct_answers)
            analysis_data = [] # Đây sẽ là 1 mảng các object

            for i in range(num_blanks):
                blank_analysis = {
                    "blankIndex": i,
                    "label": f"Ô trống {i+1}",
                    "correct": correct_answers[i],
                    "answers": {} # {"mái": 10, "trống": 2}
                }
                
                # Đếm tần suất
                for ans_array in all_answers:
                    ans_text = "" # Dùng '' để đại diện cho [Bỏ trống]
                    if isinstance(ans_array, list) and i < len(ans_array) and ans_array[i] is not None:
                        ans_text = str(ans_array[i]).strip()
                    
                    blank_analysis["answers"][ans_text] = blank_analysis["answers"].get(ans_text, 0) + 1
                
                analysis_data.append(blank_analysis)
        
        else:
            return jsonify({"message": "Loại câu hỏi này không hỗ trợ phân tích"}), 400

        return jsonify({
            "success": True,
            "questionId": q_id_str,
            "questionText": q_text,
            "type": q_type,
            "data": analysis_data
        }), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({"message": f"Lỗi server: {str(e)}"}), 500


# ... (Các hàm /questions... (GET, POST, PUT, DELETE, image) giữ nguyên) ...
@app.route("/questions/image/<file_id>", methods=["GET"])
def get_question_image(file_id):
    try:
        file_obj = fs.get(ObjectId(file_id))
        return send_file(file_obj, mimetype=file_obj.content_type, as_attachment=False)
    except Exception as e:
        print("❌ Lỗi lấy ảnh:", e)
        return jsonify({"message": f"File not found: {str(e)}"}), 404


@app.route("/api/results/test-stats/<test_id>", methods=["GET"])
def get_test_stats_for_class(test_id):
    try:
        # Lấy className của học sinh (nếu cần lọc theo lớp)
        # Tạm thời chúng ta sẽ tính trên toàn bộ bài thi
        # student_class = request.args.get("className")
        # match_query = {"testId": test_id, "className": student_class}

        match_query = {"testId": test_id}

        pipeline = [
            {"$match": match_query},
            {"$group": {
                "_id": "$testId",
                "avgScore": {"$avg": "$totalScore"},
                "maxScore": {"$max": "$totalScore"},
                "minScore": {"$min": "$totalScore"},
                "count": {"$sum": 1}
            }}
        ]

        stats = list(db.results.aggregate(pipeline))

        if not stats:
            return jsonify({"message": "Không có dữ liệu thống kê"}), 404

        return jsonify(stats[0]), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({"message": f"Lỗi server: {str(e)}"}), 500


@app.route("/questions", methods=["GET"])
@app.route("/api/questions", methods=["GET"])
def list_questions():
    query = {}
    subject = request.args.get("subject")
    level = request.args.get("level")
    q_type = request.args.get("type") 
    difficulty = request.args.get("difficulty")
    search_keyword = request.args.get("search") 
    
    # ✅ MỚI: Thêm logic lọc theo Tag
    tag_filter = request.args.get("tag")
    
    if subject: query["subject"] = subject
    if level: query["level"] = level
    if q_type: query["type"] = q_type
    if difficulty: query["difficulty"] = difficulty
    if search_keyword:
        query["q"] = {"$regex": search_keyword, "$options": "i"} 
    
    # ✅ MỚI: Thêm query cho tag
    if tag_filter:
        # $in tìm bất kỳ câu hỏi nào có tag này trong mảng 'tags'
        query["tags"] = {"$in": [tag_filter.strip()]}

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
    docs = list(db.questions.find(query).sort("createdAt", DESCENDING))
    for doc in docs:
        # Thêm cờ 'isAssigned' vào tài liệu
        q_uuid = doc.get("id")
        doc['isAssigned'] = (q_uuid in assigned_q_ids)
        doc['_id'] = str(doc['_id'])
        
    return jsonify(docs)


@app.route("/api/questions/bulk-upload", methods=["POST"])
def bulk_upload_questions():
    if 'file' not in request.files:
        return jsonify({"success": False, "message": "Không tìm thấy file"}), 400
    
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({"success": False, "message": "Không có file nào được chọn"}), 400

    try:
        if file.filename.endswith('.xlsx'):
            df = pd.read_excel(file, engine='openpyxl')
        elif file.filename.endswith('.csv'):
            df = pd.read_csv(file)
        else:
            return jsonify({"success": False, "message": "Định dạng file không hợp lệ. Chỉ chấp nhận .xlsx hoặc .csv"}), 400
        
        # Làm sạch tên cột: loại bỏ khoảng trắng, chuyển về chữ thường
        df.columns = df.columns.str.strip().str.lower()
        
        # Kiểm tra các cột bắt buộc
        required_cols = ['q', 'subject', 'level', 'answer']
        for col in required_cols:
            if col not in df.columns:
                return jsonify({"success": False, "message": f"File bị thiếu cột bắt buộc: '{col}'"}), 400

        questions_to_insert = []
        errors = []
        
        # Chuẩn hóa giá trị NaN (ô trống trong Excel) thành None
        df = df.where(pd.notnull(df), None)

        for index, row in df.iterrows():
            try:
                # 1. Lấy các trường bắt buộc
                q_text = str(row['q'])
                subject = str(row['subject']).lower()
                level = str(row['level'])
                
                if not q_text or not subject or not level:
                    errors.append(f"Dòng {index + 2}: Thiếu 'q', 'subject' hoặc 'level'.")
                    continue

                # 2. Lấy các trường tùy chọn (có giá trị mặc định)
                difficulty = str(row.get('difficulty', 'medium')).lower()
                q_type = str(row.get('type', 'mc')).lower()
                
                if difficulty not in ['easy', 'medium', 'hard']:
                    difficulty = 'medium'
                if q_type not in ['mc', 'essay']:
                    q_type = 'mc'

                newq = {
                    "id": str(uuid4()),
                    "q": q_text,
                    "type": q_type,
                    "points": 1, # Mặc định 1 điểm
                    "subject": subject,
                    "level": level,
                    "difficulty": difficulty,
                    "createdAt": now_vn_iso(),
                    "imageId": None,
                    "options": [],
                    "answer": ""
                }

                # 3. Xử lý câu hỏi Trắc nghiệm (mc)
                if q_type == 'mc':
                    options = []
                    # Lấy các cột option_1, option_2, ...
                    option_cols = sorted([col for col in df.columns if col.startswith('option_')])
                    
                    for col_name in option_cols:
                        option_text = row.get(col_name)
                        if option_text and str(option_text).strip():
                            options.append(str(option_text).strip())
                    
                    if not options:
                        errors.append(f"Dòng {index + 2}: Câu trắc nghiệm nhưng không có cột 'option_...'.")
                        continue
                        
                    # Xử lý đáp án đúng
                    answer_val = row.get('answer')
                    if answer_val is None:
                        errors.append(f"Dòng {index + 2}: Câu trắc nghiệm thiếu cột 'answer' (chỉ số đáp án đúng, ví dụ: 1, 2, 3...).")
                        continue
                    
                    try:
                        # Chuyển đáp án (ví dụ: '1') thành index (0)
                        answer_index = int(float(answer_val)) - 1
                    except ValueError:
                        errors.append(f"Dòng {index + 2}: Cột 'answer' ({answer_val}) không phải là một con số hợp lệ.")
                        continue
                    
                    if not (0 <= answer_index < len(options)):
                        errors.append(f"Dòng {index + 2}: 'answer' ({answer_val}) nằm ngoài số lượng options ({len(options)}).")
                        continue

                    # Tạo cấu trúc options object
                    newq["options"] = [
                        {"text": text, "correct": (i == answer_index)}
                        for i, text in enumerate(options)
                    ]
                    # 'answer' của MC để trống (vì đã lưu trong 'options')
                    newq["answer"] = ""

                # 4. Xử lý câu hỏi Tự luận (essay)
                else:
                    newq["options"] = []
                    newq["answer"] = str(row.get('answer', '')) # 'answer' là văn bản mẫu

                questions_to_insert.append(newq)

            except Exception as e:
                errors.append(f"Dòng {index + 2}: Lỗi xử lý - {str(e)}")

        # 5. Thêm vào DB
        if questions_to_insert:
            db.questions.insert_many(questions_to_insert)
            
        return jsonify({
            "success": True,
            "message": f"Hoàn tất! Đã thêm thành công {len(questions_to_insert)} câu hỏi.",
            "errors": errors,
            "error_count": len(errors)
        }), 201

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": f"Lỗi nghiêm trọng khi đọc file: {str(e)}"}), 500


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
    
    # ✅ MỚI: Xử lý Tags
    tags_raw = data.get("tags", "") # Lấy chuỗi "tag1, tag2, tag3"
    # Xử lý chuỗi thành mảng các tag sạch
    tags_list = [tag.strip() for tag in tags_raw.split(',') if tag.strip()]
    # Xóa trùng lặp
    tags_list = list(dict.fromkeys(tags_list)) 
    hint = data.get("hint", "") # <-- THÊM DÒNG NÀY

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
        "imageId": str(image_id) if image_id else None,
        "createdAt": now_vn_iso(),
        "tags": tags_list, # ✅ MỚI: Thêm trường tags vào CSDL
        "hint": hint # <-- THÊM DÒNG NÀY
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
    
    # ✅ MỚI: Xử lý Tags
    tags_raw = data.get("tags", "") # Lấy chuỗi "tag1, tag2, tag3"
    tags_list = [tag.strip() for tag in tags_raw.split(',') if tag.strip()]
    tags_list = list(dict.fromkeys(tags_list)) 
    hint = data.get("hint", "") # <-- THÊM DÒNG NÀY

    update_fields = {
        "q": data.get("q"),
        "type": data.get("type"),
        "points": int(data.get("points", 1)),
        "subject": data.get("subject"),
        "level": data.get("level"),
        "difficulty": data.get("difficulty", "medium"),
        "options": options,
        "answer": answer,
        "imageId": image_id,
        "tags": tags_list, # ✅ MỚI: Thêm trường tags vào CSDL
        "hint": hint # <-- THÊM DÒNG NÀY
       
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

            # ✅ BẮT ĐẦU LOGIC XÁO TRỘN ĐÁP ÁN
            # Tự động xáo trộn các câu trắc nghiệm khi tải
            q_type = q_full.get("type", "mc").lower()
            if q_type == "mc":
                options_list = q_full.get("options", [])
                if options_list and len(options_list) > 0:
                    # Xáo trộn 'options' ngay trên bản sao
                    random.shuffle(q_full["options"]) 
            # ✅ KẾT THÚC LOGIC XÁO TRỘN
            
            # ✅ GÁN ĐIỂM ĐÃ TÍNH (TỪ 5 QUY TẮC) VÀO
            q_full["points"] = points_map.get(qid, 1.0)
            
            final_questions.append(q_full)
        else:
            app.logger.warning(f"Question id {qid} not found in questions collection. Adding placeholder.")
            final_questions.append({
                "id": qid,
                "_id": qid,
                "q": f"[LỖI: KHÔNG TÌM THẤY CÂU HỎI ID: {qid}] <br> <i>(Câu hỏi này có thể đã bị xóa khỏi ngân hàng đề.)</i>",
                "type": "essay", # Hiển thị như một câu tự luận
                "points": points_map.get(qid, 0.0), # Lấy điểm gốc (nếu có)
                "options": [],
                "answer": "",
                "isMissing": True # Thêm cờ để JS có thể nhận biết (nếu cần)
            })

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
            
    # 2. GỌI HÀM TÍNH ĐIỂM
    points_map = calculate_question_points(question_uuids_to_save, db)

    # 3. Định dạng lại mảng câu hỏi để lưu vào DB
    formatted_questions = []
    # ======== SỬA ĐỔI TẠI ĐÂY ========
    mc_count, essay_count, tf_count, fill_count, draw_count = calculate_question_counts(question_uuids_to_save, db)
    # ===============================

    for q_id in question_uuids_to_save: 
        points = points_map.get(q_id, 0)
        formatted_questions.append({
            "id": q_id,      
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
        # ======== SỬA ĐỔI TẠI ĐÂY ========
        "mcCount": mc_count,
        "essayCount": essay_count,
        "tfCount": tf_count,
        "fillCount": fill_count,
        "drawCount": draw_count, # <-- THÊM DÒNG NÀY
        # ===============================
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
            {"$project": {"id": 1, "_id": 1, "type": 1}} # Lấy "type" để đếm
        ]
        return list(db.questions.aggregate(pipeline))

    easy_questions = pick("easy", num_easy)
    medium_questions = pick("medium", num_medium)
    hard_questions = pick("hard", num_hard)
    
    all_questions = easy_questions + medium_questions + hard_questions
    
    all_question_ids = [q.get('id') or str(q.get('_id')) for q in all_questions]
    
    if not all_question_ids:
         return jsonify({"success": False, "message": "Không tìm thấy câu hỏi nào phù hợp"}), 404

    # 1. GỌI HÀM TÍNH ĐIỂM
    points_map = calculate_question_points(all_question_ids, db)

    # 2. Định dạng mảng câu hỏi và đếm type
    formatted_questions = []
    # ======== SỬA ĐỔI TẠI ĐÂY ========
    mc_count = 0
    essay_count = 0
    tf_count = 0
    fill_count = 0
    
    for q in all_questions:
        q_id = q.get('id') or str(q.get('_id'))
        points = points_map.get(q_id, 0)
        formatted_questions.append({
            "id": q_id,
            "points": points
        })
        
        q_type = q.get('type')
        if q_type == 'essay':
            essay_count += 1
        elif q_type == 'draw': # <-- THÊM DÒNG NÀY
            draw_count += 1 # <-- SỬA TỪ essay_count 
        elif q_type == 'true_false':
            tf_count += 1
        elif q_type == 'fill_blank':
            fill_count += 1
        else: # Mặc định là MC
            mc_count += 1
    # ===============================
            
    # 3. Tạo tài liệu Test mới
    new_test = {
        "id": str(uuid4()),
        "name": name,
        "time": time,
        "subject": subject,
        "level": level,
        "questions": formatted_questions,
        "isAutoGenerated": True,
        "createdAt": now_vn_iso(),
        # ======== SỬA ĐỔI TẠI ĐÂY ========
        "mcCount": mc_count,
        "essayCount": essay_count,
        "tfCount": tf_count,
        "fillCount": fill_count,
        "drawCount": draw_count, # <-- THÊM DÒNG NÀY
        # ===============================
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
    
    if db.assignments.find_one({"testId": test_id}):
        return jsonify({"success": False, "message": "Đề thi đã được giao, không sửa được đề."}), 403 

    data = request.get_json() or {}
    
    name = data.get("name")
    time = data.get("time")
    level = data.get("level")
    subject = data.get("subject")
    
    questions_from_js = data.get("questions", [])
    
    question_oids_from_fe = [q.get('_id') for q in questions_from_js if q.get('_id')]

    if not subject:
        return jsonify({"success": False, "message": "Vui lòng chọn Môn học"}), 400
    if not question_oids_from_fe:
        return jsonify({"success": False, "message": "Vui lòng chọn ít nhất 1 câu hỏi"}), 400

    # CHUYỂN ĐỔI _id SANG id (UUID)
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
    # ======== SỬA ĐỔI TẠI ĐÂY ========
    mc_count, essay_count, tf_count, fill_count, draw_count = calculate_question_counts(question_uuids_to_save, db)
    # ===============================

    for q_id in question_uuids_to_save:
        points = points_map.get(q_id, 0)
        formatted_questions.append({
            "id": q_id,         
            "points": points
        })
            
    # 4. Tạo đối tượng $set
    update_data = {
        "name": name,
        "time": time,
        "level": level,
        "subject": subject,
        "questions": formatted_questions,
        # ======== SỬA ĐỔI TẠI ĐÂY ========
        "mcCount": mc_count,
        "essayCount": essay_count,
        "tfCount": tf_count,
        "fillCount": fill_count,
        "drawCount": draw_count, # <-- THÊM DÒNG NÀY
        # ===============================
        "count": len(question_uuids_to_save)
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

@app.route("/api/tests/<test_id>/status", methods=["PUT"])
def update_test_status(test_id):
    """
    API mới: Cập nhật trạng thái của một bài thi (ví dụ: 'assigned' hoặc 'not_assigned')
    """
    data = request.get_json() or {}
    new_status = data.get("status")
    
    if not new_status:
        return jsonify({"success": False, "message": "Missing status"}), 400

    result = db.tests.update_one(
        {"id": test_id},
        {"$set": {"assignmentStatus": new_status}}
    )
    
    if result.matched_count == 0:
        return jsonify({"success": False, "message": "Test not found"}), 404
        
    return jsonify({"success": True, "message": "Status updated"}), 200


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
                           {"_id": 0, "id": 1, "name": 1, "subject": 1, "time": 1, "mcCount": 1, "essayCount": 1, "tfCount": 1, "fillCount": 1, "drawCount": 1})
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
            "tfCount": test_info.get("tfCount", 0),     # <-- THÊM DÒNG NÀY
            "fillCount": test_info.get("fillCount", 0), # <-- THÊM DÒNG NÀY
            "drawCount": test_info.get("drawCount", 0), # <-- THÊM MỚI
            "deadline": a.get("deadline"),
            "assignedAt": assigned_date,
            "status": a.get("status", "pending"),
        })
    return jsonify({"success": True, "assignments": result_list})

# ==================================================
# ✅ THAY THẾ HÀM NỘP BÀI (Dòng 1450)
# ==================================================
@app.route("/results", methods=["POST"])
@app.route("/api/results", methods=["POST"])
def create_result():
    try:
        data = request.get_json() or {}
        student_id = data.get("studentId")
        assignment_id = data.get("assignmentId")
        test_id = data.get("testId")
        student_answers_payload = data.get("studentAnswers", []) 

        if not student_id or not assignment_id or not test_id:
            return jsonify({"message": "Thiếu ID (studentId, assignmentId, testId)"}), 400

        # 1. Lấy thông tin Test
        test_doc = db.tests.find_one({"id": test_id})
        if not test_doc:
            return jsonify({"message": "Không tìm thấy đề thi"}), 404

        test_questions = test_doc.get("questions", []) or []
        
        # 2. Xử lý cả 2 định dạng Đề thi
        points_map = {}
        question_ids_in_test = []
        
        if test_questions and isinstance(test_questions[0], dict):
            # ĐỊNH DẠNG MỚI: [{'id': ..., 'points': ...}]
            try:
                points_map = {q.get('id'): q.get('points', 1) for q in test_questions}
                question_ids_in_test = list(points_map.keys())
            except AttributeError as e:
                print(f"Lỗi khi xử lý points_map định dạng mới: {e}")
                return jsonify({"message": "Lỗi định dạng đề thi (questions không hợp lệ)."}), 500
        
        elif test_questions and isinstance(test_questions[0], str):
            # ĐỊNH DẠNG CŨ: ["id1", "id2", ...]
            print(f"Cảnh báo: Đề thi {test_id} dùng logic điểm cũ. Đang tính toán lại...")
            question_ids_in_test = [str(q) for q in test_questions]
            points_map = calculate_question_points(question_ids_in_test, db) 
        
        elif not test_questions:
             return jsonify({"message": "Đề thi không có câu hỏi."}), 400

        # 3. Lấy TOÀN BỘ đối tượng câu hỏi
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
        
        correct_questions_cursor = []
        if or_clauses:
             correct_questions_cursor = list(db.questions.find({"$or": or_clauses}))

        full_question_map = {}
        has_manual_grade = False # <-- SỬA TÊN BIẾN
        
        for q in correct_questions_cursor:
            q_id_uuid = q.get("id")
            q_id_obj_str = str(q.get("_id"))
            q_type = q.get("type", "mc")
            
            if q_type == "essay" or q_type == "draw":
                has_manual_grade = True # <-- SỬA TÊN BIẾN
            
            if q_id_uuid: full_question_map[q_id_uuid] = q
            if q_id_obj_str: full_question_map[q_id_obj_str] = q

        # 4. Tạo map câu trả lời của học sinh
        student_ans_map = {}
        for ans in student_answers_payload: 
            if not isinstance(ans, dict): continue
            qkey = ans.get("questionId") 
            if qkey:
                student_ans_map[str(qkey)] = ans.get("answer") 

        # ▼▼▼ KHỐI TÍNH ĐIỂM MỚI ▼▼▼
        mc_score = 0.0
        tf_score = 0.0
        fill_score = 0.0
        essay_score = 0.0 # Sẽ là 0
        draw_score = 0.0 # Sẽ là 0
        detailed_results = []
        # ▲▲▲ KẾT THÚC KHỐI MỚI ▲▲▲

        def norm_str(x):
            if x is None: return ""
            return str(x).strip().lower()

        # 5. LẶP VÀ TÍNH ĐIỂM
        for q_id in question_ids_in_test: 
            question_obj = full_question_map.get(q_id)
            if not question_obj:
                print(f"Cảnh báo: Không tìm thấy question_obj cho q_id {q_id}")
                continue 

            q_type = question_obj.get("type", "mc")
            max_points = float(points_map.get(q_id, 1)) 
            student_ans_value = student_ans_map.get(q_id, None) 

            is_correct = None
            points_gained = 0.0
            correct_answer_for_storage = None 
            correct_items_count_for_storage = None
            total_items_for_storage = None

            if q_type == "mc":
                correct_ans_text = next((opt.get("text") for opt in question_obj.get("options", []) if opt.get("correct")), None)
                correct_answer_for_storage = correct_ans_text 
                is_correct = (student_ans_value is not None) and \
                             (correct_ans_text is not None) and \
                             (norm_str(student_ans_value) == norm_str(correct_ans_text))
                
                total_items_for_storage = 1 
                if is_correct:
                    points_gained = max_points
                    correct_items_count_for_storage = 1 
                else:
                    correct_items_count_for_storage = 0 
                    
                mc_score += points_gained # <-- SỬA: Gán vào mc_score

            elif q_type == "true_false":
                correct_answers_list = [opt.get("correct") for opt in question_obj.get("options", [])]
                correct_answer_for_storage = correct_answers_list
                student_answers_list = student_ans_value if isinstance(student_ans_value, list) else []
                
                num_items = len(correct_answers_list)
                total_items_for_storage = num_items
                correct_items_count = 0
                
                if num_items == 0:
                    is_correct = False
                    points_gained = 0
                else:
                    points_per_item = max_points / num_items
                    
                    for i in range(num_items):
                        student_ans = None
                        if i < len(student_answers_list):
                            student_ans = student_answers_list[i]
                        
                        if student_ans is not None and student_ans == correct_answers_list[i]:
                            correct_items_count += 1
                            
                    points_gained = correct_items_count * points_per_item
                
                correct_items_count_for_storage = correct_items_count

                if points_gained == max_points:
                    is_correct = True
                elif points_gained > 0:
                    is_correct = None 
                else:
                    is_correct = False
                    
                tf_score += points_gained # <-- SỬA: Gán vào tf_score
            
            elif q_type == "fill_blank":
                correct_options = question_obj.get("options", [])
                correct_answers_list = [norm_str(opt.get("text")) for opt in correct_options]
                correct_answer_for_storage = [opt.get("text") for opt in correct_options] 
                student_answers_list = student_ans_value if isinstance(student_ans_value, list) else []
                
                num_blanks = len(correct_answers_list)
                total_items_for_storage = num_blanks
                correct_blanks_count = 0
                
                if num_blanks == 0:
                    is_correct = False
                    points_gained = 0
                else:
                    points_per_blank = max_points / num_blanks 
                    for i in range(num_blanks):
                        student_ans_norm = ""
                        if i < len(student_answers_list) and student_answers_list[i]:
                            student_ans_norm = norm_str(student_answers_list[i])
                        if student_ans_norm == correct_answers_list[i]:
                            correct_blanks_count += 1
                    points_gained = correct_blanks_count * points_per_blank
                
                correct_items_count_for_storage = correct_blanks_count
                
                if points_gained == max_points:
                    is_correct = True
                elif points_gained > 0:
                    is_correct = None 
                else:
                    is_correct = False
                
                fill_score += points_gained # <-- SỬA: Gán vào fill_score
            
            elif q_type == "essay":
                is_correct = None 
                correct_answer_for_storage = question_obj.get("answer") 

            elif q_type == "draw":
                is_correct = None
                correct_answer_for_storage = question_obj.get("answer")

            detailed_results.append({
                "questionId": q_id,
                "studentAnswer": student_ans_value, 
                "correctAnswer": correct_answer_for_storage, 
                "maxPoints": max_points,
                "pointsGained": round(points_gained, 2),
                "isCorrect": is_correct,
                "type": q_type,
                "teacherScore": None,
                "teacherNote": "",
                "correctItems": correct_items_count_for_storage,
                "totalItems": total_items_for_storage
            })

        # 6. Xác định trạng thái chấm
        grading_status = "Đang Chấm" if has_manual_grade else "Hoàn tất" # <-- SỬA TÊN BIẾN
        result_id = str(uuid4())
        total_score = round(mc_score + tf_score + fill_score, 2) # <-- SỬA TỔNG ĐIỂM

        # 7. Lấy thông tin user
        user_info = db.users.find_one({"id": student_id}) or {}

        # ▼▼▼ SỬA KHỐI TẠO new_result ▼▼▼
        new_result = {
            "id": result_id,
            "studentId": student_id,
            "assignmentId": assignment_id,
            "testId": test_id,
            "studentName": user_info.get("fullName", user_info.get("user")),
            "className": user_info.get("className"),
            "testName": test_doc.get("name"),
            "studentAnswers": student_answers_payload, 
            "detailedResults": detailed_results,
            "gradingStatus": grading_status, 
            "mcScore": round(mc_score, 2), 
            "tfScore": round(tf_score, 2),
            "fillScore": round(fill_score, 2),
            "essayScore": 0.0,
            "drawScore": 0.0,
            "totalScore": total_score,
            "submittedAt": now_vn_iso(),
            "gradedAt": None
        }
        # ▲▲▲ KẾT THÚC SỬA ▲▲▲
        
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
        
        new_result.pop("_id", None) 
        return jsonify(new_result), 201

    except Exception as e:
        print("create_result error:", e)
        traceback.print_exc()
        return jsonify({"message": f"Server error: {str(e)}"}), 500
        
# ==================================================
# ✅ THAY THẾ HÀM CHẤM ĐIỂM (Khoảng dòng 1792)
# ==================================================
@app.route("/api/results/<result_id>/grade", methods=["POST"])
def grade_result(result_id):
    try:
        data = request.get_json() or {}
        essays_payload = [e for e in data.get("essays", []) if isinstance(e, dict)] 

        print(f"--- [BE LOG 1] grade_result cho {result_id} ---")
        print(f"Payload thô nhận được (chỉ 'essays'): {essays_payload}")
        
        # === 1. Lấy bài làm (Result) ===
        result = db.results.find_one({"id": result_id})
        if not result:
            return jsonify({"error": "Không tìm thấy bài làm"}), 404

        current_regrade = result.get("regradeCount", 0)
        detailed_list = result.get("detailedResults", []) 
        
        # === 2. LẤY BÀI THI GỐC ... ===
        test_id = result.get("testId")
        test_doc = db.tests.find_one({"id": test_id})
        if not test_doc:
            return jsonify({"error": f"Không tìm thấy bài thi gốc (ID: {test_id})."}), 404
        
        points_map = {q.get('id') or str(q.get('_id')): q.get('points', 1) for q in test_doc.get('questions', [])}

        # ▼▼▼ SỬA KHỐI TÍNH ĐIỂM ▼▼▼
        # === 3. LẤY ĐIỂM TỰ ĐỘNG (Đã có) ===
        new_mc_score = result.get("mcScore", 0.0) 
        new_tf_score = result.get("tfScore", 0.0)
        new_fill_score = result.get("fillScore", 0.0)
        
        # === 4. TÍNH ĐIỂM CHẤM TAY (MỚI) ===
        new_essay_score = 0.0
        new_draw_score = 0.0
        has_ungraded_manual = False # Sửa tên biến
        # ▲▲▲ KẾT THÚC SỬA ▲▲▲

        payload_map = { str(e.get("questionId")): e for e in essays_payload if e.get("questionId") }

        for i in range(len(detailed_list)):
            
            q_id_str = str(detailed_list[i].get("questionId"))
            q_type = detailed_list[i].get("type")
            
            # Chỉ xử lý 2 loại chấm tay
            if q_type == "essay" or q_type == "draw":
            
                essay_data = payload_map.get(q_id_str)
                max_points = float(points_map.get(q_id_str, 1.0)) 
                
                if essay_data:
                    teacher_provided_score = essay_data.get("teacherScore")
                    teacher_provided_note = essay_data.get("teacherNote")
                    teacher_provided_drawing = essay_data.get("teacherDrawing") 

                    score_was_provided = (teacher_provided_score is not None)
                    
                    if score_was_provided:
                        ts_float = 0.0
                        try: ts_float = float(teacher_provided_score)
                        except: ts_float = 0.0
                        if ts_float > max_points: ts_float = max_points 
                        if ts_float < 0: ts_float = 0.0
                        
                        detailed_list[i]["teacherScore"] = ts_float
                        detailed_list[i]["pointsGained"] = ts_float
                        detailed_list[i]["isCorrect"] = ts_float > 0
                        
                        # ▼▼▼ PHÂN LOẠI ĐIỂM CHẤM TAY ▼▼▼
                        if q_type == "essay":
                            new_essay_score += ts_float
                        elif q_type == "draw":
                            new_draw_score += ts_float
                        # ▲▲▲ KẾT THÚC PHÂN LOẠI ▲▲▲

                    else:
                        # (Code xử lý 'has_ungraded_manual' giữ nguyên)
                        has_old_score = (detailed_list[i].get("teacherScore") is not None)
                        has_new_note = (teacher_provided_note is not None)
                        has_new_drawing = (teacher_provided_drawing is not None) 

                        if has_old_score:
                            # Lấy lại điểm cũ nếu GV không nhập điểm mới
                            old_points_gained = float(detailed_list[i].get("pointsGained", 0.0))
                            if q_type == "essay":
                                new_essay_score += old_points_gained
                            elif q_type == "draw":
                                new_draw_score += old_points_gained
                        elif has_new_note or has_new_drawing:
                            # Nếu GV chỉ ghi chú/vẽ mà không chấm -> 0 điểm
                            detailed_list[i]["teacherScore"] = 0.0
                            detailed_list[i]["pointsGained"] = 0.0
                            detailed_list[i]["isCorrect"] = False
                        else:
                            has_ungraded_manual = True # Đánh dấu chờ chấm

                    if "teacherNote" in essay_data:
                        detailed_list[i]["teacherNote"] = teacher_provided_note

                    if q_type == "draw":
                        if "teacherDrawing" in essay_data and teacher_provided_drawing is not None:
                            print(f"[BE LOG 2] Đang lưu teacherDrawing cho câu {q_id_str}.")
                            detailed_list[i]["teacherDrawing"] = teacher_provided_drawing
                        else:
                            print(f"[BE LOG 2] BỎ QUA lưu teacherDrawing cho câu {q_id_str}.")
            
                else: # (Không có trong payload)
                    if detailed_list[i].get("teacherScore") is None:
                        has_ungraded_manual = True
                    else:
                        # Lấy điểm cũ
                        old_points_gained = float(detailed_list[i].get("pointsGained", 0.0))
                        if q_type == "essay":
                            new_essay_score += old_points_gained
                        elif q_type == "draw":
                            new_draw_score += old_points_gained
        
        # === 5. Tính điểm tổng và xác định trạng thái ===
        new_total_score = new_mc_score + new_tf_score + new_fill_score + new_essay_score + new_draw_score
        graded_at = now_vn_iso()
        
        if has_ungraded_manual: # Sửa tên biến
             new_status = "Đang Chấm"
        elif current_regrade + 1 >= 2:
            new_status = "Hoàn tất" 
        else:
            new_status = "Đã Chấm" 

        # ▼▼▼ SỬA KHỐI CẬP NHẬT DB ▼▼▼
        update_payload = {
            "detailedResults": detailed_list, 
            "totalScore": round(new_total_score, 2),
            "mcScore": round(new_mc_score, 2), 
            "tfScore": round(new_tf_score, 2),
            "fillScore": round(new_fill_score, 2),
            "essayScore": round(new_essay_score, 2), 
            "drawScore": round(new_draw_score, 2),
            "gradingStatus": new_status,
            "gradedAt": graded_at,
        }
        # ▲▲▲ KẾT THÚC SỬA ▲▲▲

        print(f"[BE LOG 3] Chuẩn bị update MongoDB. Status: {new_status}, EssayScore: {new_essay_score}, DrawScore: {new_draw_score}")
        # ... (Log 4 giữ nguyên) ...

        db.results.update_one(
            {"id": result_id},
            {
                "$set": update_payload,
                "$inc": { "regradeCount": 1 } 
            }
        )
        
        # ... (Code trả về 'updated_document' giữ nguyên) ...
        
        updated_document = db.results.find_one({"id": result_id})
        if not updated_document:
            return jsonify({"success": False, "message": "Lỗi: Không tìm thấy bài làm sau khi cập nhật."}), 500
        updated_document.pop("_id", None)
        
        test_info = db.tests.find_one({"id": updated_document.get("testId")}, {"_id": 0, "name": 1, "subject": 1}) or {}
        student_info = db.users.find_one({"id": updated_document.get("studentId")}, {"_id": 0, "fullName": 1, "className": 1}) or {}
        
        updated_document["testName"] = updated_document.get("testName") or test_info.get("name", "Bài thi đã xóa")
        updated_document["subject"] = updated_document.get("subject") or test_info.get("subject", "khác")
        updated_document["studentName"] = updated_document.get("studentName") or student_info.get("fullName", "N/A")
        updated_document["className"] = updated_document.get("className") or student_info.get("className", "N/A")

        print(f"[BE LOG 5] Trả về tài liệu đã cập nhật.")

        return jsonify(updated_document), 200

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
            "tfScore": {"$ifNull": ["$tfScore", 0.0]},       # <-- THÊM MỚI
            "fillScore": {"$ifNull": ["$fillScore", 0.0]},   # <-- THÊM MỚI
            "essayScore": {"$ifNull": ["$essayScore", 0.0]},
            "drawScore": {"$ifNull": ["$drawScore", 0.0]},   # <-- THÊM DÒNG NÀY
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
        doc["tfScore"] = round(doc.get("tfScore", 0.0), 2)     # <-- THÊM MỚI
        doc["fillScore"] = round(doc.get("fillScore", 0.0), 2) # <-- THÊM MỚI
        doc["mcScore"] = round(doc.get("mcScore", 0.0), 2)
        doc["essayScore"] = round(doc.get("essayScore", 0.0), 2)
        doc["drawScore"] = round(doc.get("drawScore", 0.0), 2) # <-- THÊM MỚI
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
                "gradingStatus": 1, "totalScore": 1, "mcScore": 1, "essayScore": 1, "tfScore": 1, "fillScore": 1, "drawScore": 1,
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
                "tfScore": 1,     # <-- THÊM MỚI
                "fillScore": 1, # <-- THÊM MỚI
                "drawScore": 1, # <-- THÊM MỚI
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

@app.route("/api/results/bulk", methods=["POST"])
def get_bulk_results_detail():
    """
    API mới: Lấy chi tiết nhiều bài kết quả (results) để in hàng loạt.
    """
    try:
        data = request.get_json() or {}
        result_ids = data.get("result_ids", [])
        if not result_ids:
            return jsonify({"message": "Thiếu result_ids"}), 400

        # Sử dụng aggregation pipeline tương tự như get_result_detail
        # nhưng dùng $match với $in
        pipeline = [
            {"$match": {"id": {"$in": result_ids}}}, # Lọc theo danh sách ID
            {"$lookup": {"from": "tests", "localField": "testId", "foreignField": "id", "as": "test_info"}},
            {"$unwind": {"path": "$test_info", "preserveNullAndEmptyArrays": True}},
            {
                "$lookup": {
                    "from": "users", "let": { "sid": "$studentId" }, 
                    "pipeline": [
                        { "$match": { "$expr": { "$or": [ { "$eq": [ "$id", "$$sid" ] }, { "$eq": [ { "$toString": "$_id" }, "$$sid" ] } ] }}},
                        { "$project": { "fullName": 1, "className": 1, "_id": 0 } }
                    ], "as": "student_info"
                }
            },
            {"$unwind": {"path": "$student_info", "preserveNullAndEmptyArrays": True}},
            {
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
            }
        ]
        results = list(db.results.aggregate(pipeline))
        
        if not results:
            return jsonify({"message": "Không tìm thấy kết quả nào"}), 404
        
        return jsonify(results) # Trả về mảng các kết quả chi tiết
        
    except Exception as e:
        print(f"Lỗi khi lấy chi tiết bulk result: {e}")
        return jsonify({"message": f"Server error: {e}"}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
