import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from celery.exceptions import MaxRetriesExceededError

from . import client, get_app, create_celery
from .utils import tasks_statuses
from .config import MINT_ATTEMPS_CNT, MINT_RETRY_DELAY
from .config import TRANSFER_ATTEMPS_CNT, TRANSFER_RETRY_DELAY
from .config import TRANSACTION_ATTEMPS_CNT
from .config import TRANSACTION_RETRY_DELAY
from .utils.db import tg_user_by_id, author_by_tg_id
from .utils.db import transaction_by_id
from .utils.convert import address_to_friendly
from .utils.ton_client import get_transaction_data

app = get_app()
celery = create_celery(app)

engine = create_engine(
    app.config["SQLALCHEMY_DATABASE_URI"],
    pool_size=20,
    max_overflow=10,
    pool_timeout=30,
    pool_recycle=1800,
)

session_factory = sessionmaker(bind=engine)


@celery.task(queue="transactions_test",
             bind=True,
             max_retries=TRANSACTION_ATTEMPS_CNT,
             default_retry_delay=TRANSACTION_RETRY_DELAY)
def process_transaction(self, transaction_id: int):
    """Запускает фоновую задачу на проверку статуса транзакции. Обновляет статус этой
    транзакции в базе данных после окончания задачи.

    :param transaction_id: Идентификатор транзакции в базе данных
    """

    print(f"Processing transaction {transaction_id}...")
    session = session_factory()

    try:
        transaction = transaction_by_id(transaction_id=transaction_id, session=session)

        if transaction is None:
            print(f"Transaction with id {transaction_id} was not found")
            session.close()
            return

        hash = transaction.hash
        # amount = transaction.amount
        # source_address = transaction.source_address
        # destination_address = transaction.destination_address
        is_testnet = transaction.is_testnet

    except Exception as e:
        print(f"Error when trying to find a transaction {transaction_id}: {e}")
        session.close()
        return

    try:
        print(f"Attempt {self.request.retries} / {TRANSACTION_ATTEMPS_CNT}...")

        transaction.status = tasks_statuses.PENDING
        session.commit()

        transaction_data = get_transaction_data(hash=hash, is_testnet=is_testnet)

        if transaction_data.success:
            transaction.status = tasks_statuses.SUCCESS
            print(f"Transaction with id {transaction_id} was successful!")

        else:
            transaction.status = tasks_statuses.FAILED
            print(f"Transaction with id {transaction_id} was unsuccessful!")

        session.commit()
        return

    except Exception as e:

        try:
            raise self.retry(exc=e)

        except MaxRetriesExceededError:
            print(f"Error when trying to confirm the transaction {transaction_id}: {e}")

            transaction.status = tasks_statuses.CRUSHED
            session.commit()

    finally:
        session.close()


@celery.task(queue="mint_collection_test", bind=True, max_retries=MINT_ATTEMPS_CNT, default_retry_delay=MINT_RETRY_DELAY)
def collection_mint(self, telegram_id: str | int, collection_content_uri: str, nft_item_content_base_uri: str):
    """Запускает фоновую задачу на минт пустой коллекции.

    :param telegram_id: Идентификатор автора события в телеграме
    :param collection_content_uri: URL файла метаданных этой коллекции
    :param nft_item_content_base_uri: URL директории с метаданными всех NFT этой
        коллекции
    """

    print(f"Launching the task of minting the collection with content_uri {collection_content_uri}"
          f"for author with id {telegram_id}...")
    session = session_factory()

    try:
        try:
            author = author_by_tg_id(telegram_id=telegram_id, session=session)

            if author is None:
                print(f"Author with id {telegram_id} was not found")
                return

            username = tg_user_by_id(telegram_id=telegram_id, session=session).username
            author.collection_status = tasks_statuses.PENDING
            session.commit()

        except Exception as e:
            raise (f"Error when trying to find an author with id {telegram_id}: {e}") from e

        collection = client.collection_mint_body(
            collection_content_uri=collection_content_uri,
            nft_item_content_base_uri=nft_item_content_base_uri,
        )

        collection_address = collection.address.to_string(True, True, True)

        print(
            f"Start minting collection with address {collection_address} for the author with id {telegram_id}(@{username})...")

        print(f"Attempt {self.request.retries} / {MINT_ATTEMPS_CNT}...")

        try:
            success = asyncio.run(client.deploy_collection(collection))

            if success:
                author.collection_status = tasks_statuses.MINTED
                session.commit()
                print(f"The collection {collection_address} for author with id {telegram_id}(@{username})"
                      "has been successfully minted!")

            else:
                raise Exception(f"An unsuccessful attempt to mint collection {collection_address}"
                                f"for the author with id {telegram_id}(@{username})")

        except Exception as e:

            # Повторный запуск задачи
            try:
                raise self.retry(exc=e)

            except MaxRetriesExceededError:
                author.collection_status = tasks_statuses.FAILED
                session.commit()
                raise MaxRetriesExceededError(f"The attempt to mint collection {collection_address}"
                                              f"for author with id {telegram_id}(@{username}) was unsuccessful") from e

    except Exception as e:
        print(e)

    finally:
        session.close()


@celery.task(queue="mint_nft_test", bind=True, max_retries=MINT_ATTEMPS_CNT, default_retry_delay=MINT_RETRY_DELAY)
def nft_mint(self, author_telegram_id: str | int, dest_wallet_address: str, collection_address: str, nft_meta: str):
    """Запускает фоновую задачу на минт NFT в указанную коллекцию. При успешном минте
    NFT запускает задачу на передачу NFT на указанный кошелек.

    :param author_telegram_id: Идентификатор автора события в телеграме
    :param dest_wallet_address: Адрес кошелька, на который будет отправлен сминченный
        NFT
    :param collection_address: Адрес коллекции, в которую будет сминчен NFT
    :param nft_meta: URL нового NFT
    """

    dest_wallet_address = address_to_friendly(dest_wallet_address)
    collection_address = address_to_friendly(collection_address)

    print(f"Launching the task of minting the nft into collection {collection_address} to the wallet {dest_wallet_address}...")
    session = session_factory()

    try:

        # Загрузка состояния минта коллекции из БД
        try:
            author = author_by_tg_id(telegram_id=author_telegram_id, session=session)

            if author is None:
                print(f"Author with id {author_telegram_id} was not found")
                return

            collection_status = author.collection_status

        except Exception as e:
            raise (f"Error when trying to find an author with id {author_telegram_id}: {e}") from e

        if collection_status == tasks_statuses.FAILED:
            print(f"The collection with the address {collection_address} has not been minted. Canceling this task...")
            return

        # Откладывание задачи, если коллекция ещё не заминчена
        elif collection_status != tasks_statuses.MINTED:
            raise self.retry(exc=f"Collection {collection_address} is still minting, retrying...")

        # Минт NFT
        print(f"Minting NFT to the collection {collection_address} for wallet {dest_wallet_address}...")
        print(f"Attempt {self.request.retries} / {MINT_ATTEMPS_CNT}...")

        try:
            nft_address = asyncio.run(client.deploy_one_item(
                collection_address=collection_address,
                nft_meta=nft_meta,
            ))

            if nft_address is not None:
                nft_address = address_to_friendly(nft_address)

                print(f"The minting of the NFT {nft_address}"
                      f"to the collection {collection_address}"
                      f"for the wallet {dest_wallet_address} was successful!")

                try:
                    sending_nft.delay(nft_address, dest_wallet_address)

                except Exception as e:
                    raise ("An error occurred when trying to add a task "
                           f"to the queue for sending nft from collection {collection_address}: {e}") from e

            else:
                raise Exception(f"An unsuccessful attempt to mint NFT to the collection {collection_address})")

        except Exception as e:

            try:
                self.retry(exc=e)

            except MaxRetriesExceededError:
                print(f"The attempt to mint NFT to the collection {collection_address} "
                      f"to the wallet {dest_wallet_address} was unsuccessful")

    except Exception as e:
        print(e)

    finally:
        session.close()


@celery.task(queue="transfer_test", bind=True, max_retries=TRANSFER_ATTEMPS_CNT, default_retry_delay=TRANSFER_RETRY_DELAY)
def sending_nft(self, nft_address: str, dest_wallet_address: str):
    """Запускает фоновую задачу на передачу NFT на указанный кошелек.

    :param nft_address: Адрес NFT, который требуется передать
    :param dest_wallet_address: Адрес кошелька, на который будет отправлен сминченный
        NFT
    :param is_testnet: Принадлежит ли коллекция сети TestNet
    """

    nft_address = address_to_friendly(nft_address)
    dest_wallet_address = address_to_friendly(dest_wallet_address)

    print(f"Transfer of the NFT {nft_address} to the wallet {dest_wallet_address}...")

    # Передача NFT пользователю
    print(f"Attempt {self.request.retries} / {TRANSFER_ATTEMPS_CNT}...")

    try:
        success = asyncio.run(client.transfer_nft(
            nft_address=nft_address,
            new_owner_address=dest_wallet_address,
        ))

        if success:
            print(f"The transfer of the NFT {nft_address} to the wallet {dest_wallet_address} was successful!")

        else:
            raise Exception(f"An unsuccessful attempt to transfer NFT {nft_address} to the wallet {dest_wallet_address})")

    except Exception as e:

        try:
            self.retry(exc=e)

        except MaxRetriesExceededError:
            print(f"The attempt to send NFT {nft_address} to the wallet {dest_wallet_address} was unsuccessful")
