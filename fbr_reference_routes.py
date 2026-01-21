"""
FBR Reference Data API Routes
Provides endpoints to fetch HS codes, UOMs, and HS-UOM combinations from FBR.
Includes caching to minimize API calls and improve performance.
"""
from flask import request, jsonify, session
import requests
import time
import threading

# In-memory cache for reference data
# Structure: { "hs_codes": {"data": [...], "timestamp": ...}, ... }
_reference_cache = {}
_cache_lock = threading.Lock()

# Cache duration in seconds (24 hours for HS codes, 1 hour for HS-UOM)
CACHE_DURATION_HS_CODES = 86400  # 24 hours
CACHE_DURATION_UOMS = 86400  # 24 hours
CACHE_DURATION_HS_UOM = 3600  # 1 hour
CACHE_DURATION_REFERENCE = 86400  # 24 hours for provinces, doc types, etc.
CACHE_DURATION_REG_TYPE = 3600  # 1 hour for registration type lookups

# FBR API Base URLs
FBR_SANDBOX_BASE = "https://gw.fbr.gov.pk"
FBR_PRODUCTION_BASE = "https://gw.fbr.gov.pk"

# Fallback static data (used when FBR API is unavailable)
FALLBACK_UOMS = [
    {"uoM_ID": 1, "value": "Numbers, pieces, units", "description": "Numbers, pieces, units"},
    {"uoM_ID": 13, "value": "KG", "description": "KG - Kilogram"},
    {"uoM_ID": 14, "value": "MT", "description": "MT - Metric Ton"},
    {"uoM_ID": 15, "value": "LTR", "description": "LTR - Liter"},
    {"uoM_ID": 16, "value": "KWH", "description": "KWH - Kilowatt Hour"},
    {"uoM_ID": 17, "value": "MTR", "description": "MTR - Meter"},
    {"uoM_ID": 77, "value": "Square Metre", "description": "Square Metre"},
    {"uoM_ID": 18, "value": "CFT", "description": "CFT - Cubic Feet"},
    {"uoM_ID": 19, "value": "SFT", "description": "SFT - Square Feet"},
    {"uoM_ID": 20, "value": "PAIRS", "description": "PAIRS"},
    {"uoM_ID": 21, "value": "DOZEN", "description": "DOZEN"},
    {"uoM_ID": 22, "value": "GROSS", "description": "GROSS"},
    {"uoM_ID": 23, "value": "SET", "description": "SET"},
    {"uoM_ID": 24, "value": "PACK", "description": "PACK"},
    {"uoM_ID": 25, "value": "REAM", "description": "REAM"},
    {"uoM_ID": 26, "value": "ROLL", "description": "ROLL"},
    {"uoM_ID": 27, "value": "SHEET", "description": "SHEET"},
    {"uoM_ID": 28, "value": "TON", "description": "TON"},
    {"uoM_ID": 29, "value": "YARD", "description": "YARD"},
    {"uoM_ID": 30, "value": "FEET", "description": "FEET"},
    {"uoM_ID": 31, "value": "INCH", "description": "INCH"},
    {"uoM_ID": 32, "value": "CM", "description": "CM - Centimeter"},
    {"uoM_ID": 33, "value": "MM", "description": "MM - Millimeter"},
    {"uoM_ID": 34, "value": "GRAM", "description": "GRAM"},
    {"uoM_ID": 35, "value": "ML", "description": "ML - Milliliter"},
    {"uoM_ID": 36, "value": "GALLON", "description": "GALLON"},
    {"uoM_ID": 37, "value": "BARREL", "description": "BARREL"},
    {"uoM_ID": 38, "value": "BAG", "description": "BAG"},
    {"uoM_ID": 39, "value": "BOX", "description": "BOX"},
    {"uoM_ID": 40, "value": "CARTON", "description": "CARTON"},
    {"uoM_ID": 41, "value": "BOTTLE", "description": "BOTTLE"},
    {"uoM_ID": 42, "value": "CAN", "description": "CAN"},
    {"uoM_ID": 43, "value": "DRUM", "description": "DRUM"},
    {"uoM_ID": 44, "value": "BUNDLE", "description": "BUNDLE"},
    {"uoM_ID": 45, "value": "BALE", "description": "BALE"},
]


def _get_cache(key):
    """Get cached data if not expired."""
    with _cache_lock:
        if key in _reference_cache:
            cached = _reference_cache[key]
            if time.time() - cached["timestamp"] < cached["duration"]:
                return cached["data"]
    return None


def _set_cache(key, data, duration):
    """Set cache with expiration duration."""
    with _cache_lock:
        _reference_cache[key] = {
            "data": data,
            "timestamp": time.time(),
            "duration": duration
        }


def _clear_cache(key=None):
    """Clear specific cache key or all cache."""
    with _cache_lock:
        if key:
            _reference_cache.pop(key, None)
        else:
            _reference_cache.clear()


def add_fbr_reference_routes(app, get_db_connection, get_env):
    """Add FBR reference data routes to the Flask app."""
    
    def _get_client_token():
        """Get the API token for the current client."""
        client_id = session.get("client_id")
        # Get env from session first (set during login), then fallback to request params
        env = session.get("env") or get_env()
        
        print(f"[FBR Reference] _get_client_token called - client_id: {client_id}, env: {env}")
        print(f"[FBR Reference] Session contents: {dict(session)}")
        
        if not client_id:
            print("[FBR Reference] ERROR: No client_id in session!")
            return None, None
        
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute(
                """
                SELECT sandbox_api_url, sandbox_api_token, production_api_url, production_api_token
                FROM clients
                WHERE id = %s
                """,
                (client_id,),
            )
            row = cur.fetchone()
            cur.close()
            conn.close()
            
            if not row:
                print(f"[FBR Reference] ERROR: No client found with id {client_id}")
                return None, None
            
            sandbox_url, sandbox_token, prod_url, prod_token = row
            
            print(f"[FBR Reference] Token found - sandbox_token exists: {bool(sandbox_token)}, prod_token exists: {bool(prod_token)}")
            
            if env == "sandbox":
                token = sandbox_token
                url = sandbox_url or FBR_SANDBOX_BASE
                print(f"[FBR Reference] Using SANDBOX - URL: {url}, Token length: {len(token) if token else 0}")
                return url, token
            else:
                token = prod_token
                url = prod_url or FBR_PRODUCTION_BASE
                print(f"[FBR Reference] Using PRODUCTION - URL: {url}, Token length: {len(token) if token else 0}")
                return url, token
        except Exception as e:
            print(f"[FBR Reference] ERROR getting client token: {e}")
            import traceback
            traceback.print_exc()
            return None, None
    
    def _filter_hs_codes(hs_codes_list, search_term):
        """Filter HS codes by search term (code or description)."""
        if not search_term:
            return hs_codes_list
        
        search_lower = search_term.lower()
        return [
            hs for hs in hs_codes_list
            if search_lower in (hs.get("hS_CODE", "") or "").lower()
            or search_lower in (hs.get("description", "") or "").lower()
        ]
    
    def _make_fbr_request(endpoint, token, params=None):
        """Make authenticated request to FBR API."""
        base_url = FBR_SANDBOX_BASE  # Reference APIs typically use same base
        url = f"{base_url}{endpoint}"
        
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        
        print(f"[FBR API] Making request to: {url}")
        print(f"[FBR API] Params: {params}")
        print(f"[FBR API] Token (first 20 chars): {token[:20] if token and len(token) > 20 else token}...")
        
        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)
            print(f"[FBR API] Response status: {response.status_code}")
            response.raise_for_status()
            data = response.json()
            print(f"[FBR API] Response data type: {type(data)}, length: {len(data) if isinstance(data, list) else 'N/A'}")
            return data, None
        except requests.Timeout:
            print("[FBR API] ERROR: Request timed out")
            return None, "FBR API request timed out"
        except requests.ConnectionError as e:
            print(f"[FBR API] ERROR: Connection failed - {e}")
            return None, "Failed to connect to FBR API"
        except requests.HTTPError as e:
            print(f"[FBR API] ERROR: HTTP error {e.response.status_code} - {e.response.text[:200] if e.response.text else 'No body'}")
            return None, f"FBR API error: {e.response.status_code}"
        except Exception as e:
            print(f"[FBR API] ERROR: Unexpected - {e}")
            import traceback
            traceback.print_exc()
            return None, f"Unexpected error: {str(e)}"

    # -------------------- HS Codes API --------------------
    @app.route("/api/reference/hs-codes", methods=["GET"])
    def get_hs_codes():
        """
        Fetch all valid HS codes from FBR API 5.3.
        Returns cached data if available.
        Query params:
          - search: Filter HS codes by code or description
          - refresh: Force cache refresh if true
        """
        print("\n" + "="*60)
        print("[HS-CODES API] Request received")
        
        client_id = session.get("client_id")
        print(f"[HS-CODES API] Client ID from session: {client_id}")
        
        if not client_id:
            print("[HS-CODES API] ERROR: No client ID in session!")
            return jsonify({"error": "No client ID in session", "hs_codes": [], "debug": "client_id missing"}), 401
        
        search = request.args.get("search", "").strip().lower()
        force_refresh = request.args.get("refresh", "").lower() == "true"
        
        print(f"[HS-CODES API] Search term: '{search}', Force refresh: {force_refresh}")
        
        cache_key = f"hs_codes_{client_id}"
        
        # Check cache first (unless force refresh)
        if not force_refresh:
            cached_data = _get_cache(cache_key)
            if cached_data:
                print(f"[HS-CODES API] Found {len(cached_data)} HS codes in cache")
                # Apply search filter to cached data
                if search:
                    filtered = [
                        hs for hs in cached_data 
                        if search in (hs.get("hS_CODE", "") or "").lower() 
                        or search in (hs.get("description", "") or "").lower()
                    ]
                    print(f"[HS-CODES API] Filtered to {len(filtered)} results for search '{search}'")
                    return jsonify({"hs_codes": filtered[:100], "source": "cache", "total": len(filtered)})
                return jsonify({"hs_codes": cached_data[:500], "source": "cache", "total": len(cached_data)})
        
        # Fetch from FBR API
        print("[HS-CODES API] Cache miss or refresh requested, fetching from FBR...")
        _, token = _get_client_token()
        if not token:
            print("[HS-CODES API] ERROR: No API token found!")
            return jsonify({
                "hs_codes": [], 
                "source": "error", 
                "total": 0,
                "error": "No FBR API token configured for this client. Please check client settings.",
                "debug": "token missing"
            })
        
        print(f"[HS-CODES API] Token found, calling FBR API...")
        data, error = _make_fbr_request("/pdi/v1/itemdesccode", token)
        
        if error:
            print(f"[HS-CODES API] FBR API error: {error}")
            return jsonify({
                "hs_codes": [], 
                "source": "error",
                "error": error,
                "total": 0,
                "message": f"Failed to fetch HS codes from FBR: {error}",
                "debug": "api_error"
            })
        
        # Cache the results
        if isinstance(data, list):
            _set_cache(cache_key, data, CACHE_DURATION_HS_CODES)
            
            # Apply search filter
            if search:
                filtered = [
                    hs for hs in data 
                    if search in (hs.get("hS_CODE", "") or "").lower() 
                    or search in (hs.get("description", "") or "").lower()
                ]
                return jsonify({"hs_codes": filtered[:100], "source": "api", "total": len(filtered)})
            
            return jsonify({"hs_codes": data[:500], "source": "api", "total": len(data)})
        
        return jsonify({"hs_codes": [], "source": "api", "error": "Unexpected response format"})

    # -------------------- UOMs API --------------------
    @app.route("/api/reference/uoms", methods=["GET"])
    def get_uoms():
        """
        Fetch all available UOMs from FBR API 5.6.
        Returns cached data if available.
        """
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401
        
        force_refresh = request.args.get("refresh", "").lower() == "true"
        cache_key = f"uoms_{client_id}"
        
        # Check cache first
        if not force_refresh:
            cached_data = _get_cache(cache_key)
            if cached_data:
                return jsonify({"uoms": cached_data, "source": "cache"})
        
        # Fetch from FBR API
        _, token = _get_client_token()
        if not token:
            # Return fallback data if no token
            print("No API token, using fallback UOMs")
            return jsonify({"uoms": FALLBACK_UOMS, "source": "fallback"})
        
        data, error = _make_fbr_request("/pdi/v1/uom", token)
        
        if error:
            print(f"FBR UOM API error: {error}, using fallback")
            return jsonify({"uoms": FALLBACK_UOMS, "source": "fallback", "error": error})
        
        # Process and cache results - FBR returns [{"uoM_ID": 13, "description": "KG"}, ...]
        if isinstance(data, list) and len(data) > 0:
            # Normalize the data format
            normalized = []
            for item in data:
                uom_id = item.get("uoM_ID") or item.get("uom_id") or item.get("id")
                description = item.get("description", "")
                normalized.append({
                    "uoM_ID": uom_id,
                    "value": description,  # Use description as value for dropdown
                    "description": description
                })
            
            print(f"UOM API: Loaded {len(normalized)} UOMs from FBR")
            _set_cache(cache_key, normalized, CACHE_DURATION_UOMS)
            return jsonify({"uoms": normalized, "source": "api", "count": len(normalized)})
        
        # Fallback if unexpected format
        return jsonify({"uoms": FALLBACK_UOMS, "source": "fallback"})

    # -------------------- HS Code UOM Validation API --------------------
    @app.route("/api/reference/hs-uom", methods=["GET"])
    def get_hs_uom():
        """
        Fetch valid UOMs for a specific HS code from FBR API 5.9.
        Query params:
          - hs_code: The HS code to get valid UOMs for (required)
          - annexure_id: Sales annexure ID (default: 3 for most sales)
        """
        print("\n" + "="*60)
        print("[HS-UOM API] Request received")
        
        client_id = session.get("client_id")
        print(f"[HS-UOM API] Client ID from session: {client_id}")
        
        if not client_id:
            print("[HS-UOM API] ERROR: No client ID in session!")
            return jsonify({"error": "No client ID in session", "valid_uoms": [], "debug": "client_id missing"}), 401
        
        hs_code = request.args.get("hs_code", "").strip()
        annexure_id = request.args.get("annexure_id", "3")  # Default to 3 for sales
        
        print(f"[HS-UOM API] HS Code: '{hs_code}', Annexure ID: {annexure_id}")
        
        if not hs_code:
            print("[HS-UOM API] ERROR: hs_code parameter missing!")
            return jsonify({"error": "hs_code parameter is required", "valid_uoms": []}), 400
        
        cache_key = f"hs_uom_{client_id}_{hs_code}_{annexure_id}"
        
        # Check cache first
        cached_data = _get_cache(cache_key)
        if cached_data:
            print(f"[HS-UOM API] Found {len(cached_data)} UOMs in cache for HS code {hs_code}")
            return jsonify({
                "hs_code": hs_code,
                "valid_uoms": cached_data,
                "source": "cache",
                "count": len(cached_data)
            })
        
        # Fetch from FBR API
        print("[HS-UOM API] Cache miss, fetching from FBR...")
        _, token = _get_client_token()
        if not token:
            print("[HS-UOM API] ERROR: No API token found!")
            # Return all UOMs as fallback (no restriction)
            return jsonify({
                "hs_code": hs_code,
                "valid_uoms": FALLBACK_UOMS,
                "source": "fallback",
                "message": "API token not configured. All UOMs shown.",
                "debug": "token missing"
            })
        
        params = {
            "hs_code": hs_code,
            "annexure_id": annexure_id
        }
        
        data, error = _make_fbr_request("/pdi/v2/HS_UOM", token, params)
        
        if error:
            print(f"FBR HS_UOM API error for {hs_code}: {error}")
            # Return all UOMs as fallback when API fails
            return jsonify({
                "hs_code": hs_code,
                "valid_uoms": FALLBACK_UOMS,
                "source": "fallback",
                "error": error,
                "message": "Could not validate HS+UOM. All UOMs shown."
            })
        
        # Process and cache results - FBR returns [{"uoM_ID": 13, "description": "KG"}, ...]
        if isinstance(data, list) and len(data) > 0:
            normalized = []
            for item in data:
                uom_id = item.get("uoM_ID") or item.get("uom_id") or item.get("id")
                description = item.get("description", "")
                normalized.append({
                    "uoM_ID": uom_id,
                    "value": description,  # Use description as value for dropdown
                    "description": description
                })
            
            _set_cache(cache_key, normalized, CACHE_DURATION_HS_UOM)
            
            print(f"HS-UOM API: Found {len(normalized)} valid UOMs for HS code {hs_code}: {[u['description'] for u in normalized]}")
            
            return jsonify({
                "hs_code": hs_code,
                "valid_uoms": normalized,
                "source": "api",
                "count": len(normalized)
            })
        
        # Empty or unexpected response - return all UOMs
        return jsonify({
            "hs_code": hs_code,
            "valid_uoms": FALLBACK_UOMS,
            "source": "fallback",
            "message": "No specific UOMs found for this HS code. All UOMs shown."
        })

    # -------------------- Validate HS+UOM Combination --------------------
    @app.route("/api/reference/validate-hs-uom", methods=["POST"])
    def validate_hs_uom():
        """
        Validate if a specific HS code + UOM combination is valid.
        Request body:
          - hs_code: The HS code
          - uom: The UOM value to validate
        """
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401
        
        data = request.get_json() or {}
        hs_code = (data.get("hs_code") or "").strip()
        uom = (data.get("uom") or "").strip()
        
        if not hs_code or not uom:
            return jsonify({"error": "Both hs_code and uom are required"}), 400
        
        # Get valid UOMs for this HS code
        _, token = _get_client_token()
        if not token:
            # Can't validate without token, assume valid
            return jsonify({
                "valid": True,
                "message": "Validation skipped (API not configured)",
                "hs_code": hs_code,
                "uom": uom
            })
        
        params = {"hs_code": hs_code, "annexure_id": "3"}
        valid_uoms_data, error = _make_fbr_request("/pdi/v2/HS_UOM", token, params)
        
        if error:
            # Can't validate, assume valid
            return jsonify({
                "valid": True,
                "message": f"Validation skipped ({error})",
                "hs_code": hs_code,
                "uom": uom
            })
        
        if not isinstance(valid_uoms_data, list) or len(valid_uoms_data) == 0:
            # No restrictions, assume valid
            return jsonify({
                "valid": True,
                "message": "No UOM restrictions for this HS code",
                "hs_code": hs_code,
                "uom": uom
            })
        
        # Check if UOM is in the valid list
        uom_lower = uom.lower()
        for valid_uom in valid_uoms_data:
            desc = (valid_uom.get("description") or "").lower()
            if uom_lower == desc or uom_lower in desc or desc in uom_lower:
                return jsonify({
                    "valid": True,
                    "message": "Valid combination",
                    "hs_code": hs_code,
                    "uom": uom,
                    "matched_uom": valid_uom.get("description")
                })
        
        # Not found in valid list
        valid_names = [u.get("description") for u in valid_uoms_data]
        return jsonify({
            "valid": False,
            "message": f"UOM '{uom}' is not valid for HS code '{hs_code}'",
            "hs_code": hs_code,
            "uom": uom,
            "valid_options": valid_names
        })

    # -------------------- Clear Cache (Admin) --------------------
    @app.route("/api/reference/clear-cache", methods=["POST"])
    def clear_reference_cache():
        """Clear the reference data cache. Useful for admin/debugging."""
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401
        
        cache_type = request.args.get("type", "all")
        
        if cache_type == "all":
            _clear_cache()
            return jsonify({"message": "All reference cache cleared"})
        elif cache_type == "hs":
            _clear_cache(f"hs_codes_{client_id}")
            return jsonify({"message": "HS codes cache cleared"})
        elif cache_type == "uom":
            _clear_cache(f"uoms_{client_id}")
            return jsonify({"message": "UOMs cache cleared"})
        else:
            return jsonify({"error": "Invalid cache type"}), 400

    # -------------------- Reference Data Status --------------------
    @app.route("/api/reference/status", methods=["GET"])
    def get_reference_status():
        """Get status of reference data APIs and cache."""
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401
        
        base_url, token = _get_client_token()
        
        status = {
            "api_configured": bool(token),
            "base_url": base_url,
            "token_preview": f"{token[:20]}..." if token and len(token) > 20 else token,
            "cache_status": {}
        }
        
        # Check cache status
        with _cache_lock:
            for key, value in _reference_cache.items():
                if str(client_id) in key:
                    age = time.time() - value["timestamp"]
                    status["cache_status"][key] = {
                        "age_seconds": int(age),
                        "items": len(value["data"]) if isinstance(value["data"], list) else 1,
                        "expired": age > value["duration"]
                    }
        
        return jsonify(status)

    # -------------------- Debug Token Endpoint --------------------
    @app.route("/api/reference/debug-token", methods=["GET"])
    def debug_token():
        """Debug endpoint to check token retrieval - shows exactly what's happening."""
        client_id = session.get("client_id")
        env_from_session = session.get("env")
        env_from_request = get_env()
        
        debug_info = {
            "session": dict(session),
            "client_id": client_id,
            "env_from_session": env_from_session,
            "env_from_request": env_from_request,
        }
        
        if not client_id:
            debug_info["error"] = "No client_id in session"
            return jsonify(debug_info)
        
        # Try to get client data
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, sandbox_api_url, sandbox_api_token, production_api_url, production_api_token
                FROM clients
                WHERE id = %s
                """,
                (client_id,),
            )
            row = cur.fetchone()
            cur.close()
            conn.close()
            
            if row:
                debug_info["client_found"] = True
                debug_info["db_client_id"] = row[0]
                debug_info["sandbox_api_url"] = row[1]
                debug_info["sandbox_token_exists"] = bool(row[2])
                debug_info["sandbox_token_length"] = len(row[2]) if row[2] else 0
                debug_info["sandbox_token_preview"] = f"{row[2][:30]}..." if row[2] and len(row[2]) > 30 else row[2]
                debug_info["production_api_url"] = row[3]
                debug_info["production_token_exists"] = bool(row[4])
                debug_info["production_token_length"] = len(row[4]) if row[4] else 0
                debug_info["production_token_preview"] = f"{row[4][:30]}..." if row[4] and len(row[4]) > 30 else row[4]
            else:
                debug_info["client_found"] = False
                debug_info["error"] = f"No client record found for id {client_id}"
        except Exception as e:
            debug_info["db_error"] = str(e)
        
        return jsonify(debug_info)

    # ==================== NEW FBR REFERENCE APIs ====================

    # -------------------- Provinces API (5.1) --------------------
    @app.route("/api/reference/provinces", methods=["GET"])
    def get_provinces():
        """
        Fetch province codes from FBR API 5.1.
        URL: https://gw.fbr.gov.pk/pdi/v1/provinces
        Returns: [{stateProvinceCode, stateProvinceDesc}, ...]
        """
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401
        
        cache_key = f"provinces_{client_id}"
        
        # Check cache first
        cached_data = _get_cache(cache_key)
        if cached_data:
            return jsonify({"provinces": cached_data, "source": "cache"})
        
        # Fetch from FBR API
        _, token = _get_client_token()
        if not token:
            # Fallback provinces
            fallback = [
                {"stateProvinceCode": 7, "stateProvinceDesc": "PUNJAB"},
                {"stateProvinceCode": 8, "stateProvinceDesc": "SINDH"},
                {"stateProvinceCode": 9, "stateProvinceDesc": "KPK"},
                {"stateProvinceCode": 10, "stateProvinceDesc": "BALOCHISTAN"},
                {"stateProvinceCode": 11, "stateProvinceDesc": "ISLAMABAD"},
                {"stateProvinceCode": 12, "stateProvinceDesc": "AJK"},
                {"stateProvinceCode": 13, "stateProvinceDesc": "GILGIT BALTISTAN"},
            ]
            return jsonify({"provinces": fallback, "source": "fallback"})
        
        data, error = _make_fbr_request("/pdi/v1/provinces", token)
        
        if error:
            print(f"[Provinces API] Error: {error}")
            fallback = [
                {"stateProvinceCode": 7, "stateProvinceDesc": "PUNJAB"},
                {"stateProvinceCode": 8, "stateProvinceDesc": "SINDH"},
                {"stateProvinceCode": 9, "stateProvinceDesc": "KPK"},
                {"stateProvinceCode": 10, "stateProvinceDesc": "BALOCHISTAN"},
                {"stateProvinceCode": 11, "stateProvinceDesc": "ISLAMABAD"},
            ]
            return jsonify({"provinces": fallback, "source": "fallback", "error": error})
        
        if isinstance(data, list):
            _set_cache(cache_key, data, CACHE_DURATION_REFERENCE)
            return jsonify({"provinces": data, "source": "api", "count": len(data)})
        
        return jsonify({"provinces": [], "source": "api", "error": "Unexpected response format"})

    # -------------------- Document Types API (5.2) --------------------
    @app.route("/api/reference/document-types", methods=["GET"])
    def get_document_types():
        """
        Fetch document types from FBR API 5.2.
        URL: https://gw.fbr.gov.pk/pdi/v1/doctypecode
        Returns: [{docTypeId, docDescription}, ...]
        """
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401
        
        cache_key = f"doctypes_{client_id}"
        
        # Check cache first
        cached_data = _get_cache(cache_key)
        if cached_data:
            return jsonify({"document_types": cached_data, "source": "cache"})
        
        # Fetch from FBR API
        _, token = _get_client_token()
        if not token:
            fallback = [
                {"docTypeId": 4, "docDescription": "Sale Invoice"},
                {"docTypeId": 9, "docDescription": "Debit Note"},
                {"docTypeId": 10, "docDescription": "Credit Note"},
            ]
            return jsonify({"document_types": fallback, "source": "fallback"})
        
        data, error = _make_fbr_request("/pdi/v1/doctypecode", token)
        
        if error:
            print(f"[Document Types API] Error: {error}")
            fallback = [
                {"docTypeId": 4, "docDescription": "Sale Invoice"},
                {"docTypeId": 9, "docDescription": "Debit Note"},
            ]
            return jsonify({"document_types": fallback, "source": "fallback", "error": error})
        
        if isinstance(data, list):
            _set_cache(cache_key, data, CACHE_DURATION_REFERENCE)
            return jsonify({"document_types": data, "source": "api", "count": len(data)})
        
        return jsonify({"document_types": [], "source": "api", "error": "Unexpected response format"})

    # -------------------- SRO Item Codes API (5.4) --------------------
    @app.route("/api/reference/sro-items", methods=["GET"])
    def get_sro_items():
        """
        Fetch SRO item codes from FBR API 5.4.
        URL: https://gw.fbr.gov.pk/pdi/v1/sroitemcode
        Returns: [{srO_ITEM_ID, srO_ITEM_DESC}, ...]
        """
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401
        
        cache_key = f"sroitems_{client_id}"
        
        # Check cache first
        cached_data = _get_cache(cache_key)
        if cached_data:
            return jsonify({"sro_items": cached_data, "source": "cache"})
        
        # Fetch from FBR API
        _, token = _get_client_token()
        if not token:
            return jsonify({"sro_items": [], "source": "fallback", "message": "API token not configured"})
        
        data, error = _make_fbr_request("/pdi/v1/sroitemcode", token)
        
        if error:
            print(f"[SRO Items API] Error: {error}")
            return jsonify({"sro_items": [], "source": "error", "error": error})
        
        if isinstance(data, list):
            _set_cache(cache_key, data, CACHE_DURATION_REFERENCE)
            return jsonify({"sro_items": data, "source": "api", "count": len(data)})
        
        return jsonify({"sro_items": [], "source": "api", "error": "Unexpected response format"})

    # -------------------- Transaction Types API (5.5) --------------------
    @app.route("/api/reference/transaction-types", methods=["GET"])
    def get_transaction_types():
        """
        Fetch transaction types from FBR API 5.5.
        URL: https://gw.fbr.gov.pk/pdi/v1/transtypecode
        Returns: [{transactioN_TYPE_ID, transactioN_DESC}, ...]
        """
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401
        
        cache_key = f"transtypes_{client_id}"
        
        # Check cache first
        cached_data = _get_cache(cache_key)
        if cached_data:
            return jsonify({"transaction_types": cached_data, "source": "cache"})
        
        # Fetch from FBR API
        _, token = _get_client_token()
        if not token:
            return jsonify({"transaction_types": [], "source": "fallback", "message": "API token not configured"})
        
        data, error = _make_fbr_request("/pdi/v1/transtypecode", token)
        
        if error:
            print(f"[Transaction Types API] Error: {error}")
            return jsonify({"transaction_types": [], "source": "error", "error": error})
        
        if isinstance(data, list):
            _set_cache(cache_key, data, CACHE_DURATION_REFERENCE)
            return jsonify({"transaction_types": data, "source": "api", "count": len(data)})
        
        return jsonify({"transaction_types": [], "source": "api", "error": "Unexpected response format"})

    # -------------------- Sale Type To Rate API (5.8) --------------------
    @app.route("/api/reference/sale-type-rates", methods=["GET"])
    def get_sale_type_rates():
        """
        Fetch valid tax rates for a sale type from FBR API 5.8.
        URL: https://gw.fbr.gov.pk/pdi/v2/SaleTypeToRate?date=24-Feb-2024&transTypeId=18&originationSupplier=1
        
        Query params:
          - date: Date in DD-MMM-YYYY format (e.g., 24-Feb-2024)
          - trans_type_id: Transaction type ID from API 5.5
          - origination_supplier: Province ID (1-9)
        
        Returns: [{ratE_ID, ratE_DESC, ratE_VALUE}, ...]
        """
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401
        
        # Get query parameters
        date = request.args.get("date", "")
        trans_type_id = request.args.get("trans_type_id", "")
        origination_supplier = request.args.get("origination_supplier", "1")  # Default to Punjab (1)
        
        # If no date provided, use current date in required format
        if not date:
            from datetime import datetime
            date = datetime.now().strftime("%d-%b-%Y")  # e.g., "24-Feb-2024"
        
        if not trans_type_id:
            return jsonify({"error": "trans_type_id parameter is required"}), 400
        
        cache_key = f"saletyperates_{client_id}_{date}_{trans_type_id}_{origination_supplier}"
        
        # Check cache
        cached_data = _get_cache(cache_key)
        if cached_data:
            return jsonify({"rates": cached_data, "source": "cache"})
        
        # Fetch from FBR API
        _, token = _get_client_token()
        if not token:
            return jsonify({"rates": [], "source": "fallback", "message": "API token not configured"})
        
        params = {
            "date": date,
            "transTypeId": trans_type_id,
            "originationSupplier": origination_supplier
        }
        
        data, error = _make_fbr_request("/pdi/v2/SaleTypeToRate", token, params)
        
        if error:
            print(f"[SaleTypeToRate API] Error: {error}")
            return jsonify({"rates": [], "source": "error", "error": error})
        
        if isinstance(data, list):
            _set_cache(cache_key, data, CACHE_DURATION_HS_UOM)  # Cache for 1 hour
            return jsonify({
                "rates": data, 
                "source": "api", 
                "count": len(data),
                "params": {"date": date, "trans_type_id": trans_type_id, "origination_supplier": origination_supplier}
            })
        
        return jsonify({"rates": [], "source": "api", "error": "Unexpected response format"})

    # -------------------- SRO Schedule API (5.7) --------------------
    @app.route("/api/reference/sro-schedule", methods=["GET"])
    def get_sro_schedule():
        """
        Fetch SRO schedules from FBR API 5.7.
        URL: https://gw.fbr.gov.pk/pdi/v1/SroSchedule?rate_id=413&date=04-Feb-2024&origination_supplier_csv=1
        
        Query params:
          - rate_id: Rate ID from SaleTypeToRate API
          - date: Date in DD-MMM-YYYY format
          - origination_supplier_csv: Province IDs (comma separated)
        
        Returns: [{srO_ID, srO_DESC}, ...]
        """
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401
        
        rate_id = request.args.get("rate_id", "")
        date = request.args.get("date", "")
        origination_supplier_csv = request.args.get("origination_supplier_csv", "1")
        
        if not rate_id:
            return jsonify({"error": "rate_id parameter is required"}), 400
        
        # If no date, use current date
        if not date:
            from datetime import datetime
            date = datetime.now().strftime("%d-%b-%Y")
        
        cache_key = f"sroschedule_{client_id}_{rate_id}_{date}_{origination_supplier_csv}"
        
        cached_data = _get_cache(cache_key)
        if cached_data:
            return jsonify({"sro_schedules": cached_data, "source": "cache"})
        
        _, token = _get_client_token()
        if not token:
            return jsonify({"sro_schedules": [], "source": "fallback", "message": "API token not configured"})
        
        params = {
            "rate_id": rate_id,
            "date": date,
            "origination_supplier_csv": origination_supplier_csv
        }
        
        data, error = _make_fbr_request("/pdi/v1/SroSchedule", token, params)
        
        if error:
            print(f"[SRO Schedule API] Error: {error}")
            return jsonify({"sro_schedules": [], "source": "error", "error": error})
        
        if isinstance(data, list):
            _set_cache(cache_key, data, CACHE_DURATION_HS_UOM)
            return jsonify({"sro_schedules": data, "source": "api", "count": len(data)})
        
        return jsonify({"sro_schedules": [], "source": "api", "error": "Unexpected response format"})

    # -------------------- SRO Item by SRO ID API (5.10) --------------------
    @app.route("/api/reference/sro-item-by-id", methods=["GET"])
    def get_sro_item_by_id():
        """
        Fetch SRO items by SRO ID from FBR API 5.10.
        URL: https://gw.fbr.gov.pk/pdi/v2/SROItem?date=2025-03-25&sro_id=389
        
        Query params:
          - date: Date in YYYY-MM-DD format
          - sro_id: SRO ID from SroSchedule API
        
        Returns: [{srO_ITEM_ID, srO_ITEM_DESC}, ...]
        """
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401
        
        date = request.args.get("date", "")
        sro_id = request.args.get("sro_id", "")
        
        if not sro_id:
            return jsonify({"error": "sro_id parameter is required"}), 400
        
        if not date:
            from datetime import datetime
            date = datetime.now().strftime("%Y-%m-%d")
        
        cache_key = f"sroitem_{client_id}_{date}_{sro_id}"
        
        cached_data = _get_cache(cache_key)
        if cached_data:
            return jsonify({"sro_items": cached_data, "source": "cache"})
        
        _, token = _get_client_token()
        if not token:
            return jsonify({"sro_items": [], "source": "fallback", "message": "API token not configured"})
        
        params = {
            "date": date,
            "sro_id": sro_id
        }
        
        data, error = _make_fbr_request("/pdi/v2/SROItem", token, params)
        
        if error:
            print(f"[SRO Item API] Error: {error}")
            return jsonify({"sro_items": [], "source": "error", "error": error})
        
        if isinstance(data, list):
            _set_cache(cache_key, data, CACHE_DURATION_HS_UOM)
            return jsonify({"sro_items": data, "source": "api", "count": len(data)})
        
        return jsonify({"sro_items": [], "source": "api", "error": "Unexpected response format"})

    # -------------------- Registration Type Lookup API (5.12) --------------------
    @app.route("/api/reference/registration-type", methods=["POST"])
    def get_registration_type():
        """
        Lookup registration type (Registered/Unregistered) by NTN/CNIC from FBR API 5.12.
        URL: https://gw.fbr.gov.pk/dist/v1/Get_Reg_Type
        
        NOTE: Per FBR Technical Doc v1.12, this API uses /dist/ path (not /pdi/).
        The doc says "HTTP meth Get" but shows JSON request body format.
        Based on the sample request format {"Registration_No":"0788762"}, this appears
        to be a POST request with JSON body.
        
        Request body: {"registration_no": "0788762"}
        Returns: {
            "statuscode": "00" (registered) or "01" (unregistered),
            "REGISTRATION_NO": "0788762",
            "REGISTRATION_TYPE": "Registered" or "unregistered"
        }
        """
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401
        
        data = request.get_json() or {}
        registration_no = (data.get("registration_no") or "").strip()
        
        if not registration_no:
            return jsonify({"error": "registration_no is required"}), 400
        
        # Clean up the registration number (remove dashes, spaces)
        registration_no_clean = registration_no.replace("-", "").replace(" ", "")
        
        print(f"\n{'='*60}")
        print(f"[Registration Type API] Looking up: {registration_no_clean}")
        
        # Check cache first
        cache_key = f"regtype_{registration_no_clean}"
        cached_data = _get_cache(cache_key)
        if cached_data:
            print(f"[Registration Type API] Cache hit for {registration_no_clean}")
            return jsonify({**cached_data, "source": "cache"})
        
        # Fetch from FBR API
        _, token = _get_client_token()
        if not token:
            return jsonify({
                "error": "API token not configured",
                "registration_no": registration_no,
                "registration_type": None,
                "source": "error"
            })
        
        # Per FBR Technical Doc v1.12 Section 5.12:
        # URL: https://gw.fbr.gov.pk/dist/v1/Get_Reg_Type
        # Sample Request: {"Registration_No":"0788762"}
        # NOTE: This is /dist/ path, different from /pdi/ reference APIs
        url = f"{FBR_SANDBOX_BASE}/dist/v1/Get_Reg_Type"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        
        # FBR uses exact field name "Registration_No" (with underscore and capital N)
        payload = {"Registration_No": registration_no_clean}
        
        print(f"[Registration Type API] Calling FBR: {url}")
        print(f"[Registration Type API] Payload: {payload}")
        print(f"[Registration Type API] Token (first 30): {token[:30] if token else 'None'}...")
        
        try:
            # Per documentation, this appears to be POST with JSON body
            # (sample shows JSON request format, not query params)
            response = requests.post(
                url, 
                headers=headers, 
                json=payload,
                timeout=30
            )
            print(f"[Registration Type API] POST Response status: {response.status_code}")
            print(f"[Registration Type API] POST Response body: {response.text[:500] if response.text else 'empty'}")
            
            response.raise_for_status()
            
            result = response.json()
            print(f"[Registration Type API] Result: {result}")
            
            # Normalize the response
            # statuscode "00" = Registered, "01" = Unregistered
            normalized = {
                "registration_no": result.get("REGISTRATION_NO") or registration_no,
                "registration_type": result.get("REGISTRATION_TYPE", "").lower(),
                "status_code": result.get("statuscode"),
                "is_registered": result.get("statuscode") == "00" or result.get("REGISTRATION_TYPE", "").lower() == "registered",
                "source": "api"
            }
            
            # Cache for 1 hour
            _set_cache(cache_key, normalized, CACHE_DURATION_REG_TYPE)
            
            return jsonify(normalized)
            
        except requests.Timeout:
            print("[Registration Type API] Timeout")
            return jsonify({
                "error": "FBR API request timed out",
                "registration_no": registration_no,
                "registration_type": None,
                "source": "error"
            })
        except requests.HTTPError as e:
            error_body = e.response.text[:300] if e.response and e.response.text else "No response body"
            print(f"[Registration Type API] HTTP Error: {e.response.status_code} - {error_body}")
            return jsonify({
                "error": f"FBR API error: {e.response.status_code}",
                "error_detail": error_body,
                "registration_no": registration_no,
                "registration_type": None,
                "source": "error"
            })
        except Exception as e:
            print(f"[Registration Type API] Error: {e}")
            import traceback
            traceback.print_exc()
            return jsonify({
                "error": str(e),
                "registration_no": registration_no,
                "registration_type": None,
                "source": "error"
            })

    # -------------------- STATL API - Check Active/Inactive Status (5.11) --------------------
    @app.route("/api/reference/statl", methods=["POST"])
    def get_statl_status():
        """
        Check if a registration is Active or Inactive from FBR API 5.11.
        URL: https://gw.fbr.gov.pk/dist/v1/statl
        
        Request body: {"regno": "0788762", "date": "2025-05-18"}
        Returns: {
            "status code": "01" or "02",
            "status": "In-Active"
        }
        """
        client_id = session.get("client_id")
        if not client_id:
            return jsonify({"error": "No client ID in session"}), 401
        
        data = request.get_json() or {}
        regno = (data.get("regno") or "").strip()
        date = data.get("date") or ""
        
        if not regno:
            return jsonify({"error": "regno is required"}), 400
        
        # Clean up the registration number
        regno_clean = regno.replace("-", "").replace(" ", "")
        
        # Use current date if not provided (format: YYYY-MM-DD)
        if not date:
            from datetime import datetime
            date = datetime.now().strftime("%Y-%m-%d")
        
        print(f"[STATL API] Checking status for: {regno_clean} on date: {date}")
        
        # Fetch from FBR API
        _, token = _get_client_token()
        if not token:
            return jsonify({
                "error": "API token not configured",
                "regno": regno,
                "status": None,
                "source": "error"
            })
        
        url = f"{FBR_SANDBOX_BASE}/dist/v1/statl"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        # Per FBR doc sample: {"regno":"0788762","date":"2025-05-18"}
        payload = {"regno": regno_clean, "date": date}
        
        print(f"[STATL API] Calling FBR: {url}")
        print(f"[STATL API] Payload: {payload}")
        
        try:
            # Per documentation, this uses POST with JSON body (sample shows JSON request)
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            print(f"[STATL API] POST Response status: {response.status_code}")
            print(f"[STATL API] Response body: {response.text[:500] if response.text else 'empty'}")
            response.raise_for_status()
            
            result = response.json()
            print(f"[STATL API] Result: {result}")
            
            # Normalize the response
            normalized = {
                "regno": regno,
                "status_code": result.get("status code") or result.get("statuscode"),
                "status": result.get("status"),
                "is_active": (result.get("status") or "").lower() != "in-active",
                "date": date,
                "source": "api"
            }
            
            return jsonify(normalized)
            
        except requests.Timeout:
            return jsonify({
                "error": "FBR API request timed out",
                "regno": regno,
                "status": None,
                "source": "error"
            })
        except requests.HTTPError as e:
            error_body = e.response.text[:300] if e.response and e.response.text else "No response body"
            print(f"[STATL API] HTTP Error: {e.response.status_code} - {error_body}")
            return jsonify({
                "error": f"FBR API error: {e.response.status_code}",
                "error_detail": error_body,
                "regno": regno,
                "status": None,
                "source": "error"
            })
        except Exception as e:
            print(f"[STATL API] Error: {e}")
            import traceback
            traceback.print_exc()
            return jsonify({
                "error": str(e),
                "regno": regno,
                "status": None,
                "source": "error"
            })
