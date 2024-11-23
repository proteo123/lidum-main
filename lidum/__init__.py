import logging
from os import makedirs
from typing import Any
from os.path import join
from collections.abc import Callable, Awaitable

from flask import Flask, has_app_context
from celery import Celery
from aiogram import Bot, Dispatcher, BaseMiddleware
from aiogram.types import Update
from flask_limiter import Limiter
from sqlalchemy.orm import sessionmaker, scoped_session
from flask_sqlalchemy import SQLAlchemy
from flask_limiter.util import get_remote_address
from cryptography.fernet import Fernet
from aiogram.dispatcher.router import Router
from aiogram.fsm.storage.redis import RedisStorage

from .config import LS_INDEX, BOT_TOKEN, LOGS_PATH
from .config import LS_RETRY_CNT, REDIS_ADDRESS
from .config import CONFIG_RETRY_CNT, FERNET_PRIVATE_KEY
from .config import RUN_METHOD_RETRY_CNT, Flask_Config
from .utils.ton_client import TonClient

_app = None
_session_factory = None
_Session = None

_lidum_logger = None
_bot_logger = None

db = SQLAlchemy()
fernet = Fernet(FERNET_PRIVATE_KEY)
limiter = Limiter(get_remote_address, storage_uri=REDIS_ADDRESS, default_limits=["5 per second"])

client = TonClient(is_testnet=Flask_Config.TESTNET,
                   ls_index=LS_INDEX,
                   ls_retry_cnt=LS_RETRY_CNT,
                   config_retry_cnt=CONFIG_RETRY_CNT,
                   run_method_retry_cnt=RUN_METHOD_RETRY_CNT,
                   verbose=True)


def create_logger(name: str, log_file: str, level=logging.INFO):
    """Настройка логгера."""

    makedirs(LOGS_PATH, exist_ok=True)

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    file_handler = logging.FileHandler(join(LOGS_PATH, log_file))
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger


def get_loggers():
    global _lidum_logger
    global _bot_logger

    _lidum_logger = create_logger("lidum", "lidum.log")
    _bot_logger = create_logger("bot", "bot.log")

    return _lidum_logger, _bot_logger


def create_session(app: Flask):

    with app.app_context():
        session_factory = sessionmaker(bind=db.engine)
        Session = scoped_session(session_factory)

    return session_factory, Session


def get_session(app: Flask):
    global _session_factory
    global _Session

    if _session_factory is None or _Session is None:
        _session_factory, _Session = create_session(app)

    return _session_factory, _Session


def create_bot(app: Flask):
    """Создает экземпляр Telegram-бота, работающий в контексте app."""

    bot = Bot(token=BOT_TOKEN)

    storage = RedisStorage.from_url(REDIS_ADDRESS)
    dp = Dispatcher(storage=storage)

    router = Router()
    dp.include_router(router)

    class AppContextMiddleware(BaseMiddleware):

        def __init__(self, app: Flask):
            self.app = app
            super().__init__()

        async def __call__(
            self,
            handler: Callable[[Update, dict[str, Any]], Awaitable[Any]],
            event: Update,
            data: dict[str, Any],
        ):
            with self.app.app_context():
                return await handler(event, data)

    dp.update.middleware(AppContextMiddleware(app))

    return bot, dp, router


def create_celery(app: Flask):
    """Создает экземпляр celery, работающий в контексте app."""

    celery = Celery(__name__)

    celery.conf.update({
        "broker_url": app.config["CELERY_BROKER_URL"],
        "result_backend": app.config["CELERY_RESULT_BACKEND"],
        "broker_connection_retry_on_startup": app.config["CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP"],
    })

    TaskBase = celery.Task

    class ContextTask(TaskBase):

        def __call__(self, *args, **kwargs):
            with app.app_context():
                return TaskBase.__call__(self, *args, **kwargs)

    celery.Task = ContextTask
    return celery


def get_app():
    """Возвращает текущий экземпляр Flask, либо инициализирует новый экземпляр."""
    global _app

    if _app is None:
        _app = create_app()

    if not has_app_context():
        with _app.app_context():
            return _app

    return _app


def create_app():
    """Создает экземпляр Flask и инициализирует необходимые части приложения."""

    app = Flask(__name__)
    app.config.from_object(Flask_Config)

    limiter.init_app(app)
    db.init_app(app)

    with app.app_context():
        db.create_all()

    return app
