from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, List

from flask import Blueprint, Response, current_app, jsonify, render_template, request

from .config import AppConfig
from .db import (
    delete_session,
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


def _parse_payload() -> Dict:
    payload = request.get_json(silent=True)
    if payload is None and request.data:
        try:
            payload = json.loads(request.data.decode("utf-8"))
        except (ValueError, TypeError, AttributeError):
            payload = {}
    return payload or {}


def _start_session_logic(payload: Dict) -> Dict:
    cfg: AppConfig = current_app.config["MARKSCHECKER_CONFIG"]

    raw_terms = payload.get("searchTerm") or payload.get("search_terms") or ""
    global_sid = payload.get("globalSid") or payload.get("sessionId") or payload.get("voilaSessionId")
    search_type = (payload.get("searchType") or "article").lower()
    limit = payload.get("limit", "all")

    if not global_sid:
        raise ValueError("globalSid is required")
    if not raw_terms.strip():
        raise ValueError("searchTerm is required")

    service = get_service()
    region_info = service.get_region_info(global_sid)
    region_id = region_info.get("regionId")
    if not region_id:
        raise ValueError("Unable to resolve region from global_sid")

    terms, duplicates, duplicate_count, contains_ea = service.parse_terms(raw_terms)
    session_id = str(uuid.uuid4())

    insert_session(
        cfg.database_path,
        session_id=session_id,
        total_terms=len(terms),
        region_id=region_id,
        region_info=region_info,
    )

    chunk_size = cfg.chunk_size or 1
    total_chunks = (len(terms) + chunk_size - 1) // chunk_size if terms else 0

    return {
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
        "status": "processing" if terms else "pending",
    }


def _process_chunk_logic(session_id: str, payload: Dict) -> Dict:
    cfg: AppConfig = current_app.config["MARKSCHECKER_CONFIG"]
    session = fetch_session(cfg.database_path, session_id)
    if not session:
        raise LookupError("Session not found")

    search_terms: List[str] = (
        payload.get("searchTerms")
        or payload.get("terms")
        or []
    )
    global_sid = (
        payload.get("globalSid")
        or payload.get("sessionId")
        or payload.get("voilaSessionId")
    )
    limit = payload.get("limit", "all")
    search_type = (payload.get("searchType") or session.get("search_type") or "article").lower()
    chunk_index = payload.get("chunkIndex", 0)

    if not global_sid:
        raise ValueError("globalSid is required")
    if not search_terms:
        raise ValueError("terms list is required")

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
    total_terms = session.get("total_terms") or 0
    status = "completed" if new_processed >= total_terms and total_terms > 0 else "processing"

    update_session_progress(
        cfg.database_path,
        session_id=session_id,
        processed_terms=new_processed,
        total_products=new_total_products,
        status=status,
    )

    return {
        "sessionId": session_id,
        "chunkIndex": chunk_index,
        "processedCount": processed,
        "productsFound": len(products),
        "totalFound": total_found,
        "status": status,
    }


def _get_results_logic(session_id: str) -> Dict:
    cfg: AppConfig = current_app.config["MARKSCHECKER_CONFIG"]
    session = fetch_session(cfg.database_path, session_id)
    if not session:
        raise LookupError("Session not found")

    products = fetch_products(cfg.database_path, session_id)
    found_count = sum(1 for product in products if product.get("found"))
    not_found_count = sum(1 for product in products if not product.get("found"))

    return {
        "session": {
            "id": session_id,
            "status": session.get("status"),
            "totalTerms": session.get("total_terms"),
            "processedTerms": session.get("processed_terms"),
            "totalProducts": session.get("total_products"),
            "region": session.get("region_info"),
        },
        "products": products,
        "stats": {
            "total_products": len(products),
            "found_products": found_count,
            "not_found_products": not_found_count,
        },
    }


@pages_bp.route("/")
def index() -> str:
    return render_template("index.html")


@api_bp.route("/sessions", methods=["POST"])
def start_session() -> Response:
    try:
        data = _start_session_logic(_parse_payload())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:
        logger.exception("Failed to start session")
        return jsonify({"error": "Internal server error"}), 500
    return jsonify(data)


@api_bp.route("/sessions/<session_id>/chunks", methods=["POST"])
def process_chunk(session_id: str) -> Response:
    try:
        data = _process_chunk_logic(session_id, _parse_payload())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception:
        logger.exception("Failed to process chunk for session %s", session_id)
        return jsonify({"error": "Internal server error"}), 500
    return jsonify(data)


@api_bp.route("/sessions/<session_id>/results", methods=["GET"])
def get_results(session_id: str) -> Response:
    try:
        data = _get_results_logic(session_id)
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception:
        logger.exception("Failed to fetch results for session %s", session_id)
        return jsonify({"error": "Internal server error"}), 500
    return jsonify(data)


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


@api_bp.route("/cleanup-session", methods=["POST"])
def cleanup_session() -> Response:
    payload = _parse_payload()
    session_id = payload.get("sessionId") or payload.get("session_id")
    if not session_id:
        return jsonify({"error": "sessionId is required"}), 400

    cfg: AppConfig = current_app.config["MARKSCHECKER_CONFIG"]
    delete_session(cfg.database_path, session_id)
    return jsonify({"status": "ok"})


@api_bp.route("/start-search", methods=["POST"])
def legacy_start_search() -> Response:
    try:
        data = _start_session_logic(_parse_payload())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:
        logger.exception("Failed to start legacy session")
        return jsonify({"error": "Internal server error"}), 500

    return jsonify(
        {
            "session_id": data["sessionId"],
            "region_info": data["region"],
            "search_type": data["searchType"],
            "parsed_terms": data["terms"],
            "duplicates": data["duplicates"],
            "duplicate_count": data["duplicateCount"],
            "contains_ea_codes": data["containsEaCodes"],
            "total_terms": data["totalTerms"],
            "chunk_size": data["chunkSize"],
            "total_chunks": data["totalChunks"],
            "limit": data["limit"],
            "status": data["status"],
        }
    )


@api_bp.route("/process-chunk", methods=["POST"])
def legacy_process_chunk() -> Response:
    payload = _parse_payload()
    session_id = payload.get("sessionId") or payload.get("session_id")
    if not session_id:
        return jsonify({"error": "sessionId is required"}), 400

    try:
        data = _process_chunk_logic(session_id, payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception:
        logger.exception("Failed to process legacy chunk for session %s", session_id)
        return jsonify({"error": "Internal server error"}), 500

    return jsonify(
        {
            "session_id": data["sessionId"],
            "chunk_index": data["chunkIndex"],
            "processed_count": data["processedCount"],
            "products_found": data["productsFound"],
            "total_found": data["totalFound"],
            "status": data["status"],
        }
    )


@api_bp.route("/get-results/<session_id>", methods=["GET"])
def legacy_get_results(session_id: str) -> Response:
    try:
        data = _get_results_logic(session_id)
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception:
        logger.exception("Failed to fetch legacy results for session %s", session_id)
        return jsonify({"error": "Internal server error"}), 500

    return jsonify(
        {
            "session": data["session"],
            "products": data["products"],
            "stats": data["stats"],
        }
    )
