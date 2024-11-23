import os
import json
from io import BytesIO
from os.path import join
from datetime import datetime, timezone

import requests
from flask import jsonify, request, send_file
from pydantic import ValidationError

from . import client, get_app, get_loggers, get_session
from .tasks import nft_mint, collection_mint
from .tasks import process_transaction
from .utils import return_codes
from .config import BOT_TOKEN
from .utils.db import Drop, Event, Author, Transaction
from .utils.db import Telegram_User, Subscriber_Event
from .utils.db import Subscriber_Channel, event_by_id
from .utils.db import tg_user_by_id, author_by_tg_id
from .utils.db import transaction_by_id, add_database_entries
from .utils.db import subscriber_visited_channels
from .utils.db import subscriber_participated_events
from .utils.hash import sha256_hash
from .utils.path import get_nft_image_path
from .utils.path import get_collection_metadata_path
from .utils.image import save_base64_image, decode_base64_image
from .utils.price import get_drop_price, get_event_price
from .utils.crypto import decrypt, encrypt
from .utils.wallet import LIDUM_WALLET_ADDRESS
from .utils.channel import get_channel_avatar
from .utils.convert import to_json_ext, link_to_username
from .utils.metadata import create_metadata
from .utils.password import compare_passwords
from .utils.nft_generation import get_random_nft
from .utils.request_bodies import SendNFTParams, GetPriceParams
from .utils.request_bodies import MakePostParams
from .utils.request_bodies import UserInfoParams
from .utils.request_bodies import EventInfoParams
from .utils.request_bodies import AuthorInfoParams
from .utils.request_bodies import CreateDropParams
from .utils.request_bodies import CreateEventParams
from .utils.request_bodies import DropperPriceParams
from .utils.request_bodies import ChannelAvatarParams
from .utils.request_bodies import CheckPasswordParams
from .utils.request_bodies import AddTransactionParams
from .utils.request_bodies import IsUserSubscribedParams
from .utils.request_bodies import AddVisitedChannelParams
from .utils.request_bodies import TransactionStatusParams

app = get_app()
Session = get_session(app)[1]
logger = get_loggers()[0]


@app.route("/api/dropper_price/", methods=["POST"])
async def dropper_price():
    """Возвращает цену за перевод указанного количества NFT на нулевой адрес."""

    params = DropperPriceParams(**request.get_json())

    nfts_cnt = params.nfts_cnt

    # Вычисление комиссии
    try:
        price = get_drop_price(nfts_cnt)

    except Exception as e:
        description = f"Error when trying to calculate the price: {e}"
        logger.error(description)
        return jsonify({"status": return_codes.PRICE_ERROR, "description": description}), 500

    return jsonify({"status": return_codes.SUCCESS, "price": price}), 200


@app.route("/api/create_drop/", methods=["POST"])
async def create_drop():
    """Создание нового события на сжигание NFT."""

    params = CreateDropParams(**request.get_json())

    telegram_id = params.telegram_id
    start_date = params.start_date
    end_date = params.end_date
    prizes = params.prizes
    price = params.price

    session = Session()

    # Подготовка записи о новом дропе
    try:
        new_drop = Drop(
            telegram_id=telegram_id,
            start_date=start_date,
            end_date=end_date,
            prizes=prizes,
            price=price,
        )

        add_database_entries(entries=new_drop, session=session)

    except Exception as e:
        description = f"Error when trying to prepare an entry about a new drop: {e}"
        logger.error(description)
        return jsonify({"status": return_codes.DB_WRITING_ERROR, "description": description}), 500

    return jsonify({"status": return_codes.SUCCESS, "drop_id": new_drop.drop_id}), 200


@app.route("/api/channel_avatar/", methods=["POST"])
async def channel_avatar():
    """Обработчик запроса на получение аватара телеграм-канала."""

    params = ChannelAvatarParams(**request.get_json())

    url = params.channel_url

    try:
        avatar = get_channel_avatar(url)

        if avatar is None:
            description = f"The channel {url} not found"
            logger.error(description)
            return jsonify({"status": return_codes.NOT_FOUND, "description": description}), 500

    except Exception as e:
        description = f"An error occurred while trying to get the channel's avatar: {e}"
        logger.error(description)
        return jsonify({"status": return_codes.AVATAR_READING_ERROR, "description": description}), 500

    return jsonify({"status": return_codes.SUCCESS, "url": avatar}), 200


@app.route("/api/check_password/", methods=["POST"])
async def check_password():
    """Проверка введенного пользователем пароля."""

    params = CheckPasswordParams(**request.get_json())

    event_id = params.event_id
    password = params.password

    session = Session()

    # Поиск события в базе данных
    try:
        event_id = int(decrypt(event_id))
        event = event_by_id(event_id=event_id, session=session)

        if event is None:
            description = f"Event with id = {event_id} was not found"
            logger.error(description)
            return jsonify({"status": return_codes.NOT_FOUND, "description": description}), 404

    except Exception as e:
        description = f"Error when trying to get data from the database: {e}"
        logger.error(description)
        return jsonify({"status": return_codes.DB_READING_ERROR, "description": description}), 500

    # Сравнение паролей
    try:
        res = compare_passwords(cur_password=password, event_password=event.password)

    except Exception as e:
        description = f"Error when trying to compare passwords: {e}"
        logger.error(description)
        return jsonify({"status": return_codes.PASSWORD_ERROR, "description": description}), 500

    return jsonify({"status": return_codes.SUCCESS, "is_equal": res}), 200


@app.route("/api/event_info/", methods=["POST"])
async def event_info():
    """Возвращает данные о событии с указанным id."""

    params = EventInfoParams(**request.get_json())

    event_id = params.event_id

    session = Session()

    # Попытка получить данные события из БД
    try:
        event_id = int(decrypt(event_id))
        event = event_by_id(event_id=event_id, session=session)

        if event is None:
            description = f"Event with id = {event_id} was not found"
            logger.error(description)
            return jsonify({"status": return_codes.NOT_FOUND, "description": description}), 404

        telegram_id = event.telegram_id
        collection_name = author_by_tg_id(telegram_id=telegram_id, session=session).collection_name

        event_info = {
            "start_date": event.start_date,
            "end_date": event.end_date,
            "invites": event.invites,
            "subscriptions": event.subscriptions,
            "minted_nfts": event.minted_nfts,
            "nfts_cnt": event.nfts_cnt,
            "image_name": event.image_name,
            "logo_url": get_nft_image_path(collection_name, telegram_id, event.image_name, True),
            "collection_name": collection_name,
            "event_name": event.event_name,
            "description": event.event_description,
            "transaction_id": event.transaction_id,
            "empty_password": event.password == sha256_hash(""),
            "user_timezone": event.user_timezone,
        }

    except Exception as e:
        description = f"An error occurred while getting information about the event: {e}"
        logger.error(description)
        return jsonify({"status": return_codes.DB_READING_ERROR, "description": description}), 500

    return jsonify({"status": return_codes.SUCCESS, "event_info": event_info}), 200


@app.route("/api/add_visited_channel/", methods=["POST"])
async def add_visited_channel():
    """Добавляет в список посещенных каналов пользователя указанный канал."""

    params = AddVisitedChannelParams(**request.get_json())

    telegram_id = params.telegram_id
    channel = params.channel

    session = Session()

    # Запись посещенного канала в базу данных
    try:
        visited_channels = subscriber_visited_channels(telegram_id=telegram_id, session=session)

        channel = link_to_username(channel)

        if channel not in visited_channels:

            new_channel = Subscriber_Channel(
                telegram_id=telegram_id,
                visited_channel=channel,
            )

            add_database_entries(entries=new_channel, session=session)

    except Exception as e:
        description = f"Error when trying to record a visited channel to a user with id {telegram_id}"
        logger.error(f"{description}: {e}")
        return jsonify({"status": return_codes.DB_WRITING_ERROR, "description": description}), 500

    return jsonify({"status": return_codes.SUCCESS}), 200


@app.route("/api/is_user_subscribed/", methods=["POST"])
async def is_user_subscribed():

    params = IsUserSubscribedParams(**request.get_json())

    telegram_id = params.telegram_id
    channel = params.channel
    channel = link_to_username(channel)

    try:
        response = requests.post(
            url=f"https://api.telegram.org/bot{BOT_TOKEN}/getChatMember",
            params={
                "chat_id": channel,
                "user_id": telegram_id
            },
        )
        if response.status_code != 200:
            #Если бот не является админом канала, то в response.description должно быть "Bad Request: member list is inaccessible"
            description = f"Error at requesting Telegram API: {response.json()['description']}"
            return jsonify({"status": return_codes.BOT_ERROR, "description": description}), response.status_code

        data = response.json()

        if not data.get("ok"):
            description = "Unknown error"
            return jsonify({"status": return_codes.BOT_ERROR, "description": data.get("description", description)}), 400

        member_status = data["result"]["status"]

        # Проверяем, является ли пользователь участником канала
        if member_status in ["member", "administrator", "creator"]:
            return jsonify({"status": return_codes.SUCCESS, "subscribed": True}), 200
        else:
            return jsonify({"status": return_codes.SUCCESS, "subscribed": False}), 200

    except Exception as e:
        description = "An error occurred when getting chat member via bot"
        app.logger.error(f"{description}: {e}")
        return jsonify({"status": return_codes.VALIDATE_ERROR, "description": description}), 500


@app.route("/api/user_info/", methods=["POST"])
async def user_info():
    """Возвращает данные из базы данных о пользователе."""

    params = UserInfoParams(**request.get_json())

    telegram_id = params.telegram_id
    username = params.username
    event_id = params.event_id

    session = Session()

    # Поиск тг-пользователя в базе данных
    try:
        tg_user = tg_user_by_id(telegram_id=telegram_id, session=session)

        if tg_user is None:
            new_tg_user = Telegram_User(
                id=telegram_id,
                username=username,
            )

            add_database_entries(entries=new_tg_user, session=session)

        else:
            tg_user.last_enter = datetime.now(timezone.utc)
            tg_user.username = username

            session.commit()

    except Exception as e:
        description = f"Error when trying to add a new user with id {telegram_id} to the database"
        logger.error(f"{description}: {e}")
        return jsonify({"status": return_codes.DB_WRITING_ERROR, "description": description}), 500

    # Поиск посещенных каналов в базе данных
    try:
        channels = subscriber_visited_channels(telegram_id=telegram_id, session=session)

    except Exception as e:
        description = f"An error occurred when trying to find a list of visited channels by a user with id {telegram_id}"
        logger.error(f"{description}: {e}")
        return jsonify({"status": return_codes.DB_READING_ERROR, "description": description}), 500

    # Поиск событий, в которых участвовал пользователь
    try:
        participated_events = subscriber_participated_events(telegram_id=telegram_id, session=session)

    except Exception as e:
        description = ("An error occurred when searching for a list of events"
                       f"in which a user with id {telegram_id} participated.")
        logger.error(f"{description}: {e}")
        return jsonify({"status": return_codes.DB_WRITING_ERROR, "description": description}), 500

    try:
        event_id = int(decrypt(event_id))

        user_info = {
            "visited_channels": channels,
            "participated": event_id in participated_events,
        }

    except Exception as e:
        description = f"An error occurred while getting information about the user with id {telegram_id}"
        logger.error(f"{description}: {e}")
        return jsonify({"status": return_codes.DB_READING_ERROR, "description": description}), 500

    return jsonify({"status": return_codes.SUCCESS, "user_info": user_info}), 200


@app.route("/api/get_price/", methods=["POST"])
async def get_minter_price():
    """Возвращает рассчитанную стоимость минта коллекции."""

    params = GetPriceParams(**request.get_json())

    telegram_id = params.telegram_id
    collection_images_cnt = params.collection_images_cnt

    session = Session()

    try:
        author = author_by_tg_id(telegram_id=telegram_id, session=session)

        price = get_event_price(
            nfts_cnt=int(collection_images_cnt),
            is_new=author is None,
        )

    except Exception as e:
        description = "Error when trying to calculate the price"
        logger.error(f"{description}: {e}")
        return jsonify({"status": return_codes.PRICE_ERROR, "description": description}), 500

    return jsonify({"status": return_codes.SUCCESS, "price": price}), 200


@app.route("/api/random_nft/", methods=["GET"])
async def get_rnd_image():
    """Возвращает NFT из случайной комбинации слоёв."""

    try:
        nft = get_random_nft()
        nft_io = BytesIO()
        nft.save(nft_io, "PNG")
        nft_io.seek(0)

    except Exception as e:
        description = f"Error when trying to mix layers: {e}"
        logger.error(description)
        return (
            jsonify({
                "status": return_codes.NFT_GENERATING_ERROR,
                "description": description,
            }),
            500,
        )

    return send_file(nft_io, mimetype="image/png")


@app.route("/api/add_transaction/", methods=["POST"])
async def minter_transaction():
    """Записывает новую транзакцию после создания события в базу данных."""

    params = AddTransactionParams(**request.get_json())

    transaction_hash = params.transaction_hash
    # wallet_address = params.wallet_address
    # amount = params.amount
    event_id = params.event_id

    session = Session()

    try:
        event_id = int(decrypt(event_id))
        event = event_by_id(event_id=event_id, session=session)

        if event is None:
            description = f"The event with id {event_id} was not found"
            logger.error(description)
            return jsonify({"status": return_codes.NOT_FOUND, "description": description}), 404

        transaction_id = event.transaction_id

    except Exception as e:
        description = f"Error when trying to write a transaction to the database: {e}"
        logger.error(description)
        return jsonify({"status": return_codes.DB_WRITING_ERROR, "description": description}), 500

    try:
        transaction = transaction_by_id(transaction_id=transaction_id, session=session)

        if transaction is None:
            description = f"The transaction with id {transaction_id} was not found"
            logger.error(description)
            return jsonify({"status": return_codes.NOT_FOUND, "description": description}), 404

        transaction.hash = transaction_hash
        session.commit()

    except Exception as e:
        description = "Error when trying to write a transaction to the database"
        logger.error(f"{description}: {e}")
        return jsonify({"status": return_codes.DB_WRITING_ERROR, "description": description}), 500

    try:
        process_transaction.delay(transaction_id)

    except Exception as e:
        description = "Error when trying to add a transaction to the processing queue"
        logger.error(f"{description}: {e}")
        return jsonify({"status": return_codes.QUEUE_ERROR, "description": description}), 500

    return jsonify({"status": return_codes.SUCCESS, "transaction_id": transaction.id}), 200


@app.route("/api/transaction_status/", methods=["POST"])
async def transaction_status():
    """Возвращает статус транзации из базы данных."""

    params = TransactionStatusParams(**request.get_json())

    transaction_id = params.transaction_id

    session = Session()

    # Попытка поиска в базе данных
    try:
        transaction = transaction_by_id(transaction_id=transaction_id, session=session)

        if transaction is None:
            description = f"No transaction with id {transaction_id} was found"
            logger.error(description)
            return jsonify({"status": return_codes.NOT_FOUND, "description": description}), 404

        status = transaction.status

    except Exception as e:
        description = f"Error when trying to find a transaction with id {transaction_id}"
        logger.error(f"{description}: {e}")
        return jsonify({"status": return_codes.DB_READING_ERROR, "description": description}), 404

    return jsonify({"status": return_codes.SUCCESS, "transaction_status": status}), 200


@app.route("/api/author_info/", methods=["POST"])
async def author_info():
    """Возвращает информацию об авторе."""

    params = AuthorInfoParams(**request.get_json())

    telegram_id = params.telegram_id
    username = params.username

    session = Session()

    # Поиск тг-пользователя в базе данных
    try:
        tg_user = tg_user_by_id(telegram_id=telegram_id, session=session)

        if tg_user is None:
            new_tg_user = Telegram_User(
                id=telegram_id,
                username=username,
            )

            add_database_entries(entries=new_tg_user, session=session)

        else:
            tg_user.last_enter = datetime.now(timezone.utc)
            tg_user.username = username

            session.commit()

    except Exception as e:
        description = f"Error when trying to add a new user with id {telegram_id} to the database"
        logger.error(f"{description}: {e}")
        return jsonify({"status": return_codes.DB_WRITING_ERROR, "description": description}), 500

    # Поиск пользователя в базе данных
    try:
        author = author_by_tg_id(telegram_id=telegram_id, session=session)

        if author is None:
            description = f"Author with id {telegram_id} was not found"
            logger.error(description)
            return jsonify({"status": return_codes.NOT_FOUND, "description": description}), 404

        author_info = {
            "collection_name": author.collection_name,
            "collection_address": author.collection_address,
        }

    except Exception as e:
        description = "An error occurred while getting information about the author"
        logger.error(f"{description}: {e}")
        return jsonify({"status": return_codes.DB_READING_ERROR, "description": description}), 500

    return jsonify({"status": return_codes.SUCCESS, "author_info": author_info}), 200


@app.route("/api/make_post/", methods=["POST"])
async def make_post():
    """Отправялет QR-код и сообщение поста на телеграм-бота."""

    params = MakePostParams(**request.get_json())

    qrcode = params.qrcode
    description = params.description
    button = params.button
    button_url = params.button_url
    telegram_id = params.telegram_id

    try:
        qrcode = decode_base64_image(qrcode)
        keyboard = {"inline_keyboard": [[{"text": button, "url": button_url}]]}

    except Exception as e:
        description = "An error occurred while decoding the QR code"
        logger.error(f"{description}: {e}")
        return jsonify({"status": return_codes.SERVER_ERROR, "description": description}), 500

    try:
        requests.post(
            url=f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
            params={"chat_id": telegram_id},
            files={"photo": BytesIO(qrcode)},
        )

    except Exception as e:
        description = "An error occurred when sending a QR-code to the bot"
        logger.error(f"{description}: {e}")
        return jsonify({"status": return_codes.BOT_ERROR, "description": description}), 500

    try:
        requests.post(
            url=f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            params={
                "chat_id": telegram_id,
                "text": description,
                "reply_markup": json.dumps(keyboard),
            },
        )

    except Exception as e:
        description = "An error occurred when sending a message to the bot"
        logger.error(f"{description}: {e}")
        return jsonify({"status": return_codes.BOT_ERROR, "description": description}), 500

    return jsonify({"status": return_codes.SUCCESS}), 200


@app.route("/api/get_wallet/", methods=["GET"])
async def get_wallet():
    """Возвращает адрес кошелька приложения."""

    return jsonify({"status": return_codes.SUCCESS, "wallet": LIDUM_WALLET_ADDRESS}), 200


@app.route("/api/create_event/", methods=["POST"])
async def create_event():
    """Запись данных о новом событии и минт пустой коллекции."""

    params = CreateEventParams(**request.get_json())

    telegram_id = params.telegram_id
    wallet_address = params.wallet_address
    event_name = params.event_name
    event_description = params.event_description
    collection_name = params.collection_name
    nfts_cnt = params.nfts_cnt
    image_name = params.image_name
    image = params.image
    start_date = params.start_date
    end_date = params.end_date
    password = params.password
    subscriptions = params.subscriptions
    price = params.price
    user_timezone = params.user_timezone
    event_id = params.event_id
    invite = params.invite

    session = Session()

    # Проверка на наличие автора в БД
    try:
        author = author_by_tg_id(telegram_id=telegram_id, session=session)

        # Проверка соответствия сохраненного названия коллекции с полученным,
        # если автор уже есть в базе данных
        if author is not None and author.collection_name != collection_name:

            description = "The saved name of the author's collection does not match the received one"
            logger.error(description)
            return jsonify({"status": return_codes.VALIDATE_ERROR, "description": description}), 400

    except Exception as e:
        description = f"Error when trying to find the author with id {telegram_id} in the database"
        logger.error(f"{description}: {e}")
        return jsonify({"status": return_codes.DB_READING_ERROR, "description": description}), 500

    # Создание записи о новом авторе в БД
    try:
        if author is None:

            collection_meta_path = get_collection_metadata_path(collection_name, telegram_id, True)
            nft_item_content_base_uri = join(os.path.split(collection_meta_path)[0], "")

            # Создание тела коллекции
            collection = client.collection_mint_body(
                collection_content_uri=collection_meta_path,
                nft_item_content_base_uri=nft_item_content_base_uri,
            )

            new_author = Author(
                telegram_id=telegram_id,
                collection_address=collection.address.to_string(),
                collection_name=collection_name,
                is_testnet=app.config["TESTNET"],
            )

            add_database_entries(entries=new_author, session=session)

    except Exception as e:
        description = f"Error when trying to prepare an entry about a new author with id {telegram_id}"
        logger.error(f"{description}: {e}")
        return jsonify({"status": return_codes.DB_WRITING_ERROR, "description": description}), 500

    # Обработка транзакции за данное событие
    if event_id is not None:
        event_id = int(decrypt(str(event_id)))
        event = event_by_id(event_id=event_id, session=session)

        if event is None:
            description = f"The event with id {event_id} was not found"
            logger.error(description)
            return jsonify({"status": return_codes.NOT_FOUND, "description": description}), 404

        transaction = transaction_by_id(transaction_id=event.transaction_id, session=session)

        if transaction is None:
            description = f"The transaction of event with id {event_id} was not found."
            logger.error(description)
            return jsonify({"status": return_codes.NOT_FOUND, "description": description}), 404

        if telegram_id != event.telegram_id:
            description = f"This event does not belong to the user with id {telegram_id}"
            logger.error(description)
            return jsonify({"status": return_codes.VALIDATE_ERROR, "description": description}), 403

        # if transaction.status != 'success':
        #     description = f"Payment for this event was not successful"
        #     logger.error(description)
        #     return jsonify({"status": return_codes.VALIDATE_ERROR, "description": description}), 400

        # Обновление полей события
        event.event_name = event_name
        event.event_description = event_description
        event.image_name = image_name
        event.start_date = start_date
        event.end_date = end_date
        event.password = password
        event.invites = invite
        event.subscriptions = subscriptions
        event.user_timezone = user_timezone

        session.commit()
        new_event = event

    # Создание записи о новой транзакции
    else:
        try:
            new_transaction = Transaction(
                source_address=wallet_address,
                destination_address=LIDUM_WALLET_ADDRESS,
                amount=price,
                is_testnet=app.config["TESTNET"],
            )

            add_database_entries(entries=new_transaction, session=session)

        except Exception as e:
            description = "Error when trying to prepare an entry about a new transaction"
            logger.error(f"{description}: {e}")
            return (
                jsonify({
                    "status": return_codes.DB_WRITING_ERROR,
                    "description": description,
                }),
                500,
            )

        # Создание записи о новом событии в БД
        try:
            new_event = Event(
                telegram_id=telegram_id,
                event_name=event_name,
                transaction_id=new_transaction.id,
                image_name=image_name,
                nfts_cnt=nfts_cnt,
                start_date=start_date,
                end_date=end_date,
                password=password,
                invites=invite,
                subscriptions=subscriptions,
                event_description=event_description,
                user_timezone=user_timezone,
            )

            add_database_entries(entries=new_event, session=session)

        except Exception as e:
            description = "Error when trying to prepare an entry about a new event"
            logger.error(f"{description}: {e}")
            return (
                jsonify({
                    "status": return_codes.DB_WRITING_ERROR,
                    "description": description,
                }),
                500,
            )

    # Загрузка изображения в директорию коллекции
    try:
        image_path = get_nft_image_path(collection_name, telegram_id, image_name)

        image = decode_base64_image(image)
        save_base64_image(image, image_path)

    except Exception as e:
        description = "An error occurred when uploading an image to the server"
        logger.error(f"{description}: {e}")
        return (
            jsonify({
                "status": return_codes.SERVER_WRITING_ERROR,
                "description": description,
            }),
            500,
        )

    # Создание метадаты для новых NFT
    try:
        create_metadata(telegram_id, collection_name, event_description, image_name)

    except Exception as e:
        description = "An error occurred when writing metadata"
        logger.error(f"{description}: {e}")
        return (
            jsonify({
                "status": return_codes.SERVER_WRITING_ERROR,
                "description": description,
            }),
            500,
        )

    # Добавление задачи на минт пустой коллекции
    # TODO: ЗАПУСКАТЬ ПОСЛЕ ОПЛАТЫ
    try:
        if author is None:
            collection_mint.delay(
                telegram_id,
                collection_meta_path,
                nft_item_content_base_uri,
            )

    except Exception as e:
        description = "Error when trying to add a collection to the processing queue"
        logger.error(f"{description}: {e}")
        return jsonify({"status": return_codes.QUEUE_ERROR, "description": description}), 500

    return jsonify({"status": return_codes.SUCCESS, "event_id": encrypt(new_event.id)}), 200


@app.route("/api/send_nft/", methods=["POST"])
async def send_nft():
    """Минтит NFT из события на кошелек приложения, а затем отправляет его
    пользователю."""

    params = SendNFTParams(**request.get_json())

    telegram_id = params.telegram_id
    wallet_address = params.wallet_address
    event_id = params.event_id

    session = Session()

    # Поиск события в базе данных
    try:
        event_id = int(decrypt(event_id))
        event = event_by_id(event_id=event_id, session=session)

        if event is None:
            description = f"Event with id {event_id} was not found"
            logger.error(description)
            return jsonify({"status": return_codes.NOT_FOUND, "description": description}), 404

        author = author_by_tg_id(telegram_id=event.telegram_id, session=session)

        image_name = event.image_name
        minted_nfts = event.minted_nfts
        nfts_cnt = event.nfts_cnt

        collection_address = author.collection_address

    except Exception as e:
        description = "Error when trying to get data from the database"
        logger.error(f"{description}: {e}")
        return jsonify({"status": return_codes.DB_READING_ERROR, "description": description}), 500

    # Поиск событий, в которых участвовал пользователь
    try:
        participated_events = subscriber_participated_events(telegram_id=telegram_id, session=session)

    except Exception as e:
        description = ("An error occurred when searching for a list of events"
                       f"in which a user with id {telegram_id} participated.")
        logger.error(f"{description}: {e}")
        return jsonify({"status": return_codes.DB_WRITING_ERROR, "description": description}), 500

    # Проверки на актуальность события
    try:
        # Проверка на остаток NFT
        if minted_nfts >= nfts_cnt:
            description = "All NFTs from this event have already been received"
            logger.error(description)
            return jsonify({"status": return_codes.EVENT_NFTS_LEFT, "description": description}), 400

        # Проверка на повторное участие пользователя в событии
        if event_id in participated_events:
            description = f"The user with id {telegram_id} has already received the NFT from this event"
            logger.error(description)
            return jsonify({"status": return_codes.REPEAT_USER, "description": description}), 400

    except Exception as e:
        description = f"Error when trying to check the relevance of the event with id {event_id}"
        logger.error(f"{description}: {e}")
        return jsonify({"status": return_codes.SERVER_ERROR, "description": description}), 500

    try:
        nft_mint.delay(
            event.telegram_id,
            wallet_address,
            collection_address,
            to_json_ext(image_name),
        )

    except Exception as e:
        description = "Error when trying to add a nft to the processing queue"
        logger.error(f"{description}: {e}")
        return jsonify({"status": return_codes.QUEUE_ERROR, "description": description}), 500

    # Запись в базу данных
    try:
        event.minted_nfts += 1

        new_participated_event = Subscriber_Event(
            telegram_id=telegram_id,
            wallet_address=wallet_address,
            participated_event=event_id,
        )

        add_database_entries(entries=new_participated_event, session=session)

    except Exception as e:
        description = "Error when trying to write data to the database"
        logger.error(f"{description}: {e}")
        return jsonify({"status": return_codes.DB_WRITING_ERROR, "description": description}), 500

    return jsonify({"status": return_codes.SUCCESS}), 200


@app.teardown_appcontext
def shutdown_session(exception=None):
    Session.remove()


def validate_params(data, required_params: dict):
    """Проверяет наличие и тип необходимых параметров в пришедшем запросе."""

    missing_params = [param for param in required_params.keys() if data.get(param) is None]

    if missing_params:
        description = f"Missing required parameters: {', '.join(missing_params)}"
        logger.error(description)
        return jsonify({"status": "error", "description": description}), 400

    wrong_types_params = [param for param, type in required_params.items() if not isinstance(data.get(param), type)]

    if wrong_types_params:
        description = f"Invalid parameter type: {', '.join(wrong_types_params)}"
        logger.error(description)
        return jsonify({"status": "error", "description": description}), 400

    return None


@app.errorhandler(ValidationError)
def handle_validation_error(error):

    error_details = [{"field": e["loc"][0], "message": e["msg"], "type": e["type"]} for e in error.errors()]

    logger.error(f"Validation error: {error_details}")

    return jsonify({
        "status": return_codes.VALIDATE_ERROR,
        "description": f"Invalid request parameters: {error_details}",
    }), 400


if __name__ == "__main__":
    app.run(port=8001, debug=True)
