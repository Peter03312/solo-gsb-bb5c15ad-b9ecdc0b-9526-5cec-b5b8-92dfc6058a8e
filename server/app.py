"""Flask 后端：原稿查询、修补预览、预览确认。"""

import json
import os
import uuid

from flask import Flask, jsonify, request, send_from_directory

from db import Database
from solver import solve_repair_candidates
from validate import ValidationError, canonical, validate_design
from weave import find_violations

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "data", "weave.db"))
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

db = Database(DB_PATH)

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="")


def _design_payload(design):
    """原稿及其超限段，供页面展示。"""
    payload = dict(design)
    payload["violations"] = find_violations(
        design["grid"], design["rows"], design["cols"], design["k"])
    payload["onesCount"] = sum(v for row in design["grid"] for v in row)
    return payload


def _canonical_text(obj):
    """规范化输入的逐字节序列化，用于草稿与预览输入的一致性比对。"""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


@app.get("/api/state")
def get_state():
    return jsonify(_design_payload(db.get_design()))


@app.post("/api/preview")
def preview():
    try:
        rows, cols, grid, locks, k, ones_range = validate_design(
            request.get_json(silent=True))
    except ValidationError as exc:
        return jsonify({"error": str(exc)}), 400

    input_obj = canonical(rows, cols, grid, locks, k, ones_range)
    input_text = _canonical_text(input_obj)
    violations = find_violations(grid, rows, cols, k)
    solved = solve_repair_candidates(rows, cols, k, grid, locks, ones_range)

    if solved is None:
        # 无可行修补（含格值1总数区间不可达）：保留原稿，仅报无解（不发放令牌）
        return jsonify({
            "solvable": False,
            "violations": violations,
            "input": input_obj,
            "onesCount": sum(v for row in grid for v in row),
            "onesRange": input_obj["onesRange"],
        })

    candidates = solved["candidates"]
    first = candidates[0]
    # repaired/flips/flipCount 始终指向字典序最小的首个方案，旧客户端无感；
    # candidates 给出同样最优（翻转数同为全局最少）的前 3 个互不相同网格，
    # 每项含网格、翻转坐标与格值1总数。
    result = {
        "repaired": first["repaired"],
        "flips": first["flips"],
        "flipCount": solved["flipCount"],
        "candidates": candidates,
    }
    token = uuid.uuid4().hex
    # 预览绑定生成时的原稿版本；确认时若该版本已变则拒绝
    base_version = db.get_design()["version"]
    db.save_preview(token, input_text, result, base_version)
    return jsonify({
        "solvable": True,
        "token": token,
        "violations": violations,
        "input": input_obj,
        "onesCount": sum(v for row in grid for v in row),
        "repairedOnesCount": first["onesCount"],
        "onesRange": input_obj["onesRange"],
        "repaired": result["repaired"],
        "flips": result["flips"],
        "flipCount": result["flipCount"],
        "candidates": candidates,
        "candidateCount": len(candidates),
    })


@app.post("/api/confirm")
def confirm():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "请求体必须是 JSON 对象"}), 400

    token = body.get("token")
    if not isinstance(token, str) or not token:
        return jsonify({"error": "缺少预览令牌"}), 400

    # 可选方案选择：省略/为 0 表示确认预览返回的首个（字典序最小）方案；
    # 旧客户端与旧请求不带该字段，行为不变。bool 不接受（JSON 中属 int）。
    choice = body.get("choice", 0)
    if not isinstance(choice, int) or isinstance(choice, bool) or choice < 0:
        return jsonify({"error": "方案序号 choice 必须是从 0 开始的非负整数"}), 400

    try:
        rows, cols, grid, locks, k, ones_range = validate_design(body)
    except ValidationError as exc:
        return jsonify({"error": str(exc)}), 400

    # 原子确认：令牌未消费、草稿与预览输入一致（含格值1区间）、预览绑定的
    # 原稿版本未变、所选方案确在该预览候选内，四者同时成立才覆盖原稿
    # （版本仅 +1、区间随原稿持久化）并消费令牌；否则整体回滚。
    input_text = _canonical_text(canonical(rows, cols, grid, locks, k, ones_range))
    design, reason = db.commit_preview(token, input_text, choice)
    if design is None:
        if reason == "invalid_choice":
            # 越界或伪造（候选不在本次预览结果内）：拒绝且令牌保持未消费
            return jsonify({
                "error": "所选方案不在该预览令牌的候选范围内，请从预览返回的"
                         "方案中重新选择",
            }), 400
        message = {
            "consumed": "预览令牌不存在或已被确认，请重新预览",
            "mismatch": "预览后稿件已变动，旧预览已失效，请重新预览",
            "stale": "原稿已被其他确认更新，旧预览已失效，请重新预览",
        }[reason]
        return jsonify({"error": message}), 409

    return jsonify({"state": _design_payload(design)})


@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.errorhandler(404)
def not_found(_):
    return jsonify({"error": "接口不存在"}), 404


@app.errorhandler(405)
def method_not_allowed(_):
    return jsonify({"error": "方法不允许"}), 405


@app.errorhandler(500)
def internal_error(exc):
    return jsonify({"error": "服务器内部错误"}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    debug = os.environ.get("FLASK_DEBUG", "") == "1"
    if debug:
        app.run(host="0.0.0.0", port=port, debug=True)
    else:
        from waitress import serve
        serve(app, host="0.0.0.0", port=port)
