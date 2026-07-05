from flask import Blueprint, request, jsonify
from app.tnved import search_tnved, get_tnved_by_code

tnved_bp = Blueprint("tnved", __name__)


@tnved_bp.route("/search", methods=["GET"])
def api_search():
    q = request.args.get("q", "").strip()
    limit = min(int(request.args.get("limit", 20)), 100)
    if not q:
        return jsonify([])
    return jsonify(search_tnved(q, limit))


@tnved_bp.route("/<code>", methods=["GET"])
def api_get(code):
    result = get_tnved_by_code(code)
    if not result:
        return jsonify({"error": "Не найдено"}), 404
    return jsonify(result)
