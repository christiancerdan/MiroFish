"""Read immutable evidence snapshots belonging to a saved report."""
from flask import Blueprint, jsonify

from ..services.report_agent import ReportManager
from ..services.report_provenance import sha256_text
from ..utils.storage import validate_storage_id


evidence_bp = Blueprint("evidence", __name__)


@evidence_bp.get("/<report_id>/<citation_id>")
def get_evidence(report_id, citation_id):
    try:
        validate_storage_id(report_id, "report_id")
        validate_storage_id(citation_id, "citation_id")
        report = ReportManager.get_report(report_id)
    except ValueError as error:
        return jsonify({"success": False, "error": str(error)}), 400
    if report is None:
        return jsonify({"success": False, "error": "Report not found"}), 404
    source = next((item for item in report.evidence.get("sources", []) if item.get("citation_id") == citation_id), None)
    if source is None:
        return jsonify({"success": False, "error": "Evidence not found in this report"}), 404
    if sha256_text(source.get("text", "")) != source.get("content_sha256"):
        return jsonify({"success": False, "error": "Stored evidence integrity check failed"}), 409
    return jsonify({"success": True, "data": source, "validation_scope": "reference_integrity_only"})
