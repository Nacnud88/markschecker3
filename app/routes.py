from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, List

from flask import Blueprint, Response, current_app, jsonify, render_template, request

from .config import AppConfig
from .db import (
    fetch_products,
    fetch_session,
    insert_session,
    store_products,
    update_session_progress,
)
from .search_service import SearchService

logger = logging.getLogger(__name__)

api_bp = Blueprint("api", __name__)
pages_bp = Blueprint("pages", __name__)


def get_service() -> SearchService:
    cfg: AppConfig = current_app.config["MARKSCHECKER_CONFIG"]
    return SearchService(cfg)


@pages_bp.route("/")
def index() -> str:
    return render_template("index.html")


@api_bp.route("/sessions", methods=["POST"])
def start_session() -> Response:
    cfg: AppConfig = current_app.config["MARKSCHECKER_CONFIG"]

    payload = request.get_json(force=True, silent=False) or {}
    raw_terms = payload.get("searchTerm") or ""
    global_sid = payload.get("globalSid") or payload.get("sessionId")
    search_type = (payload.get("searchType") or "article").lower()
    limit = payload.get("limit", "all")

    if not global_sid:
        return jsonify({"error": "globalSid is required"}), 400
    if not raw_terms.strip():
        return jsonify({"error": "searchTerm is required"}), 400

    service = get_service()

    region_info = service.get_region_info(global_sid)
    region_id = region_info.get("regionId")
    if not region_id:
        return jsonify({"error": "Unable to resolve region from global_sid"}), 400

    terms, duplicates, duplicate_count, contains_ea = service.parse_terms(raw_terms)
    session_id = str(uuid.uuid4())

    insert_session(
        cfg.database_path,
        session_id=session_id,
        total_terms=len(terms),
        region_id=region_id,
        region_info=region_info,
    )

    chunk_size = cfg.chunk_size
    total_chunks = (len(terms) + chunk_size - 1) // chunk_size if chunk_size else 1

    return jsonify(
        {
            "sessionId": session_id,
            "region": region_info,
            "searchType": search_type,
            "terms": terms,
            "duplicates": duplicates,
            "duplicateCount": duplicate_count,
            "containsEaCodes": contains_ea,
            "totalTerms": len(terms),
            "chunkSize": chunk_size,
            "totalChunks": total_chunks,
            "limit": limit,
        }
    )


@api_bp.route("/sessions/<session_id>/chunks", methods=["POST"])
def process_chunk(session_id: str) -> Response:
    cfg: AppConfig = current_app.config["MARKSCHECKER_CONFIG"]
    payload = request.get_json(force=True, silent=False) or {}

    session = fetch_session(cfg.database_path, session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404

    search_terms: List[str] = payload.get("terms") or []
    global_sid = payload.get("globalSid") or payload.get("sessionId")
    limit = payload.get("limit", "all")
    search_type = (payload.get("searchType") or session.get("search_type") or "article").lower()
    chunk_index = payload.get("chunkIndex", 0)

    if not global_sid:
        return jsonify({"error": "globalSid is required"}), 400
    if not search_terms:
        return jsonify({"error": "terms list is required"}), 400

    service = get_service()
    products, total_found, processed = service.process_chunk(
        search_terms,
        global_sid=global_sid,
        limit=limit,
        search_type=search_type,
        region_id=session.get("region_id"),
    )

    store_products(cfg.database_path, session_id, products)

    new_processed = (session.get("processed_terms") or 0) + processed
    new_total_products = (session.get("total_products") or 0) + len(products)
    status = "completed" if new_processed >= (session.get("total_terms") or 0) else "active"

    update_session_progress(
        cfg.database_path,
        session_id=session_id,
        processed_terms=new_processed,
        total_products=new_total_products,
        status=status,
    )

    return jsonify(
        {
            "sessionId": session_id,
            "chunkIndex": chunk_index,
            "processedCount": processed,
            "productsFound": len(products),
            "totalFound": total_found,
            "status": status,
        }
    )


@api_bp.route("/sessions/<session_id>/results", methods=["GET"])
def get_results(session_id: str) -> Response:
    cfg: AppConfig = current_app.config["MARKSCHECKER_CONFIG"]
    session = fetch_session(cfg.database_path, session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404

    products = fetch_products(cfg.database_path, session_id)

    return jsonify(
        {
            "session": {
                "id": session_id,
                "status": session.get("status"),
                "totalTerms": session.get("total_terms"),
                "processedTerms": session.get("processed_terms"),
                "totalProducts": session.get("total_products"),
                "region": session.get("region_info"),
            },
            "products": products,
        }
    )


@api_bp.route("/sessions/<session_id>", methods=["GET"])
def session_status(session_id: str) -> Response:
    cfg: AppConfig = current_app.config["MARKSCHECKER_CONFIG"]
    session = fetch_session(cfg.database_path, session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404

    return jsonify(
        {
            "sessionId": session_id,
            "status": session.get("status"),
            "totalTerms": session.get("total_terms"),
            "processedTerms": session.get("processed_terms"),
            "totalProducts": session.get("total_products"),
            "region": session.get("region_info"),
        }
    )
