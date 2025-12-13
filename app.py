import os
import time
import logging
import json
import hashlib
import sqlite3
from datetime import datetime
from threading import Lock
from flask import Flask, request, jsonify, render_template, send_from_directory, Response, stream_with_context
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from apscheduler.schedulers.background import BackgroundScheduler
import requests

from extensions import db
from models import SystemConfig, Token, RequestLog
import services

# Initialize App
app = Flask(__name__, static_folder='static', template_folder='static')
# NOTE: Flask-SQLAlchemy 会将相对 sqlite 路径自动解析到 app.instance_path 下；
# 默认使用 sqlite:///zai2api.db 即会落到 ./instance/zai2api.db（与仓库结构一致）。
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URI', 'sqlite:///zai2api.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your-secret-key-change-me')

# Initialize DB
db.init_app(app)

# Logging Setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Login Manager
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login_page'

class User(UserMixin):
    def __init__(self, id, username):
        self.id = id
        self.username = username

@login_manager.user_loader
def load_user(user_id):
    # We only have one admin user
    config = SystemConfig.query.first()
    if config and str(config.id) == user_id:
        return User(id=str(config.id), username=config.admin_username)
    return None

# --- SQLite schema migration (lightweight, no Alembic) ---

def _sqlite_path_from_uri(uri: str) -> str | None:
    if not uri:
        return None
    if uri.startswith('sqlite:///:memory:'):
        return None
    if uri.startswith('sqlite:///'):
        # Works for both relative (sqlite:///instance/db.sqlite) and absolute (sqlite:////app/instance/db.sqlite)
        return uri[len('sqlite:///'):]
    return None

def _sqlite_table_columns(cursor, table_name: str) -> set[str]:
    cursor.execute(f"PRAGMA table_info({table_name})")
    return {row[1] for row in cursor.fetchall()}

def migrate_sqlite_schema():
    # Prefer the *resolved* sqlite file path used by Flask-SQLAlchemy (usually under app.instance_path).
    path = None
    try:
        engine = db.engine  # requires app_context
        if getattr(engine.url, 'drivername', None) == 'sqlite':
            path = engine.url.database
    except Exception:
        path = None

    if not path:
        uri = app.config.get('SQLALCHEMY_DATABASE_URI')
        path = _sqlite_path_from_uri(uri)
    if not path:
        return

    # Ensure directory exists for relative sqlite paths
    dir_name = os.path.dirname(path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)

    conn = sqlite3.connect(path)
    cur = conn.cursor()
    try:
        # system_config: add missing columns safely
        sc_cols = _sqlite_table_columns(cur, 'system_config')
        if sc_cols:
            if 'error_retry_count' not in sc_cols:
                cur.execute("ALTER TABLE system_config ADD COLUMN error_retry_count INTEGER DEFAULT 3")
            if 'token_refresh_interval' not in sc_cols:
                cur.execute("ALTER TABLE system_config ADD COLUMN token_refresh_interval INTEGER DEFAULT 3600")
            if 'stream_conversion_enabled' not in sc_cols:
                cur.execute("ALTER TABLE system_config ADD COLUMN stream_conversion_enabled BOOLEAN DEFAULT 0")

        # request_log: add missing columns for UI display
        rl_cols = _sqlite_table_columns(cur, 'request_log')
        if rl_cols:
            if 'discord_token' not in rl_cols:
                cur.execute("ALTER TABLE request_log ADD COLUMN discord_token TEXT")
            if 'zai_token' not in rl_cols:
                cur.execute("ALTER TABLE request_log ADD COLUMN zai_token TEXT")

        conn.commit()
    finally:
        conn.close()

def _mask_token(value: str | None, head: int = 12, tail: int = 6) -> str | None:
    if not value:
        return None
    if len(value) <= head + tail:
        return value
    return f"{value[:head]}...{value[-tail:]}"

# Database Initialization
def init_db():
    with app.app_context():
        db.create_all()
        # Make sure old sqlite DBs get new columns before ORM queries start
        migrate_sqlite_schema()
        db.create_all()
        config = SystemConfig.query.first()
        if not config:
            # Default Admin: admin / admin
            # Use pbkdf2:sha256 which is default in generate_password_hash
            config = SystemConfig(
                admin_username='admin',
                admin_password_hash=generate_password_hash('admin')
            )
            db.session.add(config)
            db.session.commit()
            print("Initialized default admin/admin")

        # Ensure scheduler interval reflects persisted config (survives restart)
        try:
            seconds = int(getattr(config, 'token_refresh_interval', 3600) or 3600)
            scheduler.reschedule_job('token_refresher', trigger='interval', seconds=seconds)
        except Exception as e:
            logger.error(f"Failed to apply token_refresh_interval on startup: {e}")

# Scheduler
def scheduled_refresh():
    with app.app_context():
        services.refresh_all_tokens()

scheduler = BackgroundScheduler()
scheduler.add_job(scheduled_refresh, 'interval', seconds=3600, id='token_refresher')
scheduler.start()

# --- Routes: Pages ---

@app.route('/login')
def login_page():
    return send_from_directory('static', 'login.html')

@app.route('/manage')
def manage_page():
    return send_from_directory('static', 'manage.html')

@app.route('/')
def index():
    return send_from_directory('static', 'login.html')

# --- Routes: Auth API ---

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    
    config = SystemConfig.query.first()
    if config and config.admin_username == username and check_password_hash(config.admin_password_hash, password):
        user = User(id=str(config.id), username=config.admin_username)
        login_user(user)
        import jwt
        token = jwt.encode({'user_id': str(config.id), 'exp': datetime.utcnow().timestamp() + 86400}, app.config['SECRET_KEY'], algorithm='HS256')
        return jsonify({'success': True, 'token': token})
    
    return jsonify({'success': False, 'message': 'Invalid credentials'}), 401

# Middleware for Bearer Token Auth
def check_auth_token():
    auth_header = request.headers.get('Authorization')
    if auth_header and auth_header.startswith('Bearer '):
        token = auth_header.split(' ')[1]
        import jwt
        try:
            payload = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
            return payload.get('user_id')
        except:
            return None
    return None

# Wrapper for API routes requiring auth
def api_auth_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if request.method == 'OPTIONS':
            return f(*args, **kwargs)
        user_id = check_auth_token()
        if not user_id:
             return jsonify({'error': 'Unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated_function

# --- Routes: Admin API ---

@app.route('/api/stats', methods=['GET'])
@api_auth_required
def api_stats():
    total_tokens = Token.query.count()
    active_tokens = Token.query.filter_by(is_active=True).count()
    # Mocking today stats for now or deriving from logs if detailed
    total_images = Token.query.with_entities(db.func.sum(Token.image_count)).scalar() or 0
    total_videos = Token.query.with_entities(db.func.sum(Token.video_count)).scalar() or 0
    total_errors = Token.query.with_entities(db.func.sum(Token.error_count)).scalar() or 0
    
    return jsonify({
        'total_tokens': total_tokens,
        'active_tokens': active_tokens,
        'today_images': 0, # Implement daily stats if needed
        'total_images': total_images,
        'today_videos': 0,
        'total_videos': total_videos,
        'today_errors': 0,
        'total_errors': total_errors
    })

@app.route('/api/tokens', methods=['GET'])
@api_auth_required
def get_tokens():
    tokens = Token.query.all()
    result = []
    for t in tokens:
        result.append({
            'id': t.id,
            'email': t.email,
            'is_active': t.is_active,
            'at_expires': t.at_expires.isoformat() if t.at_expires else None,
            'credits': t.credits,
            'user_paygate_tier': t.user_paygate_tier,
            'current_project_name': t.current_project_name,
            'current_project_id': t.current_project_id,
            'image_count': t.image_count,
            'video_count': t.video_count,
            'error_count': t.error_count,
            'remark': t.remark,
            'image_enabled': t.image_enabled,
            'video_enabled': t.video_enabled,
            'image_concurrency': t.image_concurrency,
            'video_concurrency': t.video_concurrency,
            'zai_token': t.zai_token,
            'st': t.discord_token[:10] + '...' # Masked for security? Frontend uses it for edit.
            # Ideally return full ST for edit, or handle separately. Frontend calls edit and pre-fills ST.
            # Let's return full ST for now as admin panel.
        })
        # Add full ST if requested or for admin
        result[-1]['st'] = t.discord_token 
    return jsonify(result)

@app.route('/api/tokens', methods=['POST'])
@api_auth_required
def add_token():
    data = request.json
    st = data.get('st')
    if not st:
        return jsonify({'success': False, 'message': 'Missing Discord Token'}), 400
        
    token = Token(
        discord_token=st,
        remark=data.get('remark'),
        current_project_id=data.get('project_id'),
        current_project_name=data.get('project_name'),
        image_enabled=data.get('image_enabled', True),
        video_enabled=data.get('video_enabled', True),
        image_concurrency=data.get('image_concurrency', -1),
        video_concurrency=data.get('video_concurrency', -1)
    )
    db.session.add(token)
    db.session.commit()
    
    # Initial refresh
    success, msg = services.update_token_info(token.id)
    if not success:
        token.remark = f"Initial refresh failed: {msg}"
        db.session.commit()
        return jsonify({'success': True, 'message': 'Token added but refresh failed: ' + msg})
        
    return jsonify({'success': True})

@app.route('/api/tokens/<int:id>', methods=['PUT'])
@api_auth_required
def update_token(id):
    token = Token.query.get_or_404(id)
    data = request.json
    
    if 'st' in data: token.discord_token = data['st']
    if 'remark' in data: token.remark = data['remark']
    if 'project_id' in data: token.current_project_id = data['project_id']
    if 'project_name' in data: token.current_project_name = data['project_name']
    if 'image_enabled' in data: token.image_enabled = data['image_enabled']
    if 'video_enabled' in data: token.video_enabled = data['video_enabled']
    if 'image_concurrency' in data: token.image_concurrency = data['image_concurrency']
    if 'video_concurrency' in data: token.video_concurrency = data['video_concurrency']
    
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/tokens/<int:id>', methods=['DELETE'])
@api_auth_required
def delete_token(id):
    token = Token.query.get_or_404(id)
    db.session.delete(token)
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/tokens/refresh-all', methods=['POST'])
@api_auth_required
def refresh_all_tokens_endpoint():
    try:
        services.refresh_all_tokens(force=True)
        return jsonify({'success': True, 'message': '所有 Token 刷新请求已发送'})
    except Exception as e:
        logger.error(f"Manual refresh failed: {e}")
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/tokens/<int:id>/refresh-at', methods=['POST'])
@api_auth_required
def refresh_token_at(id):
    success, msg = services.update_token_info(id)
    if success:
        token = Token.query.get(id)
        return jsonify({'success': True, 'token': {'at_expires': token.at_expires.isoformat() if token.at_expires else None}})
    return jsonify({'success': False, 'detail': msg})

@app.route('/api/tokens/<int:id>/refresh-credits', methods=['POST'])
@api_auth_required
def refresh_token_credits(id):
    # This requires an API call to Zai using the AT
    # services.update_token_info gets the AT. We need another function to fetch credits.
    # For now, let's reuse update_token_info as it fetches account info if we implemented it fully.
    # But currently update_token_info only does login.
    # We need to implement credit fetching.
    # For now, stub it or just call update_token_info.
    success, msg = services.update_token_info(id)
    if success:
        token = Token.query.get(id)
        return jsonify({'success': True, 'credits': token.credits})
    return jsonify({'success': False, 'detail': msg})

@app.route('/api/tokens/st2at', methods=['POST'])
@api_auth_required
def st2at():
    data = request.json
    st = data.get('st')
    handler = services.get_zai_handler()
    result = handler.backend_login(st)
    if 'error' in result:
        return jsonify({'success': False, 'message': result['error']})
    return jsonify({'success': True, 'access_token': result.get('token')})

@app.route('/api/tokens/<int:id>/enable', methods=['POST'])
@api_auth_required
def enable_token(id):
    token = Token.query.get_or_404(id)
    token.is_active = True
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/tokens/<int:id>/disable', methods=['POST'])
@api_auth_required
def disable_token(id):
    token = Token.query.get_or_404(id)
    token.is_active = False
    db.session.commit()
    return jsonify({'success': True})

# --- Admin Config Routes ---

@app.route('/api/admin/config', methods=['GET', 'POST'])
@api_auth_required
def admin_config():
    config = SystemConfig.query.first()
    if request.method == 'GET':
        return jsonify({
            'error_ban_threshold': config.error_ban_threshold,
            'error_retry_count': config.error_retry_count,
            'admin_username': config.admin_username,
            'api_key': config.api_key,
            'debug_enabled': config.debug_enabled,
            'token_refresh_interval': config.token_refresh_interval,
            'stream_conversion_enabled': getattr(config, 'stream_conversion_enabled', False)
        })
    else:
        data = request.json
        if 'error_ban_threshold' in data: config.error_ban_threshold = data['error_ban_threshold']
        if 'error_retry_count' in data: config.error_retry_count = data['error_retry_count']
        if 'stream_conversion_enabled' in data: config.stream_conversion_enabled = bool(data['stream_conversion_enabled'])
        
        db.session.commit()
        return jsonify({'success': True})

@app.route('/api/admin/apikey', methods=['POST'])
@api_auth_required
def update_apikey():
    data = request.json
    new_key = data.get('new_api_key')
    if not new_key:
        return jsonify({'success': False, 'detail': 'API Key 不能为空'}), 400
        
    config = SystemConfig.query.first()
    config.api_key = new_key
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/admin/password', methods=['POST'])
@api_auth_required
def update_password():
    data = request.json
    username = data.get('username')
    old_password = data.get('old_password')
    new_password = data.get('new_password')
    
    config = SystemConfig.query.first()
    
    # Verify old password
    if config.admin_username != username or not check_password_hash(config.admin_password_hash, old_password):
        return jsonify({'success': False, 'detail': '旧密码错误'}), 400
        
    config.admin_password_hash = generate_password_hash(new_password)
    if username:
        config.admin_username = username
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/admin/debug', methods=['POST'])
@api_auth_required
def admin_debug():
    data = request.json
    config = SystemConfig.query.first()
    if 'enabled' in data: config.debug_enabled = data.get('enabled')
    if 'token_refresh_interval' in data: 
        config.token_refresh_interval = data.get('token_refresh_interval')
        try:
            scheduler.reschedule_job('token_refresher', trigger='interval', seconds=config.token_refresh_interval)
        except Exception as e:
            logger.error(f"Failed to reschedule job: {e}")
            
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/proxy/config', methods=['GET', 'POST'])
@api_auth_required
def proxy_config():
    config = SystemConfig.query.first()
    if request.method == 'GET':
        return jsonify({
            'proxy_enabled': config.proxy_enabled,
            'proxy_url': config.proxy_url
        })
    else:
        data = request.json
        if 'proxy_enabled' in data: config.proxy_enabled = data['proxy_enabled']
        if 'proxy_url' in data: config.proxy_url = data['proxy_url']
        db.session.commit()
        return jsonify({'success': True})

@app.route('/api/logs', methods=['GET'])
@api_auth_required
def get_logs():
    limit = request.args.get('limit', 100, type=int)
    logs = RequestLog.query.order_by(RequestLog.created_at.desc()).limit(limit).all()
    return jsonify([{
        'operation': l.operation,
        'token_email': l.token_email,
        'discord_token': getattr(l, 'discord_token', None),
        'zai_token': getattr(l, 'zai_token', None),
        'status_code': l.status_code,
        'duration': l.duration,
        'created_at': l.created_at.isoformat()
    } for l in logs])

@app.route('/api/cache/config', methods=['GET', 'POST'])
@api_auth_required
def cache_config():
    config = SystemConfig.query.first()
    if request.method == 'GET':
        return jsonify({'success': True, 'config': {
            'enabled': config.cache_enabled,
            'timeout': config.cache_timeout,
            'base_url': config.cache_base_url,
            'effective_base_url': config.cache_base_url or request.host_url
        }})
    else:
        # The frontend calls separate endpoints for enabled/timeout/base-url
        data = request.json
        if 'timeout' in data: config.cache_timeout = data['timeout']
        db.session.commit()
        return jsonify({'success': True})

@app.route('/api/cache/enabled', methods=['POST'])
@api_auth_required
def cache_enabled():
    data = request.json
    config = SystemConfig.query.first()
    config.cache_enabled = data.get('enabled')
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/cache/base-url', methods=['POST'])
@api_auth_required
def cache_base_url():
    data = request.json
    config = SystemConfig.query.first()
    config.cache_base_url = data.get('base_url')
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/generation/timeout', methods=['GET', 'POST'])
@api_auth_required
def generation_timeout():
    config = SystemConfig.query.first()
    if request.method == 'GET':
         return jsonify({'success': True, 'config': {
            'image_timeout': config.image_timeout,
            'video_timeout': config.video_timeout
        }})
    else:
        data = request.json
        config.image_timeout = data.get('image_timeout')
        config.video_timeout = data.get('video_timeout')
        db.session.commit()
        return jsonify({'success': True})

@app.route('/api/token-refresh/config', methods=['GET'])
@api_auth_required
def token_refresh_config():
    config = SystemConfig.query.first()
    return jsonify({'success': True, 'config': {
        'at_auto_refresh_enabled': config.at_auto_refresh_enabled
    }})
    
@app.route('/api/token-refresh/enabled', methods=['POST'])
@api_auth_required
def token_refresh_enabled():
    data = request.json
    config = SystemConfig.query.first()
    config.at_auto_refresh_enabled = data.get('enabled')
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/tokens/import', methods=['POST'])
@api_auth_required
def import_tokens():
    data = request.json
    tokens_data = data.get('tokens', [])
    added = 0
    updated = 0
    
    for t_data in tokens_data:
        st = t_data.get('session_token')
        if not st: continue
        
        token = Token.query.filter_by(discord_token=st).first()
        if token:
            # Update
            token.email = t_data.get('email', token.email)
            token.zai_token = t_data.get('access_token', token.zai_token)
            token.is_active = t_data.get('is_active', token.is_active)
            token.image_enabled = t_data.get('image_enabled', True)
            token.video_enabled = t_data.get('video_enabled', True)
            updated += 1
        else:
            # Add
            token = Token(
                discord_token=st,
                email=t_data.get('email'),
                zai_token=t_data.get('access_token'),
                is_active=t_data.get('is_active', True),
                image_enabled=t_data.get('image_enabled', True),
                video_enabled=t_data.get('video_enabled', True),
                image_concurrency=t_data.get('image_concurrency', -1),
                video_concurrency=t_data.get('video_concurrency', -1)
            )
            db.session.add(token)
            added += 1
            
    db.session.commit()
    return jsonify({'success': True, 'added': added, 'updated': updated})

@app.route('/api/tokens/<int:id>/test', methods=['POST'])
@api_auth_required
def test_token(id):
    # Test by refreshing
    success, msg = services.update_token_info(id)
    token = Token.query.get(id)
    if success:
        return jsonify({
            'success': True, 
            'status': 'success', 
            'email': token.email,
            'sora2_supported': token.sora2_supported,
            'sora2_total_count': token.sora2_total_count,
            'sora2_redeemed_count': token.sora2_redeemed_count,
            'sora2_remaining_count': token.sora2_remaining_count
        })
    return jsonify({'success': False, 'message': msg})

@app.route('/api/tokens/<int:id>/sora2/activate', methods=['POST'])
@api_auth_required
def activate_sora2(id):
    # Not supported by zai_token.py yet
    return jsonify({'success': False, 'message': 'Not implemented in backend'})

# --- OpenAI Compatible Proxy ---

_rr_lock = Lock()
_rr_index = 0

def _get_token_candidates():
    """多号轮询：每个请求从上一次的下一个 token 开始顺序尝试。"""
    global _rr_index
    tokens = Token.query.filter_by(is_active=True).order_by(Token.id.asc()).all()
    valid_tokens = [t for t in tokens if t.zai_token and not str(t.zai_token).startswith('SESSION')]
    if not valid_tokens:
        return []
    with _rr_lock:
        start = _rr_index % len(valid_tokens)
        _rr_index = (start + 1) % len(valid_tokens)
    return valid_tokens[start:] + valid_tokens[:start]

def _mark_token_error(token: Token, config: SystemConfig, reason: str):
    token.error_count = int(token.error_count or 0) + 1
    token.remark = (reason or '')[:1000]
    threshold = int(getattr(config, 'error_ban_threshold', 3) or 3)
    if token.error_count >= threshold:
        token.is_active = False
        token.remark = f"Auto-banned due to errors: {(reason or '')[:950]}"
    db.session.commit()

def _mark_token_success(token: Token):
    if token.error_count:
        token.error_count = 0
    db.session.commit()

def _filter_stream_headers(hdrs):
    out = {}
    for k in ('Content-Type', 'Cache-Control'):
        if k in hdrs:
            out[k] = hdrs[k]
    out.setdefault('Content-Type', 'text/event-stream')
    return out

def _aggregate_sse_to_nonstream(resp, fallback_model: str | None = None):
    first_chunk = None
    usage = None
    role_by_index: dict[int, str] = {}
    content_by_index: dict[int, list[str]] = {}
    finish_by_index: dict[int, str] = {}

    for line in resp.iter_lines(decode_unicode=True):
        if not line:
            continue
        if not line.startswith('data:'):
            continue
        data = line[5:].strip()
        if not data:
            continue
        if data == '[DONE]':
            break
        try:
            chunk = json.loads(data)
        except Exception:
            continue
        if first_chunk is None:
            first_chunk = chunk
        if isinstance(chunk, dict) and chunk.get('usage'):
            usage = chunk.get('usage')
        for choice in (chunk.get('choices') or []):
            idx = int(choice.get('index', 0))
            delta = choice.get('delta') or {}
            if delta.get('role'):
                role_by_index[idx] = delta['role']
            if 'content' in delta and delta['content'] is not None:
                content_by_index.setdefault(idx, []).append(delta['content'])
            if choice.get('finish_reason') is not None:
                finish_by_index[idx] = choice.get('finish_reason')

    created = (first_chunk or {}).get('created') or int(time.time())
    model = (first_chunk or {}).get('model') or fallback_model or 'unknown'
    rid = (first_chunk or {}).get('id') or f"chatcmpl-{int(time.time()*1000)}"

    indexes = sorted(content_by_index.keys()) if content_by_index else [0]
    choices_out = []
    for idx in indexes:
        choices_out.append({
            'index': idx,
            'message': {
                'role': role_by_index.get(idx, 'assistant'),
                'content': ''.join(content_by_index.get(idx, []))
            },
            'finish_reason': finish_by_index.get(idx, 'stop')
        })

    out = {
        'id': rid,
        'object': 'chat.completion',
        'created': created,
        'model': model,
        'choices': choices_out
    }
    if usage is not None:
        out['usage'] = usage
    return out

@app.route('/v1/chat/completions', methods=['POST'])
def proxy_chat_completions():
    start_time = time.time()
    
    # Verify API Key
    config = SystemConfig.query.first()
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer ') or auth_header.split(' ')[1] != config.api_key:
         return jsonify({'error': 'Invalid API Key'}), 401

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({'error': 'Invalid JSON body'}), 400

    client_stream = bool(payload.get('stream'))
    stream_conversion_enabled = bool(getattr(config, 'stream_conversion_enabled', False))
    should_convert = (not client_stream) and stream_conversion_enabled
    zai_stream = client_stream or should_convert

    candidates = _get_token_candidates()
    if not candidates:
        return jsonify({'error': 'No active tokens available'}), 503

    max_attempts = max(1, int(getattr(config, 'error_retry_count', 1) or 1))
    attempts = 0
    last_response = None

    for token in candidates:
        if attempts >= max_attempts:
            break
        attempts += 1

        zai_url = "https://zai.is/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {token.zai_token}",
            "Content-Type": "application/json"
        }

        zai_payload = dict(payload)
        if zai_stream:
            zai_payload['stream'] = True

        try:
            resp = requests.post(zai_url, json=zai_payload, headers=headers, stream=zai_stream, timeout=600)
        except Exception as e:
            _mark_token_error(token, config, f"Request error: {e}")
            last_response = jsonify({'error': str(e)})
            last_response.status_code = 502
            continue

        # Log request (UI 展示用，写入脱敏 token)
        duration = time.time() - start_time
        log = RequestLog(
            operation="chat/completions",
            token_email=token.email,
            discord_token=_mask_token(token.discord_token),
            zai_token=_mask_token(token.zai_token),
            status_code=resp.status_code,
            duration=duration
        )
        db.session.add(log)
        db.session.commit()

        if resp.status_code >= 400:
            try:
                detail = resp.text
            except Exception:
                detail = ''
            _mark_token_error(token, config, f"HTTP {resp.status_code}: {detail[:200]}")
            last_response = Response(resp.content, status=resp.status_code, mimetype=resp.headers.get('Content-Type', 'application/json'))
            continue

        _mark_token_success(token)

        if client_stream:
            def generate():
                for chunk in resp.iter_content(chunk_size=1024):
                    if chunk:
                        yield chunk
            return Response(stream_with_context(generate()), status=resp.status_code, headers=_filter_stream_headers(resp.headers))

        if should_convert:
            aggregated = _aggregate_sse_to_nonstream(resp, fallback_model=payload.get('model'))
            return jsonify(aggregated)

        return Response(resp.content, status=resp.status_code, mimetype=resp.headers.get('Content-Type', 'application/json'))

    if last_response is not None:
        return last_response
    return jsonify({'error': 'No active tokens available'}), 503

@app.route('/v1/models', methods=['GET'])
def proxy_models():
    # Proxy or return static
    # Verify API Key
    config = SystemConfig.query.first()
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer ') or auth_header.split(' ')[1] != config.api_key:
         return jsonify({'error': 'Invalid API Key'}), 401

    start_time = time.time()

    candidates = _get_token_candidates()
    if not candidates: # If no token, maybe we can't fetch models? Or just return default list.
        # Fallback list
        return jsonify({
            "object": "list",
            "data": [
                {"id": "gpt-4", "object": "model", "created": 1687882411, "owned_by": "openai"},
                {"id": "gpt-3.5-turbo", "object": "model", "created": 1677610602, "owned_by": "openai"}
            ]
        })

    max_attempts = max(1, int(getattr(config, 'error_retry_count', 1) or 1))
    attempts = 0
    last_response = None

    for token in candidates:
        if attempts >= max_attempts:
            break
        attempts += 1

        zai_url = "https://zai.is/api/v1/models"
        headers = {"Authorization": f"Bearer {token.zai_token}"}

        try:
            resp = requests.get(zai_url, headers=headers, timeout=60)
        except Exception as e:
            _mark_token_error(token, config, f"Request error: {e}")
            last_response = jsonify({"error": "Failed to fetch models", "detail": str(e)})
            last_response.status_code = 502
            continue

        duration = time.time() - start_time
        log = RequestLog(
            operation="models",
            token_email=token.email,
            discord_token=_mask_token(token.discord_token),
            zai_token=_mask_token(token.zai_token),
            status_code=resp.status_code,
            duration=duration
        )
        db.session.add(log)
        db.session.commit()

        if resp.status_code >= 400:
            try:
                detail = resp.text
            except Exception:
                detail = ''
            _mark_token_error(token, config, f"HTTP {resp.status_code}: {detail[:200]}")
            last_response = Response(resp.content, status=resp.status_code, mimetype=resp.headers.get('Content-Type', 'application/json'))
            continue

        _mark_token_success(token)
        return Response(resp.content, status=resp.status_code, mimetype='application/json')

    if last_response is not None:
        return last_response
    return jsonify({"error": "Failed to fetch models"}), 500

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False) # use_reloader=False for scheduler
