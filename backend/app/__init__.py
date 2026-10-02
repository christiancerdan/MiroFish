"""
MiroFish Backend - Flask应用工厂
"""

import os
import warnings

# 抑制 multiprocessing resource_tracker 的警告（来自第三方库如 transformers）
# 需要在所有其他导入之前设置
warnings.filterwarnings("ignore", message=".*resource_tracker.*")

from flask import Flask, request, send_from_directory, abort, g

from .config import Config
from .utils.logger import setup_logger, get_logger
from .security import install_security


def create_app(config_class=Config):
    """Flask应用工厂函数"""
    app = Flask(__name__, static_folder=None)
    app.config.from_object(config_class)
    
    # 设置JSON编码：确保中文直接显示（而不是 \uXXXX 格式）
    # Flask >= 2.3 使用 app.json.ensure_ascii，旧版本使用 JSON_AS_ASCII 配置
    if hasattr(app, 'json') and hasattr(app.json, 'ensure_ascii'):
        app.json.ensure_ascii = False
    
    # 设置日志
    logger = setup_logger('mirofish')
    
    # 只在 reloader 子进程中打印启动信息（避免 debug 模式下打印两次）
    is_reloader_process = os.environ.get('WERKZEUG_RUN_MAIN') == 'true'
    debug_mode = app.config.get('DEBUG', False)
    should_log_startup = not debug_mode or is_reloader_process
    
    if should_log_startup:
        logger.info("=" * 50)
        logger.info("MiroFish Backend 启动中...")
        logger.info("=" * 50)
    
    install_security(app)
    
    # 注册模拟进程清理函数（确保服务器关闭时终止所有模拟进程）
    from .services.simulation_runner import SimulationRunner
    SimulationRunner.register_cleanup()
    if should_log_startup:
        logger.info("已注册模拟进程清理函数")
    
    # 请求日志中间件
    @app.before_request
    def log_request():
        logger = get_logger('mirofish.request')
        logger.debug("Request %s: %s %s", g.request_id, request.method, request.path)
    
    @app.after_request
    def log_response(response):
        logger = get_logger('mirofish.request')
        logger.debug("Request %s: response %s", g.request_id, response.status_code)
        return response
    
    # 注册蓝图
    from .api import graph_bp, simulation_bp, report_bp
    app.register_blueprint(graph_bp, url_prefix='/api/graph')
    app.register_blueprint(simulation_bp, url_prefix='/api/simulation')
    app.register_blueprint(report_bp, url_prefix='/api/report')
    
    # 健康检查
    @app.route('/health')
    def health():
        return {'status': 'ok', 'service': 'MiroFish Backend'}

    # Serve the compiled UI from the same origin in production. send_from_directory
    # performs path containment; API misses must never fall back to index.html.
    frontend_dist = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../frontend/dist'))

    @app.get('/')
    @app.get('/<path:path>')
    def frontend(path=''):
        if path.startswith('api/') or not os.path.isfile(os.path.join(frontend_dist, 'index.html')):
            abort(404)
        if path and os.path.isfile(os.path.join(frontend_dist, path)):
            return send_from_directory(frontend_dist, path)
        if path.startswith('assets/'):
            abort(404)
        return send_from_directory(frontend_dist, 'index.html')
    
    if should_log_startup:
        logger.info("MiroFish Backend 启动完成")
    
    return app
