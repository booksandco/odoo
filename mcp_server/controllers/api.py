import logging
import xmlrpc.client as xmlrpclib  # nosec
from datetime import datetime
from typing import Any, Optional, Tuple

import defusedxml.xmlrpc

from odoo import http
from odoo.http import request
from odoo.service import (
    common as common_service_root,
    db as db_service_root,
    model as model_service_root,
)

from odoo.addons.rpc.controllers.xmlrpc import dumps as odoo_dumps

from . import auth, utils
from .rate_limiting import (
    check_rate_limit,
    is_rate_limiting_enabled,
    record_api_request,
)

_logger = logging.getLogger(__name__)
defusedxml.xmlrpc.monkey_patch()

# XML-RPC fault codes aligned with HTTP status codes
XMLRPC_FAULT_CODES = {
    "bad_request": 400,
    "unauthorized": 401,
    "forbidden": 403,
    "not_found": 404,
    "rate_limit": 429,
    "internal_error": 500,
}


def _generate_xmlrpc_fault(code: int, message: str) -> str:
    """Build an XML-RPC fault string with standardized HTTP-aligned codes."""
    fault = xmlrpclib.Fault(code, message)
    return xmlrpclib.dumps(fault, methodresponse=1, allow_none=1)


def _get_client_ip() -> Optional[str]:
    """Get client IP address from request."""
    if request and hasattr(request, "httprequest"):
        return request.httprequest.remote_addr
    return None


def _dispatch_service_xmlrpc(controller_name: str, service_dispatch) -> Any:
    """Guard + parse + dispatch + marshal + fault scaffold for the stateless
    ``common`` / ``db`` XML-RPC proxies.

    The ``object`` proxy differs (custom access-controlled dispatch and Odoo's
    date-aware marshaller) and keeps its own handler below.
    """
    if not utils.is_mcp_enabled():
        fault_response = _generate_xmlrpc_fault(
            XMLRPC_FAULT_CODES["forbidden"],
            "MCP Server is disabled globally.",
        )
        return request.make_response(fault_response, [("Content-Type", "text/xml")])

    data = request.httprequest.data
    try:
        params, method = xmlrpclib.loads(data)
        result = service_dispatch(method, params)
        response_data = xmlrpclib.dumps((result,), methodresponse=1, allow_none=1)
        return request.make_response(response_data, [("Content-Type", "text/xml")])
    except xmlrpclib.Fault as e:
        _logger.warning(
            f"{controller_name} XML-RPC Fault: "
            f"Code {e.faultCode}, String: {e.faultString}"
        )
        return request.make_response(
            xmlrpclib.dumps(e, methodresponse=1, allow_none=1),
            [("Content-Type", "text/xml")],
        )
    except Exception as e:
        error_msg = str(e)
        _logger.error("Error in %s: %s", controller_name, error_msg, exc_info=True)
        fault_response = _generate_xmlrpc_fault(
            XMLRPC_FAULT_CODES["internal_error"],
            f"{controller_name} Error: {error_msg}",
        )
        return request.make_response(fault_response, [("Content-Type", "text/xml")])


class MCPCommonController(http.Controller):
    # auth="none"/csrf=False: stateless XML-RPC proxy. Credentials travel in the
    # call params (db, uid, api-key), not an Odoo session cookie, so there is no
    # CSRF surface (mirrors stock /xmlrpc/2/common); MCP is gated inside dispatch.
    @http.route(
        "/mcp/xmlrpc/common", type="http", auth="none", methods=["POST"], csrf=False
    )
    def index(self, **kwargs):
        return _dispatch_service_xmlrpc(
            "MCPCommonController", common_service_root.dispatch
        )


class MCPDatabaseController(http.Controller):
    # auth="none"/csrf=False: stateless XML-RPC proxy, same rationale as
    # /mcp/xmlrpc/common -- no session cookie, so no CSRF surface (mirrors stock
    # /xmlrpc/2/db); the global MCP kill-switch is checked inside dispatch.
    @http.route(
        "/mcp/xmlrpc/db", type="http", auth="none", methods=["POST"], csrf=False
    )
    def index(self, **kwargs):
        return _dispatch_service_xmlrpc(
            "MCPDatabaseController", db_service_root.dispatch
        )


class MCPObjectController(http.Controller):
    def _validate_request(self, xmlrpc_method: str, params: list) -> None:
        """Validate the XML-RPC method and params; raise Fault if invalid."""
        if xmlrpc_method != "execute_kw":
            _logger.warning(
                f"MCPObjectController received non-execute_kw method: {xmlrpc_method}"
            )
            if request and hasattr(request, "env"):
                request.env["mcp.log"].sudo().log_error(
                    error_message=f"MCPObjectController: "
                    f"Unsupported method {xmlrpc_method}. "
                    f"Only execute_kw is allowed.",
                    error_code="E400",
                    endpoint="/mcp/xmlrpc/object",
                    operation=xmlrpc_method,
                    ip_address=_get_client_ip(),
                )
            raise xmlrpclib.Fault(
                XMLRPC_FAULT_CODES["bad_request"],
                f"MCPObjectController: Unsupported method "
                f"{xmlrpc_method}. Only execute_kw is allowed.",
            )

        if len(params) < 5:
            raise xmlrpclib.Fault(
                XMLRPC_FAULT_CODES["bad_request"],
                "MCPObjectController: Insufficient parameters for execute_kw.",
            )

    def _identify_user(
        self, auth_token: Any, uid: Any
    ) -> Tuple[Optional[Any], Optional[int]]:
        """Identify the user from API key or uid, for rate limiting."""
        user_obj = None
        user_id = None

        # First try to get user from API key if it looks like one
        if isinstance(auth_token, str) and len(auth_token) > 20:
            user_obj = auth.get_user_from_api_key(auth_token)
            if user_obj:
                user_id = user_obj.id
                _logger.debug(
                    f"MCP XML-RPC: Identified user {user_id} "
                    f"from API key for rate limiting."
                )

        if not user_id and uid:
            user_id = uid

        return user_obj, user_id

    def _apply_rate_limiting(
        self,
        user_obj: Optional[Any],
        user_id: Optional[int],
        model_name: str,
        model_method: str,
    ) -> None:
        """Apply rate limiting if enabled; raise Fault if the limit is exceeded."""
        if not is_rate_limiting_enabled():
            return

        # Namespace the rate-limit bucket by database (cross-tenant collision).
        dbname = request.env.cr.dbname

        if user_id:
            if not check_rate_limit(user_id, dbname):
                _logger.warning(
                    f"MCP XML-RPC: Rate limit exceeded for user ID "
                    f"{user_id} on {model_name}.{model_method}."
                )
                env_for_log = request.env(user=user_obj.id) if user_obj else request.env
                env_for_log["mcp.log"].sudo().log_rate_limit_exceeded(
                    user_id=user_id,
                    endpoint="/mcp/xmlrpc/object",
                    ip_address=_get_client_ip(),
                )
                raise xmlrpclib.Fault(
                    XMLRPC_FAULT_CODES["rate_limit"],
                    "Too many requests. Rate limit exceeded.",
                )
            record_api_request(user_id, dbname)
        else:
            anonymous_id = -1
            if not check_rate_limit(anonymous_id, dbname):
                raise xmlrpclib.Fault(
                    XMLRPC_FAULT_CODES["rate_limit"],
                    "Too many requests. Rate limit exceeded.",
                )
            record_api_request(anonymous_id, dbname)

    def _get_env_for_user(self, user_obj: Optional[Any], uid: Any) -> Any:
        """Return the Odoo environment for the resolved user context."""
        if user_obj:
            return request.env(user=user_obj.id)

        if uid:
            try:
                return request.env(user=uid)
            except Exception as e:
                # Log the failure but continue with default environment
                _logger.debug(f"Failed to create environment for uid {uid}: {e}")

        return request.env

    def _extract_record_ids(self, params: list) -> Optional[list]:
        """Extract record IDs from params[5] if present, else None."""
        if len(params) > 5 and isinstance(params[5], list):
            # For methods like read, write that have record IDs in params[5]
            if params[5] and isinstance(params[5][0], int):
                return params[5]
        return None

    def _mcp_object_dispatch(self, xmlrpc_method: str, params: list):
        """Dispatch an XML-RPC object call through the MCP access-control gate."""
        self._validate_request(xmlrpc_method, params)

        # Standard params for execute_kw: (db_name, uid, password, model_name,
        # model_method, args_array, kwargs_dict)
        uid = params[1]
        auth_token = params[2]
        # Collapse CR/LF in the client-supplied method name before it reaches
        # any log sink or the mcp.log audit table: on this auth="none" route a
        # newline-bearing name would otherwise forge log/audit lines (CWE-117).
        # A legitimate method name has no CR/LF, so this is a no-op for real
        # calls; a forged one is denied at check_mcp_access before dispatch runs.
        model_method = utils._one_line(params[4])

        try:
            model_name = utils.sanitize_model_name(params[3])
        except ValueError as e:
            raise xmlrpclib.Fault(
                XMLRPC_FAULT_CODES["bad_request"], f"Invalid model name: {e}"
            ) from e

        user_obj, user_id = self._identify_user(auth_token, uid)

        self._apply_rate_limiting(user_obj, user_id, model_name, model_method)

        env_for_check = self._get_env_for_user(user_obj, uid)

        start_time = datetime.now()
        ip_address = _get_client_ip()

        if not utils.check_mcp_access(env_for_check, model_name, model_method):
            env_for_check["mcp.log"].sudo().log_permission_denied(
                model_name=model_name,
                operation=model_method,
                user_id=user_id,
                endpoint="/mcp/xmlrpc/object",
                ip_address=ip_address,
                error_message=f"Access denied by MCP for model "
                f"'{model_name}' method '{model_method}'.",
            )
            raise xmlrpclib.Fault(
                XMLRPC_FAULT_CODES["forbidden"],
                f"Access denied by MCP for model "
                f"'{model_name}' method '{model_method}'.",
            )

        _logger.info(
            f"MCP XML-RPC: Access GRANTED for {model_name}.{model_method} "
            f"(User ID: {user_id if user_id else 'N/A'})"
        )

        try:
            result = model_service_root.dispatch(xmlrpc_method, params)

            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            env_for_check["mcp.log"].sudo().log_model_access(
                model_name=model_name,
                operation=model_method,
                user_id=user_id,
                record_ids=self._extract_record_ids(params),
                endpoint="/mcp/xmlrpc/object",
                http_method="POST",
                duration_ms=duration_ms,
                ip_address=ip_address,
            )

            return result
        except Exception as e:
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            env_for_check["mcp.log"].sudo().log_error(
                error_message=str(e),
                error_code="E500",
                endpoint="/mcp/xmlrpc/object",
                model_name=model_name,
                operation=model_method,
                user_id=user_id,
                ip_address=ip_address,
            )
            raise

    # auth="none"/csrf=False: stateless XML-RPC proxy -- credentials ride in the
    # call params, not a session cookie, so no CSRF surface (mirrors stock
    # /xmlrpc/2/object). Every call is access-controlled by check_mcp_access
    # (per-model/operation MCP gate) inside _mcp_object_dispatch.
    @http.route(
        "/mcp/xmlrpc/object", type="http", auth="none", methods=["POST"], csrf=False
    )
    def index(self, **kwargs):
        if not utils.is_mcp_enabled():
            fault_response = _generate_xmlrpc_fault(
                XMLRPC_FAULT_CODES["forbidden"],
                "MCP Server is disabled globally.",
            )
            return request.make_response(fault_response, [("Content-Type", "text/xml")])

        data = request.httprequest.data
        try:
            params, method = xmlrpclib.loads(data)
            result = self._mcp_object_dispatch(method, params)
            # Use Odoo's custom XML-RPC marshaller that handles date objects
            response_data = odoo_dumps((result,))
            return request.make_response(response_data, [("Content-Type", "text/xml")])
        except xmlrpclib.Fault as e:
            _logger.warning(
                f"MCPObjectController XML-RPC Fault: "
                f"Code {e.faultCode}, String: {e.faultString}"
            )
            return request.make_response(
                xmlrpclib.dumps(e, methodresponse=1, allow_none=1),
                [("Content-Type", "text/xml")],
            )
        except Exception as e:
            error_msg = str(e)
            _logger.error(
                "Critical error in MCPObjectController dispatch: %s",
                error_msg,
                exc_info=True,
            )
            fault_response = _generate_xmlrpc_fault(
                XMLRPC_FAULT_CODES["internal_error"],
                f"Internal Server Error in MCPObjectController: {error_msg}",
            )
            return request.make_response(fault_response, [("Content-Type", "text/xml")])
