"""Authenticated budget settings and cumulative usage for one project run."""
from flask import Blueprint, jsonify, request

from ..security import public_error
from ..utils.budget import BudgetStore
from ..utils.storage import validate_storage_id

budget_bp = Blueprint('budget', __name__)


@budget_bp.get('/<run_id>')
def get_budget(run_id):
    try:
        validate_storage_id(run_id, 'run_id')
        return jsonify(success=True, data=BudgetStore().get(run_id))
    except ValueError:
        return public_error({'success': False, 'error': 'Invalid run budget request'}, 400)


@budget_bp.put('/<run_id>')
def configure_budget(run_id):
    try:
        validate_storage_id(run_id, 'run_id')
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or set(body) != {'limits'} or not isinstance(body['limits'], dict):
            raise ValueError('Expected a limits object')
        return jsonify(success=True, data=BudgetStore().configure(run_id, body['limits']))
    except ValueError as error:
        # Validation messages contain field names and constraints only.
        return public_error({'success': False, 'error': str(error)}, 400)
